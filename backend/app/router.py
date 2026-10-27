from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .messaging import HELP_TEXT, send_chunked, send_markdown
from .conversion import convert, ConversionError
from .generated_tools import GENERATOR_PROMPT, ToolDefinitionError, parse_tool_definition, serialize_tool_definition, tool_prompt


_CODEX_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".csv", ".json", ".docx", ".xlsx", ".zip"}


TOOL_PROMPTS = {
    "translate": "请把下面内容翻译成用户要求的目标语言；未指定时，中英文互译。只返回译文：",
    "summary": "请总结下面内容，列出关键要点：",
    "abstract": "请为下面内容写一段简洁摘要：",
    "meeting": "请将下面内容整理为会议纪要，包含议题、结论、待办、负责人和截止时间（未知项标注未指定）：",
}
AUTO_ROUTER_PROMPT = """你是微信工具路由器。根据用户消息选择最合适的工具，只返回 JSON，不要 Markdown：
可选工具：translate（翻译）、summary（总结）、abstract（摘要）、meeting（会议纪要）、todo（待办）、remind（提醒）、repeat（重复任务）、mail（邮件草稿）、create-tool（生成工具）、skill（组合多个工具）、unknown。
格式：{"tool":"工具名","input":"去掉意图后的原始内容"}。如果只是闲聊、问候或无法判断，tool 必须是 unknown。不要执行外部副作用。用户消息：
"""

# Short aliases are intentionally stable so they are easy to type on mobile.
COMMAND_ALIASES = {
    "/tr": "translate", "/trans": "translate", "/翻译": "translate",
    "/sum": "summary", "/总结": "summary", "/abs": "abstract", "/摘要": "abstract",
    "/mtg": "meeting", "/纪要": "meeting", "/td": "todo", "/待办": "todo",
    "/rm": "remind", "/提醒": "remind", "/rp": "repeat", "/重复": "repeat",
    "/scan": "ocr", "/识图": "ocr", "/cv": "convert", "/转换": "convert",
    "/em": "mail", "/邮件": "mail", "/gen": "create-tool", "/工具": "create-tool",
    "/run": "run-tool", "/编排": "skill", "/skill": "skill",
}


@dataclass(frozen=True)
class ParsedCommand:
    kind: str
    input: str


@dataclass
class PendingMail:
    recipient: str
    subject: str
    body: str
    attachment: tuple[bytes, str] | None = None


def parse_command(text: str) -> ParsedCommand:
    value = text.strip()
    for alias, kind in COMMAND_ALIASES.items():
        if value == alias or value.startswith(alias + " "):
            return ParsedCommand(kind, value[len(alias):].strip())
    if not value or value == "/help":
        return ParsedCommand("help", "")
    if value.startswith("/codex "):
        return ParsedCommand("codex", value[7:].strip())
    if value == "/codex":
        return ParsedCommand("codex", "")
    if value == "/say":
        return ParsedCommand("codex", "")
    if value.startswith("/say "):
        return ParsedCommand("codex", value[5:].strip())
    if value == "/desktop" or value.startswith("/desktop "):
        return ParsedCommand("codex", value)
    if value.startswith("/control "):
        return ParsedCommand("control", value[9:].strip())
    if value == "/control":
        return ParsedCommand("control", "")
    if value == "/list":
        return ParsedCommand("list", "")
    if value == "/exit":
        return ParsedCommand("exit", "")
    if value == "/tools":
        return ParsedCommand("tools", "")
    if value == "/status":
        return ParsedCommand("status", "")
    if value in {"/n", "/new", "/new-session"}:
        return ParsedCommand("new-session", "")
    if value in {"/sessions", "/ss"} or value.startswith("/sessions ") or value.startswith("/ss "):
        prefix = "/sessions" if value.startswith("/sessions") else "/ss"
        return ParsedCommand("sessions", value[len(prefix):].strip())
    if value == "/session" or value.startswith("/session "):
        return ParsedCommand("session", value[8:].strip())
    for prefix in ("/translate", "/summary", "/abstract", "/meeting", "/todo", "/repeat", "/remind", "/ocr", "/convert", "/mail", "/create-tool", "/run-tool", "/skill"):
        if value == prefix or value.startswith(prefix + " "):
            return ParsedCommand(prefix[1:], value[len(prefix):].strip())
    return ParsedCommand("unknown", value)


