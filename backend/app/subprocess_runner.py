from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def decode_output(value: bytes) -> str:
    """Decode command output from UTF-8, with the Windows console fallback."""
    if not value:
        return ""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        # cmd.exe and legacy Windows utilities commonly use the active OEM
        # code page (936 on Simplified Chinese systems).
        if sys.platform == "win32":
            try:
                return value.decode("cp936")
            except UnicodeDecodeError:
                pass
        return value.decode("utf-8", "replace")


@dataclass(frozen=True)
class ProcessResult:
    status: str
    output: str
    code: int | None = None
    truncated: bool = False
    duration_ms: int = 0


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def run_process(
    command: str,
    args: list[str],
    cwd: Path,
    timeout_seconds: float,
    max_output: int,
    stdin_data: bytes | None = None,
) -> ProcessResult:
    started = time.monotonic()
    kwargs: dict = {
        "cwd": str(cwd),
        "stdin": asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "shell": False,
    }
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
    try:
        process = await asyncio.create_subprocess_exec(command, *args, **kwargs)
    except (OSError, ValueError) as exc:
        return ProcessResult("failed", f"process could not start: {type(exc).__name__}", duration_ms=int((time.monotonic() - started) * 1000))
    output = bytearray()
    truncated = False
    try:
        if stdin_data is not None and process.stdin is not None:
            process.stdin.write(stdin_data)
            await process.stdin.drain()
            process.stdin.close()
        async def collect() -> None:
            nonlocal truncated
            assert process.stdout is not None
            while True:
                data = await process.stdout.read(4096)
                if not data:
                    break
                remaining = max(0, max_output - len(output))
                output.extend(data[:remaining])
                if len(data) > remaining:
                    truncated = True
            await process.wait()

        await asyncio.wait_for(collect(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        await _terminate(process)
        return ProcessResult("timeout", decode_output(bytes(output)), process.returncode, truncated, int((time.monotonic() - started) * 1000))
    except asyncio.CancelledError:
        await _terminate(process)
        raise
    text = decode_output(bytes(output))
    return ProcessResult("completed" if process.returncode == 0 else "failed", text, process.returncode, truncated, int((time.monotonic() - started) * 1000))
