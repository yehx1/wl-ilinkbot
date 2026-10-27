from pathlib import Path

import pytest

from app.config import ConfigError, load_settings
from app.api import _write_env


def test_defaults_are_localhost_and_resolved(tmp_path: Path):
    settings = load_settings({}, tmp_path)
    assert settings.host == "127.0.0.1"
    assert settings.port == 3210
    assert settings.database_path == (tmp_path / "data" / "ilinkbot.sqlite3").resolve()
    assert settings.codex_workdir == tmp_path.resolve()


def test_non_loopback_requires_explicit_opt_in(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_settings({"HOST": "0.0.0.0"}, tmp_path)
    settings = load_settings({"HOST": "0.0.0.0", "ALLOW_UNSAFE_BIND": "true"}, tmp_path)
    assert settings.host == "0.0.0.0"


def test_invalid_port_is_rejected(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_settings({"PORT": "bad"}, tmp_path)


def test_quoted_dotenv_values_are_decoded(tmp_path: Path):
    (tmp_path / ".env").write_text('SMTP_PASSWORD="value with spaces"\n', encoding="utf-8")
    settings = load_settings({}, tmp_path)
    assert settings.smtp_password == "value with spaces"


def test_env_writer_roundtrips_special_secret_values(tmp_path: Path):
    _write_env(tmp_path / ".env", {"SMTP_PASSWORD": 'value # with = and "quotes"'})
    settings = load_settings({}, tmp_path)
    assert settings.smtp_password == 'value # with = and "quotes"'
