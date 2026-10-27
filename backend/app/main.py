from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .api import configure_cors, create_router
from .codex import CodexRunner
from .llm import LLMService
from .mail import MailService
from .config import load_settings
from .control import ControlRunner
from .db import Database
from .repositories import Repository
from .router import MessageRouter
from .scheduler import Scheduler, natural_schedule_values
from .wechat_service import WeChatService


_CODEX_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".csv", ".json", ".docx", ".xlsx", ".zip"}
_CODEX_PATH_RE = re.compile(r"(?:(?:[A-Za-z]:[\\/])|/)[^\r\n`<>\"]+", re.IGNORECASE)
_CODEX_MARKDOWN_PATH_RE = re.compile(r"\]\s*\\?\(([^)\r\n]+)\\?\)")
_CODEX_SEND_MARKER_RE = re.compile(r"\[(?:send_to_wechat|WECHAT_SEND_FILE)\s*[: ]\s*([^\]]+)\]", re.IGNORECASE)


def _codex_artifact_references(value: str, workdir: Path) -> list[Path]:
    """Return existing, supported files explicitly referenced by Codex.

    References may be bare paths, Markdown links, or the explicit send marker.
    Relative paths are resolved below the Codex workdir; absolute paths may
    refer to any local drive or directory.
    """
    # Explicit send markers are handled by MessageRouter so they are not sent
    # twice by this automatic path-reference pass.
    without_markers = _CODEX_SEND_MARKER_RE.sub("", value or "")
    raw_values = [*_CODEX_PATH_RE.findall(without_markers), *_CODEX_MARKDOWN_PATH_RE.findall(without_markers)]
    root = workdir.resolve()
    found: list[Path] = []
    seen: set[str] = set()
    for raw in raw_values:
        candidate_text = raw.strip().strip('"\'').replace("\\\\", "\\")
        candidate_text = candidate_text.rstrip("\\).,;:!?，。；：！？")
        if candidate_text.lower().startswith("file://"):
            candidate_text = candidate_text[7:]
        candidate = Path(candidate_text).expanduser()
        if candidate.suffix.lower() not in _CODEX_FILE_EXTENSIONS:
            continue
        was_absolute = candidate.is_absolute()
        if not was_absolute:
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
            if not was_absolute:
                resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.stat().st_size <= 20 * 1024 * 1024 and str(resolved).casefold() not in seen:
            seen.add(str(resolved).casefold())
            found.append(resolved)
    return found


def create_app(settings=None, bot_factory=None) -> FastAPI:
    config = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(config.database_path)
        repository = Repository(db)
        wechat = WeChatService(config.credentials_path, config.ilink_base_url, repository, config.message_max_chars, bot_factory)
        codex = CodexRunner(config.codex_command, config.codex_workdir, config.codex_timeout_seconds, config.codex_max_output, config.codex_concurrency, repository, config.codex_model)
        control = ControlRunner(config.control_allowlist, config.codex_workdir, repository)
        scheduler = Scheduler(repository, wechat.send_text, config.scheduler_timezone)
        llm = LLMService(config.ai_provider, codex, config.openai_api_key, config.openai_model, config.openai_base_url, config.openai_api_mode)
        mail = MailService(config.smtp_host, config.smtp_port, config.smtp_user, config.smtp_password, config.smtp_from, config.smtp_ssl)
        async def schedule_from_message(kind: str, text: str, user_id: str) -> str:
            try:
                values = natural_schedule_values(kind, text, user_id, config.scheduler_timezone)
            except ValueError as exc:
                return f"创建任务失败：{exc}"
            next_run = scheduler.prepare(values)
            created = repository.create_schedule(values, next_run)
            return f"已创建任务 #{created['id']}：{values['message']}"
        async def codex_with_wechat_files(prompt: str, requester: str | None = None) -> str:
            """Send the user's message unchanged to the native Codex session."""
            result = await codex.run(prompt, requester)
            # Codex often creates an artifact and mentions its path in the answer.
            # Attach local files automatically, while keeping the text response concise.
            if requester:
                artifacts = _codex_artifact_references(result, config.codex_workdir)
                for path in artifacts:
                    await wechat.send_file(requester, path.read_bytes(), path.name)
                if artifacts:
                    result += "\n已将生成的文件发送给你。"
            return result
        codex_with_wechat_files.reset_session = codex.reset_session
        router = MessageRouter(wechat.send_text, codex_with_wechat_files, control.execute, repository, config.message_max_chars, llm.generate, schedule_from_message, mail.send, llm.ocr, wechat.send_file, config.codex_workdir)
        wechat.on_message(router.handle)
        app.state.services = {"config": config, "database": db, "repository": repository, "wechat": wechat, "codex": codex, "llm": llm, "mail": mail, "control": control, "scheduler": scheduler}
        scheduler.start()
        await wechat.start_login()
        try:
            yield
        finally:
            await scheduler.stop()
            await wechat.stop()
            db.close()

    app = FastAPI(title="iLinkBot", lifespan=lifespan)
    configure_cors(app, config)
    app.include_router(create_router())
    return app


def run() -> None:
    import uvicorn
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
