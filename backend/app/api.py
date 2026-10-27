from __future__ import annotations

import base64
import binascii
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .conversion import ConversionError, convert
from .generated_tools import GENERATOR_PROMPT, ToolDefinitionError, parse_tool_definition, serialize_tool_definition, tool_prompt
from .models import (
    ConversationSummary,
    DeleteMessagesRequest,
    MessageHistory,
    MessageTarget,
    Page,
    ScheduleCreate,
    SchedulePatch,
    SendMessageRequest,
    SendMessageResponse,
)
from .scheduler import natural_schedule_values
from .router import TOOL_PROMPTS
from .wechat_service import TargetNotSendableError, WeChatNotConnectedError
from .desktop import DesktopError, focus_window, screenshot


def _service(request: Request) -> dict[str, Any]:
    return request.app.state.services


def _schedule_values(model: ScheduleCreate | SchedulePatch, current: dict | None = None) -> dict:
    values = model.model_dump(exclude_unset=True) if current is None else {**current, **model.model_dump(exclude_unset=True)}
    if current is None:
        values.setdefault("kind", "cron")
        values.setdefault("timezone", "Asia/Shanghai")
    return values


def _bool_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("布尔值必须是 true 或 false")


def _write_env(path: Path, updates: dict[str, str]) -> None:
    current = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in current:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            output.append(f"{key}={json.dumps(updates[key], ensure_ascii=False)}")
            seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in updates.items() if key not in seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(output) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求内容必须是 JSON 对象")
    return payload


def _required_text(payload: dict[str, Any], key: str, maximum: int = 100_000) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"{key} 不能为空")
    if len(value) > maximum:
        raise HTTPException(status_code=422, detail=f"{key} 不能超过 {maximum} 个字符")
    return value


