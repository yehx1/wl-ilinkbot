from __future__ import annotations


def chunk_text(text: str, max_chars: int = 1800) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > max_chars:
        candidates = [remaining.rfind("\n", 0, max_chars + 1), remaining.rfind(" ", 0, max_chars + 1)]
        boundary = max(candidates)
        if boundary < max_chars // 2:
            boundary = max_chars
        part = remaining[:boundary].strip()
        if part:
            chunks.append(part)
        remaining = remaining[boundary:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def chunk_markdown(text: str, max_chars: int = 1800) -> list[str]:
    """Split Markdown without leaving fenced code blocks unterminated.

    iLink delivers text messages and the WeChat client renders Markdown on the
    receiving side. Re-opening a fence in the next part keeps each chunk
    renderable (and therefore keeps its copy-code action available).
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    value = (text or "").strip()
    if len(value) <= max_chars:
        return [value] if value else []

    lines = value.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    language = ""

    def size(lines_: list[str], closing: bool = False) -> int:
        return len("\n".join(lines_)) + (len("\n```") if closing else 0)

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append("\n".join(current).strip())
            current = []

    for line in lines:
        opening = line.strip()
        was_fenced = fence is not None
        if current and size(current + [line], was_fenced) > max_chars:
            if was_fenced:
                # Close this part and continue the code block in the next one.
                current.append("```")
                flush()
                current = [f"```{language}".rstrip()]
            else:
                flush()
        current.append(line)
        if fence is None and opening.startswith("```"):
            language = opening[3:].strip()
            fence = "```"
        elif fence is not None and opening == fence:
            fence = None

    if current:
        if fence is not None and current[-1].strip() != "```":
            current.append("```")
        flush()
    return [chunk for chunk in chunks if chunk]


HELP_TEXT = "可用命令：/help、/list、/translate、/summary、/abstract、/meeting、/todo、/repeat、/remind、/ocr、/convert、/mail、/create-tool、/run-tool confirm 工具ID 输入内容、/tools、/status、/exit、/codex、/control。翻译/总结/摘要/会议纪要命令后直接附加内容；邮件使用 /mail confirm 确认发送。"


async def send_chunked(send, target: str, text: str, max_chars: int = 1800) -> None:
    for part in chunk_text(text, max_chars):
        await send(target, part)


async def send_markdown(send, target: str, text: str, max_chars: int = 1800) -> None:
    for part in chunk_markdown(text, max_chars):
        await send(target, part)

