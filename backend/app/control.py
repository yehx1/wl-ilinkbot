from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .repositories import Repository
from .subprocess_runner import run_process


class ControlError(ValueError):
    pass


class ControlRunner:
    def __init__(self, allowlist_path: Path, default_cwd: Path, repository: Repository):
        self.repository = repository
        self.default_cwd = default_cwd
        raw = json.loads(allowlist_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not raw:
            raise ControlError("control allowlist must be a non-empty object")
        self.commands: dict[str, dict[str, Any]] = {}
        for command_id, item in raw.items():
            if not isinstance(command_id, str) or not command_id or not isinstance(item, dict):
                raise ControlError("invalid control allowlist entry")
            executable = item.get("executable")
            args = item.get("args", [])
            if not isinstance(executable, str) or not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ControlError(f"invalid allowlist command: {command_id}")
            if not Path(executable).is_absolute() and not shutil.which(executable):
                raise ControlError(f"allowlist executable not found: {executable}")
            timeout = float(item.get("timeout_seconds", 10))
            if not 0 < timeout <= 3600 or timeout != timeout:
                raise ControlError(f"invalid timeout for command: {command_id}")
            self.commands[command_id] = {**item, "timeout_seconds": timeout}

    def list_commands(self) -> list[dict[str, str]]:
        return [{"id": key, "label": str(value.get("label", key))} for key, value in self.commands.items()]

    async def execute(self, command_id: str, requester: str | None = None) -> str:
        item = self.commands.get(command_id)
        if item is None:
            return "拒绝执行：未知的白名单命令。"
        cwd = Path(str(item.get("cwd", ".")))
        if not cwd.is_absolute():
            cwd = (self.default_cwd / cwd).resolve()
        if not cwd.is_dir():
            return "控制命令配置无效：工作目录不存在。"
        timeout = float(item.get("timeout_seconds", 10))
        run_id = self.repository.start_run("control", "wechat", requester, None, command_id)
        result = await run_process(str(item["executable"]), list(item.get("args", [])), cwd, timeout, 12000)
        self.repository.finish_run(run_id, result.status, result.output, "", result.code, result.truncated)
        if result.status == "completed":
            return result.output or "命令已完成。"
        return f"控制命令执行失败（{result.status}）。"