def _decode_file(payload: dict[str, Any], maximum: int = 20 * 1024 * 1024) -> bytes:
    maximum_mb = maximum // (1024 * 1024)
    encoded = str(payload.get("data_base64", ""))
    if not encoded:
        raise HTTPException(status_code=422, detail="缺少文件内容")
    if len(encoded) > ((maximum + 2) // 3) * 4 + 16:
        raise HTTPException(status_code=413, detail=f"文件不能超过 {maximum_mb} MB")
    try:
        result = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="文件内容不是有效 Base64") from exc
    if len(result) > maximum:
        raise HTTPException(status_code=413, detail=f"文件不能超过 {maximum_mb} MB")
    return result


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health(request: Request):
        return {"ok": True}

    @router.get("/status")
    async def status(request: Request):
        services = _service(request)
        config = services["config"]
        return {
            "wechat": services["wechat"].public_status(),
            "scheduler": {"running": services["scheduler"].task is not None},
            "ai": {"provider": config.ai_provider, "model": config.openai_model if config.ai_provider == "openai" else (config.codex_model or "Codex 默认模型"), "openai_model": config.openai_model, "openai_base_url": config.openai_base_url, "openai_api_mode": config.openai_api_mode, "codex_model": config.codex_model, "api_key_configured": bool(config.openai_api_key)},
            "smtp": {"configured": bool(config.smtp_host and config.smtp_user and config.smtp_password and config.smtp_from), "host": config.smtp_host, "port": config.smtp_port, "user": config.smtp_user, "from": config.smtp_from, "ssl": config.smtp_ssl, "password_configured": bool(config.smtp_password)},
        }

    @router.patch("/settings/providers")
    async def update_provider_settings(request: Request):
        payload = await request.json()
        allowed = {"AI_PROVIDER", "OPENAI_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_API_MODE", "CODEX_MODEL", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_SSL"}
        secret_keys = {"OPENAI_API_KEY", "SMTP_PASSWORD"}
        updates = {str(key): str(value) for key, value in payload.items() if key in allowed and value is not None and not (key in secret_keys and str(value) == "")}
        if not updates:
            raise HTTPException(status_code=422, detail="没有可更新的配置")
        if any("\n" in value or "\r" in value or len(value) > 4096 for value in updates.values()):
            raise HTTPException(status_code=422, detail="配置值无效")
        if "AI_PROVIDER" in updates and updates["AI_PROVIDER"] not in {"codex", "openai"}:
            raise HTTPException(status_code=422, detail="AI_PROVIDER must be codex or openai")
        if "OPENAI_API_MODE" in updates and updates["OPENAI_API_MODE"] not in {"responses", "chat_completions"}:
            raise HTTPException(status_code=422, detail="OPENAI_API_MODE must be responses or chat_completions")
        if "SMTP_PORT" in updates:
            try:
                if not 1 <= int(updates["SMTP_PORT"]) <= 65535:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="SMTP_PORT must be between 1 and 65535") from exc
        services = _service(request)
        config = services["config"]
        try:
            changes = {
                "ai_provider": updates.get("AI_PROVIDER", config.ai_provider),
                "openai_model": updates.get("OPENAI_MODEL", config.openai_model),
                "openai_base_url": updates.get("OPENAI_BASE_URL", config.openai_base_url),
                "openai_api_key": updates.get("OPENAI_API_KEY", config.openai_api_key),
                "openai_api_mode": updates.get("OPENAI_API_MODE", config.openai_api_mode),
                "codex_model": updates.get("CODEX_MODEL", config.codex_model),
                "smtp_host": updates.get("SMTP_HOST", config.smtp_host),
                "smtp_port": int(updates.get("SMTP_PORT", config.smtp_port)),
                "smtp_user": updates.get("SMTP_USER", config.smtp_user),
                "smtp_password": updates.get("SMTP_PASSWORD", config.smtp_password),
                "smtp_from": updates.get("SMTP_FROM", config.smtp_from),
                "smtp_ssl": _bool_value(updates.get("SMTP_SSL", str(config.smtp_ssl))),
            }
            next_config = replace(config, **changes)
            next_config.validate()
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _write_env(Path(__file__).resolve().parents[1] / ".env", updates)
        services["llm"].configure(next_config.ai_provider, next_config.openai_api_key, next_config.openai_model, next_config.openai_base_url, next_config.openai_api_mode)
        services["codex"].model = next_config.codex_model
        services["mail"].configure(next_config.smtp_host, next_config.smtp_port, next_config.smtp_user, next_config.smtp_password, next_config.smtp_from, next_config.smtp_ssl)
        services["config"] = next_config
        return {"ok": True, "updated": sorted(updates), "restart_required": False}

    @router.get("/wechat/qr")
    async def qr(request: Request):
        return {"qr_url": _service(request)["wechat"].public_status().get("qr_url")}

    @router.post("/wechat/reconnect")
    async def reconnect(request: Request):
        await _service(request)["wechat"].reconnect()
        return await status(request)

    @router.get("/schedules")
    async def schedules(request: Request):
        return _service(request)["repository"].list_schedules()

    @router.post("/schedules", status_code=201)
    async def create_schedule(payload: ScheduleCreate, request: Request):
        services = _service(request)
        values = _schedule_values(payload)
        try:
            next_run = services["scheduler"].prepare(values)
            return services["repository"].create_schedule(values, next_run)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.patch("/schedules/{schedule_id}")
    async def update_schedule(schedule_id: int, payload: SchedulePatch, request: Request):
        services = _service(request)
        current = services["repository"].get_schedule(schedule_id)
        if not current:
            raise HTTPException(status_code=404, detail="schedule not found")
        values = _schedule_values(payload, current)
        try:
            next_run = services["scheduler"].prepare(values) if values.get("enabled") else None
            result = services["repository"].update_schedule(schedule_id, values, next_run)
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: int, request: Request):
        if not _service(request)["repository"].delete_schedule(schedule_id):
            raise HTTPException(status_code=404, detail="schedule not found")
        return {"ok": True}

    @router.get("/schedules/{schedule_id}/executions")
    async def executions(schedule_id: int, request: Request):
        return _service(request)["repository"].list_executions(schedule_id)

    @router.get("/messages", response_model=MessageHistory)
    async def messages(request: Request, user_id: str = Query(min_length=1, max_length=200), limit: int = Query(100, ge=1, le=200), before_id: int | None = Query(None, ge=1)):
        items, has_more, next_before_id = _service(request)["repository"].list_messages(user_id, limit, before_id)
        return MessageHistory(items=items, has_more=has_more, next_before_id=next_before_id)

    @router.delete("/messages")
    async def delete_messages(payload: DeleteMessagesRequest, request: Request):
        deleted = _service(request)["repository"].delete_messages(payload.user_id, payload.message_ids)
        return {"ok": True, "deleted": deleted}

    @router.post("/messages", response_model=SendMessageResponse)
    async def send_message(payload: SendMessageRequest, request: Request):
        services = _service(request)
        if not services["repository"].is_sendable_target(payload.target_user_id):
            raise HTTPException(status_code=422, detail="该用户尚无可用的微信会话上下文")
        try:
            sent_parts = await services["wechat"].send_text(payload.target_user_id, payload.text)
        except TargetNotSendableError as exc:
            raise HTTPException(status_code=422, detail="该用户尚无可用的微信会话上下文") from exc
        except WeChatNotConnectedError as exc:
            raise HTTPException(status_code=503, detail="微信当前未连接") from exc
        except Exception as exc:
            services["database"].log("error", "web_message_send", type(exc).__name__)
            raise HTTPException(status_code=502, detail="微信消息发送失败") from exc
        return SendMessageResponse(target_user_id=payload.target_user_id, sent_parts=sent_parts)

    @router.post("/messages/media", response_model=SendMessageResponse)
    async def send_message_media(request: Request):
        payload = _json_payload(await request.json())
        target = _required_text(payload, "target_user_id", 200)
        filename = Path(_required_text(payload, "filename", 255)).name
        caption = str(payload.get("caption", "")).strip()
        if len(caption) > 10_000:
            raise HTTPException(status_code=422, detail="caption 不能超过 10000 个字符")
        data = _decode_file(payload)
        services = _service(request)
        if not services["repository"].is_sendable_target(target):
            raise HTTPException(status_code=422, detail="该用户尚无可用的微信会话上下文")
        try:
            await services["wechat"].send_file(target, data, filename, caption or None)
        except TargetNotSendableError as exc:
            raise HTTPException(status_code=422, detail="该用户尚无可用的微信会话上下文") from exc
        except WeChatNotConnectedError as exc:
            raise HTTPException(status_code=503, detail="微信当前未连接") from exc
        except Exception as exc:
            services["database"].log("error", "web_media_send", type(exc).__name__)
            raise HTTPException(status_code=502, detail="微信附件发送失败") from exc
        return SendMessageResponse(target_user_id=target, sent_parts=1)

    @router.get("/conversations", response_model=Page)
    async def conversations(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        items, total = _service(request)["repository"].list_conversations(limit, offset)
        validated = [ConversationSummary.model_validate(item).model_dump() for item in items]
        return Page(items=validated, total=total, limit=limit, offset=offset)

    @router.get("/message-targets", response_model=Page)
    async def message_targets(request: Request, limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)):
        items, total = _service(request)["repository"].list_sendable_targets(limit, offset)
        validated = [MessageTarget.model_validate(item).model_dump() for item in items]
        return Page(items=validated, total=total, limit=limit, offset=offset)

    @router.get("/user-notes")
    async def user_notes(request: Request):
        return {"items": _service(request)["repository"].list_user_notes()}

    @router.patch("/user-notes/{user_id}")
    async def update_user_note(user_id: str, request: Request):
        payload = await request.json()
        note = str(payload.get("note", "")).strip()
        if len(user_id) > 200 or len(note) > 120:
            raise HTTPException(status_code=422, detail="备注最多 120 个字符")
        _service(request)["repository"].set_user_note(user_id, note)
        return {"ok": True, "user_id": user_id, "note": note}

    @router.get("/tools/generated")
    async def generated_tools(request: Request, user_id: str = Query(min_length=1, max_length=200)):
        return {"items": _service(request)["repository"].list_generated_tools(user_id)}

    @router.patch("/tools/generated/{tool_id}")
    async def update_generated_tool(tool_id: int, request: Request, user_id: str = Query(min_length=1, max_length=200), enabled: bool = Query(...)):
        if not _service(request)["repository"].set_generated_tool_enabled(user_id, tool_id, enabled):
            raise HTTPException(status_code=404, detail="generated tool not found")
        return {"ok": True, "id": tool_id, "enabled": enabled}

    @router.post("/tools/generated", status_code=201)
    async def create_generated_tool(request: Request):
        payload = _json_payload(await request.json())
        user_id = _required_text(payload, "user_id", 200)
        description = _required_text(payload, "description", 2000)
        try:
            raw = await _service(request)["llm"].generate(GENERATOR_PROMPT + description, user_id)
            definition = serialize_tool_definition(parse_tool_definition(raw))
        except ToolDefinitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            _service(request)["database"].log("error", "web_tool_create", type(exc).__name__)
            raise HTTPException(status_code=502, detail="自动工具生成失败，请查看运行日志") from exc
        return _service(request)["repository"].add_generated_tool(user_id, description, definition)

    @router.post("/tools/generated/{tool_id}/run")
    async def run_generated_tool(tool_id: int, request: Request):
        payload = _json_payload(await request.json())
        user_id = _required_text(payload, "user_id", 200)
        value = _required_text(payload, "input")
        item = _service(request)["repository"].get_generated_tool(user_id, tool_id)
        if not item:
            raise HTTPException(status_code=404, detail="自动工具不存在")
        if not item["enabled"]:
            raise HTTPException(status_code=409, detail="自动工具尚未审核启用")
        try:
            prompt = tool_prompt(item["definition"], value)
            output = await _service(request)["llm"].generate(prompt, user_id)
        except ToolDefinitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            _service(request)["database"].log("error", "web_tool_run", type(exc).__name__)
            raise HTTPException(status_code=502, detail="自动工具执行失败，请查看运行日志") from exc
        return {"output": output}

    @router.get("/tools/todos")
    async def list_todos(request: Request, user_id: str = Query(min_length=1, max_length=200)):
        return {"items": _service(request)["repository"].list_todos(user_id)}

    @router.post("/tools/todos", status_code=201)
    async def create_todo(request: Request):
        payload = _json_payload(await request.json())
        user_id = _required_text(payload, "user_id", 200)
        value = _required_text(payload, "text", 5000)
        return _service(request)["repository"].add_todo(user_id, value)

    @router.post("/tools/todos/{todo_id}/complete")
    async def complete_todo(todo_id: int, request: Request):
        payload = _json_payload(await request.json())
        user_id = _required_text(payload, "user_id", 200)
        if not _service(request)["repository"].complete_todo(user_id, todo_id):
            raise HTTPException(status_code=404, detail="待办不存在")
        return {"ok": True}

    @router.get("/runs", response_model=Page)
    async def runs(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        items, total = _service(request)["repository"].list_runs(limit, offset)
        return Page(items=items, total=total, limit=limit, offset=offset)

    @router.get("/logs", response_model=Page)
    async def logs(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        items, total = _service(request)["repository"].list_logs(limit, offset)
        return Page(items=items, total=total, limit=limit, offset=offset)

    @router.post("/tools/codex/test")
    async def test_codex(payload: SendMessageRequest, request: Request):
        try:
            output = await _service(request)["codex"].run(payload.text, "web")
        except Exception as exc:
            _service(request)["database"].log("error", "web_codex_test", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Codex 测试失败，请查看运行日志") from exc
        return {"output": output}

    @router.post("/tools/ai/run")
    async def run_ai_tool(request: Request):
        payload = await request.json()
        tool = str(payload.get("tool", ""))
        value = str(payload.get("input", "")).strip()
        if tool not in TOOL_PROMPTS:
            raise HTTPException(status_code=422, detail="未知 AI 工具")
        if not value or len(value) > 100_000:
            raise HTTPException(status_code=422, detail="输入不能为空且不能超过 100000 字符")
        try:
            output = await _service(request)["llm"].generate(f"{TOOL_PROMPTS[tool]}\n\n{value}", "web")
        except Exception as exc:
            _service(request)["database"].log("error", "web_ai_tool", type(exc).__name__)
            raise HTTPException(status_code=502, detail="AI 工具执行失败，请查看运行日志") from exc
        return {"output": output}

    @router.post("/tools/skill/run")
    async def run_tool_skill(request: Request):
        payload = _json_payload(await request.json())
        value = _required_text(payload, "input", 100_000)
        catalog = "翻译、总结、摘要、会议纪要、待办、提醒、重复任务、OCR、格式转换、邮件、已审核的自定义工具"
        prompt = ("你是 iLinkBot 工具编排 Skill。根据用户目标，智能选择并串联已有工具：" + catalog +
                  "。可以先处理文本再把结果交给下一个工具，但不要执行系统命令、发送邮件或创建定时任务等外部副作用；这些操作只输出建议。只返回最终结果，必要时简述使用了哪些工具。\n\n用户目标：" + value)
        try:
            output = await _service(request)["llm"].generate(prompt, "web")
        except Exception as exc:
            _service(request)["database"].log("error", "web_tool_skill", type(exc).__name__)
            raise HTTPException(status_code=502, detail="工具编排执行失败，请检查运行日志") from exc
        return {"output": output}

    @router.post("/tools/ocr")
    async def run_ocr(request: Request):
        payload = _json_payload(await request.json())
        image = _decode_file(payload, 10 * 1024 * 1024)
        mime = str(payload.get("mime", "image/jpeg"))
        if not mime.startswith("image/"):
            raise HTTPException(status_code=422, detail="OCR 只支持图片")
        try:
            output = await _service(request)["llm"].ocr(image, mime, "web")
        except Exception as exc:
            _service(request)["database"].log("error", "web_ocr", type(exc).__name__)
            raise HTTPException(status_code=502, detail="OCR 执行失败，请查看运行日志") from exc
        return {"output": output}

    @router.post("/tools/convert")
    async def convert_file(request: Request):
        payload = _json_payload(await request.json())
        data = _decode_file(payload)
        filename = Path(_required_text(payload, "filename", 255)).name
        target = _required_text(payload, "target", 20)
        try:
            output, output_name = convert(data, filename, target)
        except (ConversionError, UnicodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"filename": output_name, "data_base64": base64.b64encode(output).decode("ascii")}

    @router.post("/tools/schedule", status_code=201)
    async def create_natural_schedule(request: Request):
        payload = _json_payload(await request.json())
        kind = _required_text(payload, "kind", 20)
        value = _required_text(payload, "input", 5000)
        user_id = _required_text(payload, "user_id", 200)
        services = _service(request)
        if not services["repository"].is_sendable_target(user_id):
            raise HTTPException(status_code=422, detail="该用户尚无可用的微信会话上下文")
        try:
            values = natural_schedule_values(kind, value, user_id, services["config"].scheduler_timezone)
            next_run = services["scheduler"].prepare(values)
            return services["repository"].create_schedule(values, next_run)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/tools/mail/send")
    async def send_mail(request: Request):
        payload = _json_payload(await request.json())
        recipient = _required_text(payload, "recipient", 320)
        subject = _required_text(payload, "subject", 500)
        body = _required_text(payload, "body", 100_000)
        attachment = None
        if payload.get("data_base64"):
            attachment = (_decode_file(payload), Path(_required_text(payload, "filename", 255)).name)
        try:
            output = await _service(request)["mail"].send(recipient, subject, body, attachment)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            _service(request)["database"].log("error", "web_mail", type(exc).__name__)
            raise HTTPException(status_code=502, detail="邮件发送失败，请查看运行日志") from exc
        return {"output": output}

    @router.get("/control/commands")
    async def control_commands(request: Request):
        return _service(request)["control"].list_commands()

    @router.post("/control/commands/{command_id}")
    async def execute_control(command_id: str, request: Request):
        if command_id not in _service(request)["control"].commands:
            raise HTTPException(status_code=404, detail="unknown control command")
        return {"output": await _service(request)["control"].execute(command_id, "web")}

    @router.post("/tools/desktop/screenshot")
    async def desktop_screenshot(request: Request):
        payload = _json_payload(await request.json())
        raw_index = payload.get("screen_index")
        try:
            screen_index = None if raw_index in (None, "", "all") else int(raw_index)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="screen_index 必须是屏幕序号或 all") from exc
        try:
            data, mime = screenshot(screen_index)
        except DesktopError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"mime": mime, "data_base64": base64.b64encode(data).decode("ascii")}

    @router.post("/tools/desktop/focus")
    async def desktop_focus(request: Request):
        payload = _json_payload(await request.json())
        title = _required_text(payload, "title", 200)
        maximize = bool(payload.get("maximize", True))
        try:
            return {"output": focus_window(title, maximize)}
        except DesktopError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def configure_cors(app, settings: Settings) -> None:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.frontend_origins), allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def local_host_guard(request: Request, call_next):
        host = request.headers.get("host", "127.0.0.1").split(":", 1)[0].lower()
        allowed = {"127.0.0.1", "localhost", "::1"}
        if not settings.allow_unsafe_bind and host not in allowed:
            return JSONResponse({"detail": "local access only"}, status_code=403)
        return await call_next(request)
