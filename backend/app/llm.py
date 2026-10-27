from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .codex import CodexRunner


class LLMError(RuntimeError):
    """A provider request failed without exposing credentials or response bodies."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    api_mode: str = "responses"


class LLMService:
    def __init__(
        self,
        provider: str,
        codex: CodexRunner,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_mode: str = "responses",
    ):
        self.codex = codex
        self.configure(provider, api_key, model, base_url, api_mode)

    def configure(self, provider: str, api_key: str, model: str, base_url: str, api_mode: str) -> None:
        provider = provider.lower().strip()
        api_mode = api_mode.lower().strip()
        if provider not in {"codex", "openai"}:
            raise ValueError("模型提供方必须是 codex 或 openai")
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError("API 协议必须是 responses 或 chat_completions")
        self.config = LLMConfig(provider, api_key, model.strip(), base_url.rstrip("/"), api_mode)

    @property
    def provider(self) -> str:
        return self.config.provider

    async def generate(self, prompt: str, requester: str | None = None) -> str:
        if self.config.provider == "codex":
            return await self.codex.run_model(prompt, requester)
        if not self.config.api_key:
            return "尚未配置大模型 API Key，请在模型设置中配置。"
        return await asyncio.to_thread(self._request, prompt, None, None)

    async def ocr(self, image: bytes, mime: str = "image/jpeg", requester: str | None = None) -> str:
        prompt = "请提取图片中的全部可见文字，保持原有顺序和换行；只返回识别到的文字。"
        if self.config.provider == "codex":
            return await self.codex.run_model(prompt, requester, [(image, mime)])
        if not self.config.api_key:
            return "尚未配置大模型 API Key，请在模型设置中配置。"
        return await asyncio.to_thread(self._request, prompt, image, mime)

    def _request(self, prompt: str, image: bytes | None, mime: str | None) -> str:
        if self.config.api_mode == "responses":
            content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
            if image is not None:
                content.append({"type": "input_image", "image_url": _data_url(image, mime)})
            body = {"model": self.config.model, "input": [{"role": "user", "content": content}]}
            endpoint = f"{self.config.base_url}/responses"
        else:
            chat_content: str | list[dict[str, object]] = prompt
            if image is not None:
                chat_content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _data_url(image, mime)}},
                ]
            body = {"model": self.config.model, "messages": [{"role": "user", "content": chat_content}], "temperature": 0.2}
            endpoint = f"{self.config.base_url}/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(f"大模型 API 请求失败：{type(exc).__name__}") from exc
        try:
            return _response_text(data, self.config.api_mode)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("大模型 API 返回内容格式无效") from exc


def _data_url(image: bytes, mime: str | None) -> str:
    media_type = mime if mime and mime.startswith("image/") else "image/jpeg"
    return f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"


def _response_text(data: dict, api_mode: str) -> str:
    if api_mode == "chat_completions":
        value = data["choices"][0]["message"]["content"]
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError("empty content")
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                parts.append(text)
    result = "\n".join(parts).strip()
    if not result:
        raise ValueError("empty output")
    return result
