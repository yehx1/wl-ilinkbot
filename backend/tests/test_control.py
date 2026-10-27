from pathlib import Path

from app.control import ControlRunner
from app.db import Database
from app.repositories import Repository
from app.subprocess_runner import decode_output


def test_only_allowlisted_ids_are_exposed(tmp_path: Path):
    allowlist = tmp_path / "allow.json"
    allowlist.write_text('{"safe": {"label": "Safe", "executable": "python", "args": ["-c", "print(1)"]}}', encoding="utf-8")
    runner = ControlRunner(allowlist, tmp_path, Repository(Database(tmp_path / "db.sqlite3")))
    assert runner.list_commands() == [{"id": "safe", "label": "Safe"}]
    assert "未知" in __import__('asyncio').run(runner.execute("arbitrary; whoami"))


def test_windows_console_output_falls_back_to_cp936(monkeypatch):
    monkeypatch.setattr("app.subprocess_runner.sys.platform", "win32")
    assert decode_output("网络配置".encode("cp936")) == "网络配置"
