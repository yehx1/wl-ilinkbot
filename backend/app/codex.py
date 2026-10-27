from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
import shlex
import re

from .repositories import Repository
from .subprocess_runner import ProcessResult, run_process


class CodexRunner:
    def __init__(self, command: str, cwd: Path, timeout_seconds: int, max_output: int, concurrency: int, repository: Repository, model: str = ""):
        self.command = command
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.max_output = max_output
        self.semaphore = asyncio.Semaphore(concurrency)
        self.repository = repository
        self.model = model
        self.sessions: dict[str, str] = {}

    def reset_session(self, requester: str) -> None:
        self.sessions.pop(requester, None)

    async def run(self, prompt: str, requester: str | None = None, attachments: list[tuple[bytes, str, str | None]] | None = None) -> str:
        """Run Codex with optional user media attachments.

        Images are passed through Codex's native ``--image`` option. Other
        files are materialised in a temporary directory and their paths are
        included in the prompt so Codex can inspect them with its tools.
        """
        return await self._run(prompt, requester, safe=False, attachments=attachments)

    async def run_model(self, prompt: str, requester: str | None = None, images: list[tuple[bytes, str]] | None = None) -> str:
        """Use Codex as a read-only model backend for text and vision tools."""
        attachments = [(content, mime, None) for content, mime in (images or [])]
        return await self._run(prompt, requester, safe=True, attachments=attachments)

    async def _run(self, prompt: str, requester: str | None, safe: bool, attachments: list[tuple[bytes, str, str | None]] | None = None) -> str:
        run_id = self.repository.start_run("codex", "wechat", requester, requester, prompt)
        try:
            async with self.semaphore:
                command_parts = [part.strip('"') for part in shlex.split(self.command, posix=False)]
                executable, configured_args = command_parts[0], command_parts[1:]
                is_codex = Path(executable).stem.lower() in {"codex", "codex.cmd", "codex.exe"}
                # The Codex CLI needs the non-interactive `exec` subcommand when
                # launched from the backend without a terminal.
                if is_codex and "exec" not in configured_args:
                    configured_args = ["exec", *configured_args]
                resume_id = self.sessions.get(requester or "") if (is_codex and not safe) else None
                if safe and not is_codex:
                    self.repository.finish_run(run_id, "failed", error="invalid_codex_command")
                    return "CODEX_COMMAND 必须指向 Codex CLI，才能作为模型提供方使用。"
                if is_codex:
                    with tempfile.TemporaryDirectory(prefix="ilinkbot-codex-") as temp_dir:
                        temp = Path(temp_dir)
                        output_path = temp / "last-message.txt"
                        if resume_id:
                            # Resume accepts the same automation options after
                            # the subcommand. The prompt itself is always read
                            # from stdin so Windows .cmd quoting cannot truncate
                            # multi-line or non-ASCII user messages.
                            args = ["exec", "resume", *configured_args[1:]]
                        else:
                            args = [*configured_args, "--color", "never"]
                            if safe:
                                args[1:1] = ["--ephemeral", "--sandbox", "read-only"]
                        args.extend(["-o", str(output_path)])
                        if self.model:
                            args.extend(["--model", self.model])
                        prompt_parts = [prompt]
                        for index, (content, mime, filename) in enumerate(attachments or []):
                            suffix = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".jpg")
                            safe_name = Path(filename or f"attachment-{index}{suffix}").name
                            attachment_path = temp / f"{index}-{safe_name}"
                            attachment_path.write_bytes(content)
                            if mime.startswith("image/"):
                                args.extend(["--image", str(attachment_path)])
                            else:
                                prompt_parts.append(f"\n用户发送的附件（请读取）：{attachment_path}")
                        run_prompt = "".join(prompt_parts)
                        if resume_id:
                            args.append(resume_id)
                        transcript = await run_process(executable, [*args, "-"], self.cwd, self.timeout_seconds, self.max_output, run_prompt.encode("utf-8"))
                        result = transcript
                        if not safe and transcript.status == "completed" and requester:
                            match = re.search(r"session id:\s*([0-9a-f-]{20,})", transcript.output, re.IGNORECASE)
                            if match:
                                self.sessions[requester] = match.group(1)
                        if transcript.status == "completed" and output_path.is_file():
                            final = output_path.read_text(encoding="utf-8", errors="replace").strip()
                            if final:
                                result = ProcessResult(transcript.status, final, transcript.code, transcript.truncated, transcript.duration_ms)
                else:
                    result = await run_process(executable, [*configured_args, prompt], self.cwd, self.timeout_seconds, self.max_output)
        except asyncio.CancelledError:
            self.repository.finish_run(run_id, "cancelled", error="cancelled")
            raise
        except FileNotFoundError:
            self.repository.finish_run(run_id, "failed", error="codex_command_not_found")
            return f"未找到 Codex 命令：{self.command}。请在 backend/.env 中设置 CODEX_COMMAND 为完整路径。"
        except Exception as exc:
            self.repository.finish_run(run_id, "failed", error=type(exc).__name__)
            raise
        self.repository.finish_run(run_id, result.status, result.output, "", result.code, result.truncated)
        if result.status == "timeout":
            return "Codex 执行超时。\n" + result.output
        if result.status == "failed":
            if result.code is None and "process could not start" in result.output:
                return f"未找到 Codex 命令：{self.command}。请在 backend/.env 中设置 CODEX_COMMAND 为完整路径。"
            return f"Codex 执行失败（退出码 {result.code}）。\n{result.output}"
        return clean_codex_output(result.output) or "Codex 已完成，但没有返回文本。"


def clean_codex_output(value: str) -> str:
    """Remove CLI transcript noise before content is sent to WeChat."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value or "")
    lines = [line.rstrip() for line in text.splitlines()]
    # The CLI may echo the final answer twice: once in the transcript and once
    # after `tokens used`. Prefer the final section.
    markers = [i for i, line in enumerate(lines) if line.strip().lower() == "tokens used"]
    if markers:
        tail = [line for line in lines[markers[-1] + 1:] if line.strip()]
        if tail and re.fullmatch(r"[\d,]+", tail[0].strip()):
            tail = tail[1:]
        if tail:
            lines = tail
    noise = ("reading additional input from stdin", "openai codex v", "--------", "warning:", "tokens used", "session id:", "workdir:", "model:", "provider:", "approval:", "sandbox:", "reasoning effort:", "reasoning summaries:", "user", "codex", "exec")
    # Never remove lines from a fenced Markdown code block. A legitimate code
    # line can begin with words such as ``exec`` or ``user`` and must remain
    # copyable in the WeChat client.
    cleaned: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            cleaned.append(line)
        elif in_fence or not stripped.lower().startswith(noise):
            cleaned.append(line)
    return "\n".join(cleaned).strip()

