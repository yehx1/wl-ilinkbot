from datetime import datetime, timezone
from pathlib import Path
import asyncio

from app.db import Database
from app.repositories import Repository
from app.scheduler import Scheduler, _connection_reminder_slot, natural_schedule_values


def test_once_schedule_claims_and_disables_after_send(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    repo = Repository(db)
    sent: list[tuple[str, str]] = []

    async def send(target: str, message: str):
        sent.append((target, message))

    scheduler = Scheduler(repo, send)
    values = {"name": "once", "kind": "once", "run_at": datetime(2020, 1, 1, tzinfo=timezone.utc), "timezone": "UTC", "target_user_id": "u", "message": "m", "enabled": True}
    schedule = repo.create_schedule(values, scheduler.prepare(values))
    asyncio.run(scheduler.tick(datetime(2020, 1, 2, tzinfo=timezone.utc)))
    asyncio.run(scheduler.tick(datetime(2020, 1, 2, tzinfo=timezone.utc)))
    assert sent == [("u", "m")]
    assert repo.get_schedule(schedule["id"])["enabled"] == 0
    db.close()


def test_natural_schedule_values_are_strict():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reminder = natural_schedule_values("remind", "20分钟后提醒我开会", "u", now=now)
    assert reminder["kind"] == "once"
    assert reminder["message"] == "提醒我开会"
    repeat = natural_schedule_values("repeat", "每天9点30分发送天气", "u", now=now)
    assert repeat["spec"] == "30 9 * * *"
    try:
        natural_schedule_values("repeat", "每天25点发送天气", "u", now=now)
    except ValueError as exc:
        assert "0 到 23" in str(exc)
    else:
        raise AssertionError("invalid hour must be rejected")


def test_connection_reminder_repeats_and_reply_resets_window(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    repo = Repository(db)
    sent: list[tuple[str, str]] = []

    async def send(target: str, message: str):
        sent.append((target, message))

    repo.save_context_token("u", "token")
    repo.add_message("inbound", "u", "hello", created_at="2026-01-01T00:00:00+00:00")
    scheduler = Scheduler(repo, send, "Asia/Shanghai")

    asyncio.run(scheduler.tick(datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)))
    asyncio.run(scheduler.tick(datetime(2026, 1, 1, 23, 0, 30, tzinfo=timezone.utc)))
    asyncio.run(scheduler.tick(datetime(2026, 1, 1, 23, 10, tzinfo=timezone.utc)))
    assert len(sent) == 2
    assert all(target == "u" and "请回复任意消息" in message for target, message in sent)

    repo.add_message("inbound", "u", "still here", created_at="2026-01-01T23:11:00+00:00")
    asyncio.run(scheduler.tick(datetime(2026, 1, 1, 23, 20, tzinfo=timezone.utc)))
    assert len(sent) == 2
    db.close()


def test_connection_reminder_moves_overnight_expiry_to_2300_and_observes_quiet_hours():
    zone = "Asia/Shanghai"
    # Last reply at 03:00 local means expiry at 03:00 the following day.
    last_inbound = datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)
    assert _connection_reminder_slot(last_inbound, datetime(2026, 1, 2, 14, 59, tzinfo=timezone.utc), zone) is None
    at_2300 = _connection_reminder_slot(last_inbound, datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc), zone)
    at_2350 = _connection_reminder_slot(last_inbound, datetime(2026, 1, 2, 15, 59, tzinfo=timezone.utc), zone)
    assert at_2300 and at_2300[0] == datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    assert at_2350 and at_2350[0] == datetime(2026, 1, 2, 15, 50, tzinfo=timezone.utc)
    assert _connection_reminder_slot(last_inbound, datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc), zone) is None


def test_connection_reminder_resumes_at_0600_for_later_expiry():
    zone = "Asia/Shanghai"
    # Expiry is 06:30, so the 05:30-06:00 portion is quiet and 06:00 resumes.
    last_inbound = datetime(2026, 1, 1, 22, 30, tzinfo=timezone.utc)
    assert _connection_reminder_slot(last_inbound, datetime(2026, 1, 2, 21, 50, tzinfo=timezone.utc), zone) is None
    resumed = _connection_reminder_slot(last_inbound, datetime(2026, 1, 2, 22, 0, tzinfo=timezone.utc), zone)
    assert resumed and resumed[0] == datetime(2026, 1, 2, 22, 0, tzinfo=timezone.utc)
