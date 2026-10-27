from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .cron_compat import next_cron

from .repositories import Repository


CONNECTION_TTL = timedelta(hours=24)
CONNECTION_REMINDER_LEAD = timedelta(hours=1)
CONNECTION_REMINDER_INTERVAL = timedelta(minutes=10)
CONNECTION_REMINDER_TEXT = "iLink 微信连接预计将在 {expires_at} 左右过期，请回复任意消息以保持连接。若未回复，我会每隔 10 分钟提醒；凌晨 0 点至早上 6 点不会打扰。"


def natural_schedule_values(
    kind: str,
    text: str,
    user_id: str,
    timezone_name: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    if kind == "remind":
        match = re.fullmatch(r"\s*(\d+)\s*(分钟|小时|天)后\s*(.+?)\s*", text)
        if not match:
            raise ValueError("用法：20分钟后提醒我开会")
        amount, unit, message = int(match.group(1)), match.group(2), match.group(3).strip()
        if amount < 1 or amount > 10000:
            raise ValueError("提醒时间范围无效")
        delta = {"分钟": timedelta(minutes=amount), "小时": timedelta(hours=amount), "天": timedelta(days=amount)}[unit]
        return {
            "name": message[:80],
            "kind": "once",
            "run_at": (current + delta).isoformat(),
            "timezone": timezone_name,
            "target_user_id": user_id,
            "message": message,
            "enabled": True,
        }
    if kind == "repeat":
        match = re.fullmatch(r"\s*每天\s*(\d{1,2})点(?:([0-5]?\d)分?)?\s*(.+?)\s*", text)
        if not match:
            raise ValueError("用法：每天9点发送天气")
        hour, minute, message = int(match.group(1)), int(match.group(2) or 0), match.group(3).strip()
        if hour > 23:
            raise ValueError("小时必须在 0 到 23 之间")
        return {
            "name": message[:80],
            "kind": "cron",
            "spec": f"{minute} {hour} * * *",
            "timezone": timezone_name,
            "target_user_id": user_id,
            "message": message,
            "enabled": True,
        }
    raise ValueError("任务类型必须是 remind 或 repeat")


def _parse_iso(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _next_time(schedule: dict, now: datetime) -> str | None:
    if schedule["kind"] == "once":
        run_at = _parse_iso(schedule.get("run_at"))
        if run_at and run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=ZoneInfo(schedule.get("timezone", "UTC")))
        return run_at.astimezone(timezone.utc).isoformat() if run_at else None
    try:
        zone = ZoneInfo(schedule["timezone"])
        local_now = now.astimezone(zone)
        return next_cron(schedule["spec"], local_now).astimezone(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _connection_reminder_slot(last_inbound: datetime, now: datetime, timezone_name: str) -> tuple[datetime, datetime] | None:
    """Return the current reminder slot and expiry, both in UTC."""
    zone = ZoneInfo(timezone_name)
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    current = now.astimezone(timezone.utc)
    expires = last_inbound.astimezone(timezone.utc) + CONNECTION_TTL
    local_expiry = expires.astimezone(zone)

    # If expiry is during the quiet period (including exactly 06:00, whose
    # normal lead window would be entirely quiet), warn from 23:00 to midnight.
    expiry_clock = local_expiry.timetz().replace(tzinfo=None)
    if time(0, 0) <= expiry_clock <= time(6, 0):
        reminder_date: date = local_expiry.date() - timedelta(days=1)
        starts = datetime.combine(reminder_date, time(23, 0), zone).astimezone(timezone.utc)
        ends = datetime.combine(local_expiry.date(), time(0, 0), zone).astimezone(timezone.utc)
    else:
        starts = expires - CONNECTION_REMINDER_LEAD
        ends = expires

    local_current = current.astimezone(zone)
    if current < starts or current >= ends or time(0, 0) <= local_current.time().replace(tzinfo=None) < time(6, 0):
        return None
    intervals = int((current - starts) // CONNECTION_REMINDER_INTERVAL)
    slot = starts + intervals * CONNECTION_REMINDER_INTERVAL
    return slot, expires


def _connection_reminder_message(expires: datetime, timezone_name: str) -> str:
    local_expiry = expires.astimezone(ZoneInfo(timezone_name))
    return CONNECTION_REMINDER_TEXT.format(expires_at=local_expiry.strftime("%m 月 %d 日 %H:%M"))


class Scheduler:
    def __init__(self, repository: Repository, send, timezone_name: str = "Asia/Shanghai", poll_seconds: float = 1.0):
        self.repository = repository
        self.send = send
        self.timezone_name = timezone_name
        self.poll_seconds = poll_seconds
        self.task: asyncio.Task | None = None
        self.stopping = False

    def prepare(self, values: dict) -> str | None:
        now = datetime.now(timezone.utc)
        if values["kind"] == "cron":
            try:
                ZoneInfo(values["timezone"])
                next_cron(values.get("spec", ""), now)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError("无效的 Cron 表达式或时区") from exc
        elif not values.get("run_at"):
            raise ValueError("一次性任务必须提供 run_at")
        return _next_time(values, now)

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.stopping = False
            now = datetime.now(timezone.utc)
            for schedule in self.repository.list_schedules():
                if schedule["enabled"] and not schedule.get("next_run_at"):
                    self.repository.set_schedule_next(schedule["id"], _next_time(schedule, now), schedule.get("last_run_at"))
            self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self.stopping:
            try:
                await self.tick()
            except Exception as exc:
                self.repository.db.log("error", "scheduler", type(exc).__name__)
            await asyncio.sleep(self.poll_seconds)

    async def tick(self, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        await self._tick_connection_reminders(current)
        for schedule in self.repository.list_schedules():
            if not schedule["enabled"] or not schedule.get("next_run_at"):
                continue
            due = _parse_iso(schedule["next_run_at"])
            if due is None or due > current:
                continue
            scheduled_for = due.astimezone(timezone.utc).isoformat()
            execution_id = self.repository.claim_execution(schedule["id"], scheduled_for)
            if execution_id is None:
                stale_before = (current - timedelta(minutes=5)).isoformat()
                execution_id = self.repository.reclaim_stale_execution(schedule["id"], scheduled_for, stale_before)
                if execution_id is None:
                    continue
            try:
                await self.send(schedule["target_user_id"], schedule["message"])
            except Exception as exc:
                self.repository.finish_execution(execution_id, "failed", type(exc).__name__)
                if schedule["kind"] == "once":
                    self.repository.set_schedule_next(schedule["id"], None, scheduled_for, False)
                else:
                    self.repository.set_schedule_next(schedule["id"], _next_time(schedule, current), scheduled_for)
            else:
                self.repository.finish_execution(execution_id, "completed")
                if schedule["kind"] == "once":
                    self.repository.set_schedule_next(schedule["id"], None, scheduled_for, False)
                else:
                    self.repository.set_schedule_next(schedule["id"], _next_time(schedule, current), scheduled_for)

    async def _tick_connection_reminders(self, current: datetime) -> None:
        for activity in self.repository.list_connection_activity():
            inbound_at = str(activity["last_inbound_at"])
            last_inbound = _parse_iso(inbound_at)
            if last_inbound is None:
                continue
            reminder = _connection_reminder_slot(last_inbound, current, self.timezone_name)
            if reminder is None:
                continue
            slot, expires = reminder
            if not self.repository.claim_connection_reminder(activity["user_id"], inbound_at, slot.isoformat()):
                continue
            try:
                await self.send(activity["user_id"], _connection_reminder_message(expires, self.timezone_name))
            except Exception as exc:
                self.repository.db.log("warning", "connection_reminder", type(exc).__name__)

    async def stop(self) -> None:
        self.stopping = True
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
