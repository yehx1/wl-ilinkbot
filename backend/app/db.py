from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('cron', 'once')),
  spec TEXT,
  run_at TEXT,
  timezone TEXT NOT NULL,
  target_user_id TEXT NOT NULL,
  message TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  next_run_at TEXT,
  last_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_executions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  scheduled_for TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  error TEXT,
  run_id INTEGER,
  UNIQUE(schedule_id, scheduled_for)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  source TEXT,
  requester_user_id TEXT,
  target_user_id TEXT,
  status TEXT NOT NULL,
  exit_code INTEGER,
  input_summary TEXT,
  output_summary TEXT,
  output_truncated INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
  user_id TEXT NOT NULL,
  message_id TEXT,
  message_type TEXT,
  text_summary TEXT,
  text TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wechat_context_tokens (
  user_id TEXT PRIMARY KEY,
  context_token TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL,
  event TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  text TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS generated_tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  description TEXT NOT NULL,
  definition TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_notes (
  user_id TEXT PRIMARY KEY,
  note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connection_reminder_state (
  user_id TEXT PRIMARY KEY,
  inbound_at TEXT NOT NULL,
  last_slot_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC, id DESC);
"""



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        with self.transaction() as conn:
            current = int(conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()[0])
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(messages)")}
            if "text" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN text TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC, id DESC)")
            if current < 3:
                conn.execute("UPDATE schema_version SET version=3")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def log(self, level: str, event: str, message: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO logs(level,event,message,created_at) VALUES(?,?,?,?)",
                (level, event, message[:2000], now_iso()),
            )
