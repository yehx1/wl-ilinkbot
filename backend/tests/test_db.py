from pathlib import Path

from app.db import Database
from app.repositories import Repository


def test_schema_and_schedule_crud(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    repo = Repository(db)
    created = repo.create_schedule({"name": "test", "kind": "once", "run_at": "2030-01-01T00:00:00+00:00", "timezone": "UTC", "target_user_id": "u1", "message": "hello", "enabled": True}, "2030-01-01T00:00:00+00:00")
    assert created["id"] > 0
    assert repo.get_schedule(created["id"])["name"] == "test"
    assert repo.update_schedule(created["id"], {"message": "updated"}, created["next_run_at"])["message"] == "updated"
    assert repo.delete_schedule(created["id"])
    assert repo.list_schedules() == []
    db.close()


def test_message_history_conversations_and_sendable_targets(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    repo = Repository(db)
    repo.add_message("inbound", "u1", "hello", message_type="text")
    repo.add_message("outbound", "u1", "world", message_type="text")
    repo.add_message("inbound", "u2", "private")
    repo.save_context_token("u1", "secret-token")

    items, has_more, cursor = repo.list_messages("u1", limit=1)
    assert [item["text"] for item in items] == ["world"]
    assert has_more is True
    older, older_has_more, _ = repo.list_messages("u1", limit=10, before_id=cursor)
    assert [item["text"] for item in older] == ["hello"]
    assert older_has_more is False

    conversations, total = repo.list_conversations()
    assert total == 2
    assert {item["user_id"] for item in conversations} == {"u1", "u2"}
    targets, target_total = repo.list_sendable_targets()
    assert target_total == 1
    assert [item["user_id"] for item in targets] == ["u1"]
    assert repo.is_sendable_target("u1") is True
    assert repo.is_sendable_target("u2") is False
    assert "secret-token" not in str(conversations) + str(targets) + str(items)
    assert db.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    db.close()


def test_execution_claim_is_unique_and_context_is_private(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")
    repo = Repository(db)
    schedule = repo.create_schedule({"name": "test", "kind": "once", "run_at": "2030-01-01T00:00:00+00:00", "timezone": "UTC", "target_user_id": "u1", "message": "hello", "enabled": True}, "2030-01-01T00:00:00+00:00")
    assert repo.claim_execution(schedule["id"], "2030-01-01T00:00:00+00:00") is not None
    assert repo.claim_execution(schedule["id"], "2030-01-01T00:00:00+00:00") is None
    repo.save_context_token("u1", "secret-token")
    assert repo.context_token("u1") == "secret-token"
    assert "secret-token" not in repo.list_logs()[0] if repo.list_logs()[0] else True
    db.close()
