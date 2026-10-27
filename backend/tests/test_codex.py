from pathlib import Path

import pytest

from app.codex import CodexRunner, clean_codex_output
from app.db import Database
from app.repositories import Repository
from app.subprocess_runner import ProcessResult


@pytest.mark.asyncio
async def test_model_mode_is_ephemeral_read_only_and_accepts_images(tmp_path: Path, monkeypatch):
    captured = {}

    async def fake_run_process(command, args, cwd, timeout, max_output, stdin_data=None):
        captured.update(command=command, args=args, stdin=stdin_data)
        assert Path(args[args.index("--image") + 1]).read_bytes() == b"png"
        Path(args[args.index("-o") + 1]).write_text("final answer", encoding="utf-8")
        return ProcessResult("completed", "cli noise", 0)

    monkeypatch.setattr("app.codex.run_process", fake_run_process)
    repository = Repository(Database(tmp_path / "db.sqlite3"))
    runner = CodexRunner("codex", tmp_path, 10, 1000, 1, repository, "model-x")
    result = await runner.run_model("prompt", "user", [(b"png", "image/png")])
    assert result == "final answer"
    assert captured["args"][:1] == ["exec"]
    assert "--ephemeral" in captured["args"]
    assert captured["args"][captured["args"].index("--sandbox") + 1] == "read-only"
    assert captured["stdin"] == b"prompt"


@pytest.mark.asyncio
async def test_model_mode_rejects_non_codex_command(tmp_path: Path, monkeypatch):
    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("non-Codex command must not run")

    monkeypatch.setattr("app.codex.run_process", must_not_run)
    repository = Repository(Database(tmp_path / "db.sqlite3"))
    runner = CodexRunner("python", tmp_path, 10, 1000, 1, repository)
    result = await runner.run_model("prompt", "user")
    assert "必须指向 Codex CLI" in result


@pytest.mark.asyncio
async def test_agent_mode_uses_stdin_for_new_and_resumed_sessions(tmp_path: Path, monkeypatch):
    calls = []
    session_id = "01a17703-01da-7293-bafa-f153b87d971d"

    async def fake_run_process(command, args, cwd, timeout, max_output, stdin_data=None):
        calls.append((args, stdin_data))
        Path(args[args.index("-o") + 1]).write_text(f"answer-{len(calls)}", encoding="utf-8")
        return ProcessResult("completed", f"session id: {session_id}\ntokens used\n290\nanswer", 0)

    monkeypatch.setattr("app.codex.run_process", fake_run_process)
    repository = Repository(Database(tmp_path / "db.sqlite3"))
    runner = CodexRunner("codex exec --dangerously-bypass-approvals-and-sandbox", tmp_path, 10, 1000, 1, repository)

    first = await runner.run("第一行\n第二行：将企业微信最大化", "user")
    second = await runner.run("继续处理\n不要截断", "user")

    assert first == "answer-1"
    assert second == "answer-2"
    assert calls[0][0][-1] == "-"
    assert calls[0][1] == "第一行\n第二行：将企业微信最大化".encode("utf-8")
    assert calls[1][0][:2] == ["exec", "resume"]
    assert session_id in calls[1][0]
    assert "--dangerously-bypass-approvals-and-sandbox" in calls[1][0]
    assert calls[1][0][-1] == "-"
    assert calls[1][1] == "继续处理\n不要截断".encode("utf-8")


def test_clean_codex_output_removes_token_count():
    output = "tokens used\n12,161\n最终答复"
    assert clean_codex_output(output) == "最终答复"
