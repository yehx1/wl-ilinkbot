from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import Database, now_iso


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def list_schedules(self) -> list[dict[str, Any]]:
        with self.db.lock:
            return [dict(row) for row in self.db.connection.execute("SELECT * FROM schedules ORDER BY id DESC")]

    def list_user_notes(self) -> dict[str, str]:
        with self.db.lock:
            rows = self.db.connection.execute("SELECT user_id,note FROM user_notes WHERE note<>''").fetchall()
        return {str(row["user_id"]): str(row["note"]) for row in rows}

    def set_user_note(self, user_id: str, note: str) -> str:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO user_notes(user_id,note,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET note=excluded.note,updated_at=excluded.updated_at",
                (user_id, note, now_iso()),
            )
        return note

    def add_todo(self, user_id: str, text: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            cursor = conn.execute("INSERT INTO todos(user_id,text,created_at) VALUES(?,?,?)", (user_id, text, now_iso()))
        row = self.db.connection.execute("SELECT * FROM todos WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_todos(self, user_id: str) -> list[dict[str, Any]]:
        with self.db.lock:
            return [dict(row) for row in self.db.connection.execute("SELECT * FROM todos WHERE user_id=? ORDER BY done,id DESC", (user_id,))]

    def complete_todo(self, user_id: str, todo_id: int) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("UPDATE todos SET done=1,completed_at=? WHERE id=? AND user_id=?", (now_iso(), todo_id, user_id))
        return cursor.rowcount > 0

    def add_generated_tool(self, user_id: str, description: str, definition: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            cursor = conn.execute("INSERT INTO generated_tools(user_id,description,definition,created_at) VALUES(?,?,?,?)", (user_id, description, definition, now_iso()))
        row = self.db.connection.execute("SELECT * FROM generated_tools WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_generated_tools(self, user_id: str) -> list[dict[str, Any]]:
        with self.db.lock:
            return [dict(row) for row in self.db.connection.execute("SELECT * FROM generated_tools WHERE user_id=? ORDER BY id DESC", (user_id,))]

    def get_generated_tool(self, user_id: str, tool_id: int) -> dict[str, Any] | None:
        with self.db.lock:
            row = self.db.connection.execute("SELECT * FROM generated_tools WHERE id=? AND user_id=?", (tool_id, user_id)).fetchone()
        return dict(row) if row else None

    def set_generated_tool_enabled(self, user_id: str, tool_id: int, enabled: bool) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("UPDATE generated_tools SET enabled=? WHERE id=? AND user_id=?", (int(enabled), tool_id, user_id))
        return cursor.rowcount > 0

    def get_schedule(self, schedule_id: int) -> dict[str, Any] | None:
        with self.db.lock:
            return _row(self.db.connection.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone())

    def create_schedule(self, values: dict[str, Any], next_run_at: str | None) -> dict[str, Any]:
        now = now_iso()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO schedules(name,kind,spec,run_at,timezone,target_user_id,message,enabled,next_run_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (values["name"], values["kind"], values.get("spec"), _iso(values.get("run_at")), values["timezone"], values["target_user_id"], values["message"], int(values.get("enabled", True)), next_run_at, now, now),
            )
        return self.get_schedule(int(cursor.lastrowid))  # type: ignore[return-value]

    def update_schedule(self, schedule_id: int, values: dict[str, Any], next_run_at: str | None) -> dict[str, Any] | None:
        current = self.get_schedule(schedule_id)
        if not current:
            return None
        merged = {**current, **values}
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE schedules SET name=?,kind=?,spec=?,run_at=?,timezone=?,target_user_id=?,message=?,enabled=?,next_run_at=?,updated_at=? WHERE id=?""",
                (merged["name"], merged["kind"], merged.get("spec"), _iso(merged.get("run_at")), merged["timezone"], merged["target_user_id"], merged["message"], int(merged["enabled"]), next_run_at, now_iso(), schedule_id),
            )
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: int) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        return cursor.rowcount > 0

    def claim_execution(self, schedule_id: int, scheduled_for: str) -> int | None:
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO schedule_executions(schedule_id,scheduled_for,started_at,status) VALUES(?,?,?,?)",
                    (schedule_id, scheduled_for, now_iso(), "running"),
                )
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def finish_execution(self, execution_id: int, status: str, error: str | None = None) -> None:
        with self.db.lock:
            self.db.connection.execute("UPDATE schedule_executions SET status=?,error=?,finished_at=? WHERE id=?", (status, error[:2000] if error else None, now_iso(), execution_id))

    def reclaim_stale_execution(self, schedule_id: int, scheduled_for: str, stale_before: str) -> int | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM schedule_executions WHERE schedule_id=? AND scheduled_for=? AND status='running' AND started_at<?", (schedule_id, scheduled_for, stale_before)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE schedule_executions SET started_at=?,status='running',finished_at=NULL,error=NULL WHERE id=?", (now_iso(), row[0]))
        return int(row[0])

    def set_schedule_next(self, schedule_id: int, next_run_at: str | None, last_run_at: str | None, enabled: bool | None = None) -> None:
        with self.db.lock:
            if enabled is None:
                self.db.connection.execute("UPDATE schedules SET next_run_at=?,last_run_at=?,updated_at=? WHERE id=?", (next_run_at, last_run_at, now_iso(), schedule_id))
            else:
                self.db.connection.execute("UPDATE schedules SET next_run_at=?,last_run_at=?,enabled=?,updated_at=? WHERE id=?", (next_run_at, last_run_at, int(enabled), now_iso(), schedule_id))

    def list_executions(self, schedule_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.lock:
            rows = self.db.connection.execute("SELECT * FROM schedule_executions WHERE schedule_id=? ORDER BY id DESC LIMIT ?", (schedule_id, limit))
            return [dict(row) for row in rows]

    def start_run(self, kind: str, source: str, requester: str | None, target: str | None, input_summary: str | None) -> int:
        with self.db.lock:
            cursor = self.db.connection.execute("INSERT INTO runs(kind,source,requester_user_id,target_user_id,status,input_summary,started_at) VALUES(?,?,?,?,?,?,?)", (kind, source, requester, target, "running", (input_summary or "")[:2000], now_iso()))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, output: str = "", error: str = "", exit_code: int | None = None, truncated: bool = False) -> None:
        with self.db.lock:
            self.db.connection.execute("UPDATE runs SET status=?,output_summary=?,output_truncated=?,error_summary=?,exit_code=?,finished_at=? WHERE id=?", (status, output[:12000], int(truncated), error[:2000], exit_code, now_iso(), run_id))

    def list_runs(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.db.lock:
            rows = list(self.db.connection.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)))
            total = self.db.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            return [dict(row) for row in rows], int(total)

    def add_message(self, direction: str, user_id: str, text: str, message_id: str | None = None, message_type: str | None = None, created_at: str | datetime | None = None) -> None:
        with self.db.lock:
            self.db.connection.execute("INSERT INTO messages(direction,user_id,message_id,message_type,text_summary,text,created_at) VALUES(?,?,?,?,?,?,?)", (direction, user_id, message_id, message_type, text[:2000], text, _iso(created_at) or now_iso()))

    def list_connection_activity(self) -> list[dict[str, Any]]:
        """Return the latest inbound message for every proactively reachable user."""
        with self.db.lock:
            rows = self.db.connection.execute(
                """SELECT t.user_id,MAX(m.created_at) AS last_inbound_at
                   FROM wechat_context_tokens AS t
                   JOIN messages AS m ON m.user_id=t.user_id AND m.direction='inbound'
                   WHERE length(t.context_token)>0
                   GROUP BY t.user_id"""
            )
            return [dict(row) for row in rows]

    def claim_connection_reminder(self, user_id: str, inbound_at: str, slot_at: str) -> bool:
        """Atomically claim a reminder slot, retaining only one row per user."""
        with self.db.transaction() as conn:
            current = conn.execute(
                "SELECT inbound_at,last_slot_at FROM connection_reminder_state WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if current and current["inbound_at"] == inbound_at and current["last_slot_at"] >= slot_at:
                return False
            conn.execute(
                """INSERT INTO connection_reminder_state(user_id,inbound_at,last_slot_at,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     inbound_at=excluded.inbound_at,
                     last_slot_at=excluded.last_slot_at,
                     updated_at=excluded.updated_at""",
                (user_id, inbound_at, slot_at, now_iso()),
            )
            return True

    def list_conversations(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.db.lock:
            rows = list(
                self.db.connection.execute(
                    """SELECT m.user_id,
                              COUNT(*) AS message_count,
                              latest.created_at AS last_message_at,
                              latest.direction AS last_direction,
                              COALESCE(latest.text, latest.text_summary, '') AS last_text
                       FROM messages AS m
                       JOIN messages AS latest ON latest.id = (
                           SELECT m2.id FROM messages AS m2
                           WHERE m2.user_id=m.user_id
                           ORDER BY m2.id DESC LIMIT 1
                       )
                       GROUP BY m.user_id
                       ORDER BY latest.id DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                )
            )
            total = self.db.connection.execute("SELECT COUNT(DISTINCT user_id) FROM messages").fetchone()[0]
            return [dict(row) for row in rows], int(total)

    def list_messages(self, user_id: str, limit: int = 100, before_id: int | None = None) -> tuple[list[dict[str, Any]], bool, int | None]:
        params: list[Any] = [user_id]
        before_clause = ""
        if before_id is not None:
            before_clause = " AND id<?"
            params.append(before_id)
        params.append(limit + 1)
        with self.db.lock:
            rows = list(
                self.db.connection.execute(
                    f"""SELECT id,direction,user_id,message_id,message_type,
                               COALESCE(text,text_summary,'') AS text,created_at
                        FROM messages
                        WHERE user_id=?{before_clause}
                        ORDER BY id DESC LIMIT ?""",
                    tuple(params),
                )
            )
        has_more = len(rows) > limit
        page = rows[:limit]
        page.reverse()
        items = [dict(row) for row in page]
        next_before_id = int(page[0]["id"]) if has_more and page else None
        return items, has_more, next_before_id

    def delete_messages(self, user_id: str, message_ids: list[int]) -> int:
        """Delete only the selected messages belonging to the requested user."""
        unique_ids = sorted({int(message_id) for message_id in message_ids})
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"DELETE FROM messages WHERE user_id=? AND id IN ({placeholders})",
                (user_id, *unique_ids),
            )
        return int(cursor.rowcount)

    def list_sendable_targets(self, limit: int = 200, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.db.lock:
            rows = list(
                self.db.connection.execute(
                    """SELECT m.user_id,COUNT(*) AS message_count,MAX(m.created_at) AS last_message_at
                       FROM messages AS m
                       JOIN wechat_context_tokens AS t ON t.user_id=m.user_id AND length(t.context_token)>0
                       GROUP BY m.user_id
                       ORDER BY MAX(m.id) DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                )
            )
            total = self.db.connection.execute(
                """SELECT COUNT(*) FROM (
                       SELECT m.user_id FROM messages AS m
                       JOIN wechat_context_tokens AS t ON t.user_id=m.user_id AND length(t.context_token)>0
                       GROUP BY m.user_id
                   )"""
            ).fetchone()[0]
            return [dict(row) for row in rows], int(total)

    def is_sendable_target(self, user_id: str) -> bool:
        with self.db.lock:
            row = self.db.connection.execute(
                """SELECT 1 FROM wechat_context_tokens AS t
                   WHERE t.user_id=? AND length(t.context_token)>0
                     AND EXISTS(SELECT 1 FROM messages AS m WHERE m.user_id=t.user_id)
                   LIMIT 1""",
                (user_id,),
            ).fetchone()
            return row is not None

    def save_context_token(self, user_id: str, token: str) -> None:
        with self.db.lock:
            self.db.connection.execute("INSERT INTO wechat_context_tokens(user_id,context_token,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET context_token=excluded.context_token,updated_at=excluded.updated_at", (user_id, token, now_iso()))

    def context_token(self, user_id: str) -> str | None:
        with self.db.lock:
            row = self.db.connection.execute("SELECT context_token FROM wechat_context_tokens WHERE user_id=?", (user_id,)).fetchone()
            return str(row[0]) if row else None

    def list_logs(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.db.lock:
            rows = list(self.db.connection.execute("SELECT id,level,event,message,created_at FROM logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)))
            total = self.db.connection.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            return [dict(row) for row in rows], int(total)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)
