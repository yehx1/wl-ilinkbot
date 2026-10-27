from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .repositories import Repository
from .messaging import chunk_text


class WeChatNotConnectedError(RuntimeError):
    pass


class TargetNotSendableError(RuntimeError):
    pass


@dataclass
class WeChatStatus:
    state: str = "disconnected"
    qr_url: str | None = None
    error: str | None = None
    updated_at: str | None = None


class WeChatService:
    def __init__(self, credentials_path: Path, base_url: str, repository: Repository, max_chars: int = 1800, bot_factory=None):
        self.credentials_path = credentials_path
        self.base_url = base_url
        self.repository = repository
        self.max_chars = max_chars
        self.bot_factory = bot_factory
        self.status = WeChatStatus()
        self.bot: Any = None
        self.poll_task: asyncio.Task | None = None
        self.login_task: asyncio.Task | None = None
        self.handler: Callable[[Any], Awaitable[None]] | None = None
        self.reconnect_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self._stopping = False

    def on_message(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self.handler = handler

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.status.state = state
        self.status.error = error[:500] if error else None
        self.status.updated_at = datetime.now(timezone.utc).isoformat()

    def public_status(self) -> dict[str, Any]:
        return {"state": self.status.state, "qr_url": self.status.qr_url, "error": self.status.error, "updated_at": self.status.updated_at}

    def _on_qr(self, value: str) -> None:
        """Expose the SDK QR payload in a browser-displayable form."""
        try:
            self.status.qr_url = _qr_image_src(value)
        except ValueError as exc:
            self.status.qr_url = None
            self._set_state("error", str(exc))
            self.repository.db.log("error", "wechat_qr", str(exc))
            return
        self._set_state("qr_ready")

    def _on_scanned(self, *_args) -> None:
        self._set_state("scanned")

    def _on_expired(self, *_args) -> None:
        self.status.qr_url = None
        self._set_state("expired")

    def _on_error(self, error: Exception, *_args) -> None:
        self._set_state("error", type(error).__name__)
        self.repository.db.log("error", "wechat", type(error).__name__)

    async def _dispatch(self, message: Any) -> None:
        downloader = getattr(self.bot, "download", None)
        if downloader and (getattr(message, "images", None) or getattr(message, "files", None)):
            try:
                message._downloaded_media = await downloader(message)
            except Exception as exc:
                self.repository.db.log("warning", "wechat_media_download", type(exc).__name__)
        if self.handler:
            await self.handler(message)

    async def _make_bot(self):
        factory = self.bot_factory
        if factory is None:
            from wechatbot import WeChatBot
            factory = WeChatBot
        kwargs = {"cred_path": str(self.credentials_path), "base_url": self.base_url, "on_qr_url": self._on_qr, "on_scanned": self._on_scanned, "on_expired": self._on_expired, "on_error": self._on_error}
        try:
            bot = factory(**kwargs)
        except TypeError:
            kwargs.pop("base_url", None)
            bot = factory(**kwargs)
        return bot

    async def _watch_poll(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if not self._stopping:
                self._set_state("error", type(exc).__name__)
                self.repository.db.log("error", "wechat_poll", type(exc).__name__)
                await self.start_login()
        else:
            if not self._stopping:
                self._set_state("disconnected")
                await self.start_login()

    async def _watch_login(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        if not self._stopping and self.status.state != "connected":
            await asyncio.sleep(5)
            if not self._stopping and self.status.state != "connected":
                await self.start_login()

    async def start_login(self, *, force: bool = False) -> None:
        if self.login_task is None or self.login_task.done():
            self._stopping = False
            self.login_task = asyncio.create_task(self.login(force=force))
            self.login_task.add_done_callback(lambda task: asyncio.create_task(self._watch_login(task)))

    async def login(self, *, force: bool = False) -> None:
        async with self.reconnect_lock:
            if self._stopping:
                return
            self._set_state("logging_in")
            try:
                self.bot = await self._make_bot()
                register = getattr(self.bot, "on_message", None)
                if register:
                    result = register(self._dispatch)
                    if inspect.isawaitable(result):
                        await result
                bot_login = self.bot.login
                parameters = inspect.signature(bot_login).parameters
                supports_force = "force" in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
                if force and supports_force:
                    await bot_login(force=True)
                else:
                    await bot_login()
                self.status.qr_url = None
                self._set_state("connected")
                if self.poll_task is None or self.poll_task.done():
                    self.poll_task = asyncio.create_task(self.bot.start())
                    self.poll_task.add_done_callback(lambda task: asyncio.create_task(self._watch_poll(task)))
            except Exception as exc:
                self._set_state("error", type(exc).__name__)
                self.repository.db.log("error", "wechat_login", type(exc).__name__)

    async def reconnect(self) -> None:
        await self.stop()
        self._stopping = False
        self.status.qr_url = None
        # An explicit reconnect must bypass stored credentials so the SDK emits
        # a fresh QR code instead of silently restoring the previous session.
        await self.start_login(force=True)
        # Let the login task publish at least its initial state before the API
        # response is built. QR generation itself remains asynchronous.
        await asyncio.sleep(0)

    async def send_text(self, target: str, text: str) -> int:
        if not self.bot or self.status.state != "connected":
            raise WeChatNotConnectedError("微信尚未连接")
        token = self.repository.context_token(target)
        if not token:
            raise TargetNotSendableError("目标用户尚未向机器人发送过消息，无法主动发送")
        parts = chunk_text(text, self.max_chars)
        async with self.send_lock:
            for part in parts:
                synthetic = _SyntheticMessage(target, token)
                await self.bot.reply(synthetic, part)
                self.repository.add_message("outbound", target, part, message_type="text")
        return len(parts)

    async def send_file(self, target: str, data: bytes, filename: str, caption: str | None = None) -> None:
        if not self.bot or self.status.state != "connected":
            raise WeChatNotConnectedError("微信尚未连接")
        if not self.repository.context_token(target):
            raise TargetNotSendableError("目标用户尚未向机器人发送过消息，无法主动发送")
        if not data or len(data) > 20 * 1024 * 1024:
            raise ValueError("附件不能为空且不能超过 20 MB")
        content = {"file": data, "file_name": filename}
        if caption:
            content["caption"] = caption
        await self.bot.send_media(target, content)
        record = f"{caption}\n[文件：{filename}]" if caption else f"[文件：{filename}]"
        self.repository.add_message("outbound", target, record, message_type="file")

    async def stop(self) -> None:
        self._stopping = True
        if self.login_task and self.login_task is not asyncio.current_task():
            self.login_task.cancel()
            try:
                await self.login_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self.login_task = None
        if self.bot is not None:
            stop = getattr(self.bot, "stop", None)
            if stop:
                result = stop()
                if inspect.isawaitable(result):
                    try:
                        await asyncio.wait_for(result, timeout=3)
                    except asyncio.TimeoutError:
                        pass
        if self.poll_task and self.poll_task is not asyncio.current_task():
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self.poll_task = None
        self._set_state("disconnected")


def _qr_image_src(value: str) -> str:
    """Convert an SDK QR payload to a browser image source."""
    if not isinstance(value, str):
        raise ValueError("二维码数据格式无效")
    payload = value.strip()
    if not payload:
        raise ValueError("二维码数据为空")
    if payload.startswith("data:image/"):
        return payload
    if payload.startswith(("https://", "http://")):
        return _remote_qr_image_src(payload)

    if payload.startswith("{") and payload.endswith("}"):
        try:
            document = json.loads(payload)
        except json.JSONDecodeError:
            document = None
        if isinstance(document, dict):
            for key in ("url", "qrcode_img_content", "qrcode"):
                nested = document.get(key)
                if isinstance(nested, str):
                    return _qr_image_src(nested)

    compact = "".join(payload.split())
    candidates = [payload]
    try:
        # Accept both padded and unpadded standard/url-safe Base64 returned by
        # different iLink gateway versions.
        normalized = compact.replace("-", "+").replace("_", "/")
        normalized += "=" * (-len(normalized) % 4)
        raw = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError):
        raw = None

    if raw:
        if raw.startswith((b"http://", b"https://")):
            try:
                return _remote_qr_image_src(raw.decode("utf-8"))
            except UnicodeDecodeError:
                pass
        mime = _image_mime(raw)
        if mime:
            encoded = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        try:
            candidates.insert(0, raw.decode("utf-8"))
        except UnicodeDecodeError:
            pass

    # Some SDK versions provide the QR text rather than an image. Render it
    # locally so the browser does not need a third-party QR service.
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return _render_qr(candidate)
        except (ValueError, TypeError):
            continue
    raise ValueError("二维码数据不是有效的图片或二维码内容")


def _remote_qr_image_src(value: str) -> str:
    """Render a QR payload URL locally instead of loading it as an image."""
    return _render_qr(value)


def _render_qr(value: str) -> str:
    try:
        import segno
    except ImportError as exc:
        raise ValueError("二维码组件未安装，请重新安装后端依赖") from exc
    try:
        qr = segno.make(value, error="m")
        buffer = io.BytesIO()
        qr.save(buffer, kind="png", scale=6, border=4, dark="#000000", light="#ffffff")
    except Exception as exc:
        raise ValueError("生成二维码图片失败") from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _image_mime(value: bytes) -> str | None:
    if value.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if value.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if value.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if value.startswith(b"RIFF") and value[8:12] == b"WEBP":
        return "image/webp"
    return None


class _SyntheticMessage:
    def __init__(self, user_id: str, token: str):
        self.user_id = user_id
        self.text = ""
        self.type = "text"
        self.timestamp = datetime.now(timezone.utc)
        self.raw = {}
        self._context_token = token
        self.context_token = token
