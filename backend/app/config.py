from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        raw_value = value.strip()
        if raw_value.startswith('"') and raw_value.endswith('"'):
            try:
                raw_value = json.loads(raw_value)
            except json.JSONDecodeError:
                raw_value = raw_value[1:-1]
        elif raw_value.startswith("'") and raw_value.endswith("'"):
            raw_value = raw_value[1:-1]
        values[key.strip()] = raw_value
    return values


def _int(values: dict[str, str], key: str, default: int, minimum: int = 1) -> int:
    try:
        result = int(values.get(key, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if result < minimum:
        raise ConfigError(f"{key} must be at least {minimum}")
    return result


def _bool(values: dict[str, str], key: str, default: bool = False) -> bool:
    value = values.get(key, str(default)).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be true or false")


def _path(value: str, base: Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    database_path: Path
    credentials_path: Path
    ilink_base_url: str
    ai_provider: str
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    openai_api_mode: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_ssl: bool
    codex_command: str
    codex_model: str
    codex_workdir: Path
    codex_timeout_seconds: int
    codex_max_output: int
    codex_concurrency: int
    message_max_chars: int
    scheduler_timezone: str
    control_allowlist: Path
    frontend_origins: tuple[str, ...]
    allow_unsafe_bind: bool

    def validate(self) -> None:
        if not self.allow_unsafe_bind and self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("HOST must be loopback unless ALLOW_UNSAFE_BIND=true")
        if not self.ilink_base_url.startswith("https://"):
            raise ConfigError("ILINK_BASE_URL must use HTTPS")
        if not self.codex_workdir.is_dir():
            raise ConfigError(f"CODEX_WORKDIR does not exist: {self.codex_workdir}")
        if self.ai_provider not in {"codex", "openai"}:
            raise ConfigError("AI_PROVIDER must be codex or openai")
        if self.ai_provider == "openai" and not self.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        if self.openai_api_mode not in {"responses", "chat_completions"}:
            raise ConfigError("OPENAI_API_MODE must be responses or chat_completions")
        if not self.openai_base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ConfigError("OPENAI_BASE_URL must use HTTPS (localhost HTTP is allowed)")


def load_settings(env: dict[str, str] | None = None, base_dir: Path | None = None) -> Settings:
    root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
    values = _read_dotenv(root / ".env")
    values.update({key: value for key, value in (env or os.environ).items() if value is not None})
    data_dir = _path(values.get("DATA_DIR", "./data"), root)
    allowlist_value = values.get("CONTROL_ALLOWLIST", "./config/control_allowlist.json")
    allowlist = _path(allowlist_value, root)
    if not allowlist.is_file():
        packaged = Path(__file__).with_name("control_allowlist.json")
        if packaged.is_file():
            allowlist = packaged
    settings = Settings(
        host=values.get("HOST", "127.0.0.1"),
        port=_int(values, "PORT", 3210),
        data_dir=data_dir,
        database_path=data_dir / "ilinkbot.sqlite3",
        credentials_path=_path(values.get("WECHAT_CREDENTIALS", "./data/wechat/credentials.json"), root),
        ilink_base_url=values.get("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com"),
        ai_provider=values.get("AI_PROVIDER", "codex").lower(),
        openai_api_key=values.get("OPENAI_API_KEY", ""),
        openai_model=values.get("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=values.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_api_mode=values.get("OPENAI_API_MODE", "responses").lower(),
        smtp_host=values.get("SMTP_HOST", ""),
        smtp_port=_int(values, "SMTP_PORT", 587),
        smtp_user=values.get("SMTP_USER", ""),
        smtp_password=values.get("SMTP_PASSWORD", ""),
        smtp_from=values.get("SMTP_FROM", values.get("SMTP_USER", "")),
        smtp_ssl=_bool(values, "SMTP_SSL"),
        codex_command=values.get("CODEX_COMMAND", "codex"),
        codex_model=values.get("CODEX_MODEL", ""),
        codex_workdir=_path(values.get("CODEX_WORKDIR", "."), root),
        codex_timeout_seconds=_int(values, "CODEX_TIMEOUT_SECONDS", 120),
        codex_max_output=_int(values, "CODEX_MAX_OUTPUT", 12000),
        codex_concurrency=_int(values, "CODEX_CONCURRENCY", 1),
        message_max_chars=_int(values, "MESSAGE_MAX_CHARS", 1800),
        scheduler_timezone=values.get("SCHEDULER_TIMEZONE", "Asia/Shanghai"),
        control_allowlist=allowlist,
        frontend_origins=tuple(x.strip() for x in values.get("FRONTEND_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if x.strip()),
        allow_unsafe_bind=_bool(values, "ALLOW_UNSAFE_BIND"),
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.credentials_path.parent.mkdir(parents=True, exist_ok=True)
    settings.validate()
    return settings