def parse_auto_route(value: str) -> ParsedCommand | None:
    import json
    start = value.find("{")
    if start < 0:
        return None
    try:
        item = json.JSONDecoder().raw_decode(value[start:])[0]
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict) or item.get("tool") not in {"translate", "summary", "abstract", "meeting", "todo", "remind", "repeat", "mail", "create-tool", "skill", "unknown"}:
        return None
    return ParsedCommand(str(item["tool"]), str(item.get("input", "")).strip())


class MessageRouter:
    def __init__(self, send, codex, control, repository, max_chars: int = 1800, llm=None, schedule=None, mail=None, ocr=None, send_file=None, codex_workdir: Path | None = None):
        self.send = send
        self.codex = codex
        self.control = control
        self.repository = repository
        self.max_chars = max_chars
        self.llm = llm or codex
        self.schedule = schedule
        self.mail = mail
        self.ocr = ocr
        self.send_file = send_file
        self.codex_workdir = Path(codex_workdir).resolve() if codex_workdir else None
        self.modes: dict[str, str] = {}
        self.pending_mail: dict[str, PendingMail] = {}
        self.sessions: dict[str, list[list[str]]] = {}
        self.active_session: dict[str, int] = {}
        self.session_titles: dict[str, list[str]] = {}

    async def handle(self, message) -> None:
        user_id = str(getattr(message, "user_id", getattr(message, "from_user_id", "")))
        text = str(getattr(message, "text", "") or "")
        downloaded = getattr(message, "_downloaded_media", None)
        media_type = _media_value(downloaded, "type")
        media_data = _media_value(downloaded, "data")
        image = getattr(message, "image", None) or getattr(message, "image_bytes", None) or (media_data if media_type == "image" else None)
        context = getattr(message, "context_token", None) or getattr(message, "_context_token", None)
        self.repository.add_message("inbound", user_id, text, getattr(message, "message_id", None), getattr(message, "type", None))
        if context:
            self.repository.save_context_token(user_id, str(context))
        command = parse_command(text)
        if downloaded and media_type == "file" and text.lower().startswith("/convert") and self.send_file:
            target = text.split(maxsplit=1)[1].strip().lstrip('.') if len(text.split(maxsplit=1)) > 1 else ''
            try:
                data, filename = convert(media_data, _media_value(downloaded, "file_name") or "file.bin", target)
                await self.send_file(user_id, data, filename)
                await self._send_and_record(user_id, f"文件已转换为 {filename}。")
            except (ConversionError, ValueError) as exc:
                await self._send_and_record(user_id, f"转换失败：{exc}")
            return
        # In Codex mode, media is an attachment to the model. Explicit OCR
        # still wins so that /ocr remains predictable.
        if downloaded and (user_id in self.modes and self.modes[user_id] == "codex" or command.kind == "codex") and command.kind in {"unknown", "codex"}:
            mime = str(getattr(message, "mime_type", None) or _media_value(downloaded, "mime_type") or ("image/jpeg" if media_type == "image" else "application/octet-stream"))
            filename = _media_value(downloaded, "file_name") or ("attachment.jpg" if media_type == "image" else "attachment.bin")
            if self.send_file and _is_send_media_request(text):
                await self.send_file(user_id, media_data, filename, "Codex 模式附件")
                await self._send_and_record(user_id, f"已将 {filename} 发送到微信。")
                return
            attachments = [(media_data, mime, filename)]
            prompt = command.input or "请分析用户发送的附件。"
            result = await self.codex(prompt, user_id, attachments)
            await self._send_codex_result(user_id, result)
            return
        if isinstance(image, bytes) and self.ocr is not None and command.kind in {"ocr", "unknown"}:
            mime = str(getattr(message, "mime_type", None) or _media_value(downloaded, "mime_type") or "image/jpeg")
            result = await self.ocr(image, mime, user_id)
            await send_chunked(self._send_and_record, user_id, result, self.max_chars)
            return
        if command.kind == "unknown" and user_id in self.modes:
            command = ParsedCommand(self.modes[user_id], text.strip())
        elif command.kind == "unknown" and self.llm is not None and text.strip():
            # Automatic intent routing is only used when the user did not choose a mode.
            try:
                routed = parse_auto_route(await self.llm(AUTO_ROUTER_PROMPT + text.strip(), user_id))
                if routed is not None:
                    command = routed
            except Exception as exc:
                self.repository.db.log("warning", "auto_route", type(exc).__name__)
        try:
            if command.kind == "help":
                result = HELP_TEXT
            elif command.kind == "new-session":
                sessions = self.sessions.setdefault(user_id, [[]])
                titles = self.session_titles.setdefault(user_id, ["新会话"])
                sessions.append([])
                titles.append("新会话")
                self.active_session[user_id] = len(sessions) - 1
                reset = getattr(self.codex, "reset_session", None)
                if callable(reset):
                    reset(user_id)
                result = f"已新建 Codex 会话 #{len(sessions)}。"
            elif command.kind == "sessions":
                sessions = self.sessions.setdefault(user_id, [[]])
                page = 0
                try: page = max(0, int(command.input or 0))
                except ValueError: pass
                start = page * 5
                titles = self.session_titles.setdefault(user_id, ["新会话"] * len(sessions))
                result = "\n".join(f"{'*' if index == self.active_session.get(user_id, 0) else ' '} #{index + 1} {titles[index] or '未命名会话'}（{len(item) // 2} 轮）" for index, item in list(enumerate(sessions))[start:start + 5]) or "没有更多会话。"
            elif command.kind == "session":
                try:
                    index = int(command.input) - 1
                    sessions = self.sessions.setdefault(user_id, [[]])
                    self.session_titles.setdefault(user_id, ["新会话"] * len(sessions))
                    if index < 0 or index >= len(sessions): raise ValueError
                    self.active_session[user_id] = index
                    result = f"已切换到 Codex 会话 #{index + 1}。"
                except ValueError:
                    result = "用法：/session 会话编号"
            elif command.kind == "list":
                result = "可用命令：/translate、/summary、/abstract、/meeting、/todo、/remind、/repeat、/ocr、/convert、/mail、/create-tool、/codex、/control、/exit。"
            elif command.kind == "tools":
                generated = self.repository.list_generated_tools(user_id)
                labels = []
                for item in generated:
                    try:
                        name = parse_tool_definition(item["definition"])["name"]
                    except ToolDefinitionError:
                        name = "旧版工具"
                    labels.append(f"#{item['id']} {name}（{'已启用' if item['enabled'] else '待审核'}）")
                custom = "\n已生成工具：" + "、".join(labels) if labels else ""
                result = "内置工具：翻译、总结、摘要、会议纪要、待办、提醒、重复任务、OCR、格式转换、邮件、Codex、电脑控制。使用 /create-tool 可让模型生成新的工具定义。" + custom
            elif command.kind == "status":
                result = "当前工具服务正常。模型和 SMTP 配置状态请在网页的模型设置页面查看。"
            elif command.kind == "exit":
                self.modes.pop(user_id, None)
                result = "已退出当前模式。"
            elif command.kind == "skill":
                if not command.input:
                    result = "用法：/skill 把输入先翻译再总结，或：/编排 先摘要再整理成会议纪要"
                else:
                    result = await self.llm("请作为工具编排 Skill，按用户描述智能组合已有工具（翻译、总结、摘要、会议纪要、待办、提醒、重复任务、OCR、格式转换、邮件）。逐步处理输入，只返回最终结果；不要执行系统命令或外部副作用。\n\n" + command.input, user_id)
            elif command.kind == "create-tool":
                if not command.input:
                    result = "用法：/create-tool 用自然语言描述你想要的工具"
                else:
                    raw = await self.llm(GENERATOR_PROMPT + command.input, user_id)
                    try:
                        definition = parse_tool_definition(raw)
                    except ToolDefinitionError as exc:
                        result = f"工具生成失败：{exc}"
                    else:
                        saved = serialize_tool_definition(definition)
                        item = self.repository.add_generated_tool(user_id, command.input, saved)
                        result = f"工具定义已保存为 #{item['id']}，当前未启用，请先在网页中审核启用。\n\n{saved}"
            elif command.kind == "run-tool":
                parts = command.input.split(maxsplit=2)
                if len(parts) < 2 or parts[0].lower() != "confirm" or not parts[1].isdigit():
                    result = "用法：/run-tool confirm 工具ID 输入内容"
                else:
                    tool = self.repository.get_generated_tool(user_id, int(parts[1]))
                    if not tool or not tool["enabled"]:
                        result = "工具不存在或尚未启用。"
                    elif len(parts) < 3 or not parts[2].strip():
                        try:
                            hint = parse_tool_definition(tool["definition"])["input_hint"]
                        except ToolDefinitionError:
                            hint = "请输入工具所需内容"
                        result = f"请提供输入：{hint}"
                    else:
                        try:
                            prompt = tool_prompt(tool["definition"], parts[2].strip())
                        except ToolDefinitionError as exc:
                            result = f"工具定义无效：{exc}"
                        else:
                            result = await self.llm(prompt, user_id)
            elif command.kind == "mail" and command.input.lower() == "confirm":
                pending = self.pending_mail.pop(user_id, None)
                result = "没有待发送的邮件。" if pending is None else await self.mail(pending.recipient, pending.subject, pending.body, pending.attachment)
            elif command.kind == "mail" and command.input.lower() == "cancel":
                self.pending_mail.pop(user_id, None)
                result = "已取消待发送邮件。"
            elif command.kind == "mail" and self.mail and "|" in command.input:
                recipient, subject, body = [part.strip() for part in command.input.split("|", 2)]
                attachment = (media_data, _media_value(downloaded, "file_name") or "attachment.bin") if downloaded and media_type == "file" else None
                if not recipient or not subject or not body:
                    result = "用法：/mail 收件人 | 主题 | 正文"
                else:
                    self.pending_mail[user_id] = PendingMail(recipient, subject, body, attachment)
                    suffix = f"\n附件：{attachment[1]}" if attachment else ""
                    result = f"请确认邮件：\n收件人：{recipient}\n主题：{subject}\n正文：{body}{suffix}\n\n发送 /mail confirm 确认发送，或 /mail cancel 取消。"
            elif command.kind in {"translate", "summary", "abstract", "meeting", "todo", "repeat", "remind", "ocr", "convert", "mail"}:
                prompts = {**TOOL_PROMPTS, "todo": "请将下面内容整理成待办事项，列出任务、负责人（如有）和截止时间（如有）：", "repeat": "请解析下面的重复任务描述，给出清晰的执行规则和需要确认的信息：", "remind": "请将下面内容整理成提醒事项，提取时间和提醒内容；如果时间不明确请指出：", "ocr": "请提示用户发送图片进行文字提取：", "convert": "请分析下面的格式转换需求，说明源格式、目标格式和所需文件：", "mail": "请将下面内容整理成邮件草稿，包含主题、收件人、正文，并在发送前要求用户确认："}
                if command.kind == "todo" and command.input.lower() in {"list", "列表"}:
                    todos = self.repository.list_todos(user_id)
                    result = "暂无待办事项。" if not todos else "\n".join(f"[{item['id']}] {'已完成' if item['done'] else '待办'}：{item['text']}" for item in todos)
                elif command.kind == "todo" and command.input.lower().startswith("done "):
                    try:
                        result = "待办已完成。" if self.repository.complete_todo(user_id, int(command.input.split(maxsplit=1)[1])) else "找不到对应待办。"
                    except ValueError:
                        result = "用法：/todo done 待办ID"
                elif command.kind == "todo" and command.input:
                    item = self.repository.add_todo(user_id, command.input)
                    result = f"已添加待办 #{item['id']}：{item['text']}"
                elif command.kind in {"remind", "repeat"} and self.schedule and command.input:
                    result = await self.schedule(command.kind, command.input, user_id)
                elif not command.input:
                    result = f"用法：/{command.kind} 内容"
                else:
                    result = await self.llm(f"{prompts[command.kind]}\n\n{command.input}", user_id)
            elif command.kind == "codex":
                if not command.input:
                    self.modes[user_id] = "codex"
                    self.sessions.setdefault(user_id, [[]])
                    self.active_session.setdefault(user_id, 0)
                    result = "已进入 Codex 模式，之后直接发送内容即可执行。发送 /exit 退出。"
                else:
                    sessions = self.sessions.setdefault(user_id, [[]])
                    index = self.active_session.setdefault(user_id, 0)
                    # Native Codex resume owns the conversation history. Only
                    # send the current user message here to avoid nested context
                    # wrappers being interpreted as a separate user request.
                    prompt = command.input
                    result = await self.codex(prompt, user_id)
                    sessions[index].extend([("user", command.input), ("assistant", result)])
                    titles = self.session_titles.setdefault(user_id, ["新会话"] * len(sessions))
                    if titles[index] == "新会话":
                        titles[index] = command.input[:32].replace("\n", " ")
            elif command.kind == "control":
                if not command.input:
                    self.modes[user_id] = "control"
                    result = "已进入控制模式，之后直接发送命令 ID 即可执行。发送 /exit 退出。"
                else:
                    result = await self.control(command.input, user_id)
            else:
                result = "未知命令，请发送 /help 查看帮助。"

        except Exception as exc:
            result = f"执行失败：{type(exc).__name__}"
            self.repository.db.log("error", "message_command", f"{type(exc).__name__}: {str(exc)[:500]}")
        if command.kind == "codex":
            await self._send_codex_result(user_id, result)
        else:
            await send_chunked(self._send_and_record, user_id, result, self.max_chars)

    async def _send_and_record(self, target: str, text: str) -> None:
        await self.send(target, text)

    async def _send_codex_result(self, target: str, result: str) -> None:
        """Send Codex's optional file markers back through the WeChat media API.

        Codex can request a transfer by printing ``[send_to_wechat:path]``.
        Relative marker paths use the configured Codex workdir. Absolute marker
        paths may refer to any local drive or directory; the marker is removed
        from the user-visible reply.
        """
        pattern = re.compile(r"\[(?:send_to_wechat|WECHAT_SEND_FILE)\s*[: ]\s*([^\]]+)\]", re.IGNORECASE)
        paths = pattern.findall(result or "")
        visible = pattern.sub("", result or "").strip()
        if self.send_file:
            for raw_path in paths:
                candidate = Path(raw_path.strip().strip('"')).expanduser()
                was_absolute = candidate.is_absolute()
                if not was_absolute:
                    if not self.codex_workdir:
                        visible += f"\n附件未发送（相对路径缺少 Codex 工作目录）：{raw_path.strip()}"
                        continue
                    candidate = self.codex_workdir / candidate
                try:
                    resolved = candidate.resolve()
                    if not was_absolute:
                        resolved.relative_to(self.codex_workdir)
                    if resolved.suffix.lower() not in _CODEX_FILE_EXTENSIONS:
                        visible += f"\n附件未发送（文件类型不受支持）：{raw_path.strip()}"
                    elif resolved.is_file() and resolved.stat().st_size <= 20 * 1024 * 1024:
                        await self.send_file(target, resolved.read_bytes(), resolved.name)
                    else:
                        visible += f"\n附件未发送（文件不存在或超过 20 MB）：{raw_path.strip()}"
                except (OSError, ValueError):
                    visible += f"\n附件未发送（路径无效或无法访问）：{raw_path.strip()}"
        elif paths:
            visible += "\nCodex 请求发送附件，但当前未配置附件发送能力。"
        await send_markdown(self._send_and_record, target, visible or "Codex 已完成。", self.max_chars)


def _media_value(media, name: str):
    if isinstance(media, dict):
        return media.get(name)
    return getattr(media, name, None)


def _is_send_media_request(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in {"/send", "/send-file", "/send-image"} or value.startswith("/send ")
