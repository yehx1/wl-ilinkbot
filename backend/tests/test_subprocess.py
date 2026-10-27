import asyncio
from pathlib import Path

from app.subprocess_runner import run_process


def test_process_uses_argv_and_limits_output(tmp_path: Path):
    result = asyncio.run(run_process("python", ["-c", "print('x' * 100)"], tmp_path, 5, 20))
    assert result.status == "completed"
    assert len(result.output) <= 20
    assert result.truncated


def test_process_timeout(tmp_path: Path):
    result = asyncio.run(run_process("python", ["-c", "import time; time.sleep(2)"], tmp_path, 0.05, 100))
    assert result.status == "timeout"
