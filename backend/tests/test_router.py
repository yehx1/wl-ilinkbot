from app.router import parse_command
from app.messaging import chunk_text, chunk_markdown


def test_explicit_commands_only():
    assert parse_command("").kind == "help"
    assert parse_command("/codex hello").input == "hello"
    assert parse_command("/control status").input == "status"
    assert parse_command("/codex").input == ""
    assert parse_command("plain text").kind == "unknown"


def test_chunking_prefers_boundaries_and_handles_unicode():
    assert chunk_text("abc def ghi", 5) == ["abc", "def", "ghi"]
    assert chunk_text("你好世界", 2) == ["你好", "世界"]
    assert chunk_text("  ") == []


def test_markdown_chunking_keeps_fenced_code_blocks_renderable():
    parts = chunk_markdown("说明\n\n```python\nexec('hello')\nuser = 1\n```\n\n结尾", 24)
    assert len(parts) > 1
    assert all(part.count("```") == 2 for part in parts if "```" in part)
    assert "exec('hello')" in "\n".join(parts)
