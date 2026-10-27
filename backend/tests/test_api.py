from pathlib import Path
import base64

from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.main import _codex_artifact_references
from app.wechat_service import _qr_image_src


class FakeBot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.handler = None
        self.stopped = False

    def on_message(self, handler):
        self.handler = handler

    async def login(self, *, force=False):
        self.login_force = force
        self.kwargs["on_qr_url"]("https://example.invalid/qr")

    async def start(self):
        import asyncio
        while not self.stopped:
            await asyncio.sleep(0.01)

    async def reply(self, message, text):
        self.last_reply = (message.user_id, text)

    async def send_media(self, user_id, content):
        self.last_media = (user_id, content)

    async def stop(self):
        self.stopped = True


class QrWaitingBot(FakeBot):
    async def login(self, *, force=False):
        import asyncio
        self.login_force = force
        self.kwargs["on_qr_url"]("https://example.invalid/qr")
        self.login_release = asyncio.Event()
        await self.login_release.wait()

    async def stop(self):
        await super().stop()
        if hasattr(self, "login_release"):
            self.login_release.set()


def test_qr_payload_is_browser_image_source(monkeypatch):
    png = "iVBORw0KGgo="
    assert _qr_image_src(png) == f"data:image/png;base64,{png}"
    assert _qr_image_src("data:image/jpeg;base64,abc") == "data:image/jpeg;base64,abc"
    monkeypatch.setattr("app.wechat_service._render_qr", lambda value: "data:image/png;base64,generated")
    remote = _qr_image_src("https://example.invalid/qr")
    assert remote == "data:image/png;base64,generated"
    wrapped = _qr_image_src('{"qrcode_img_content":"https://example.invalid/qr"}')
    assert wrapped == "data:image/png;base64,generated"


def test_codex_artifact_references_support_markdown_and_forward_slashes(tmp_path: Path):
    artifact = tmp_path / "screen one.png"
    artifact.write_bytes(b"png")
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"no")
    result = f"[下载截图]({artifact.as_posix()})\n另一个文件：{outside.as_posix()}"
    assert _codex_artifact_references(result, tmp_path) == [artifact.resolve(), outside.resolve()]


def test_health_status_and_schedule_validation(tmp_path: Path):
    settings = load_settings({"DATA_DIR": str(tmp_path / "data"), "CONTROL_ALLOWLIST": str(Path(__file__).parents[1] / "config" / "control_allowlist.json")}, tmp_path)
    with TestClient(create_app(settings, FakeBot)) as client:
        assert client.get("/api/health", headers={"host": "127.0.0.1"}).json() == {"ok": True}
        response = client.get("/api/status", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        assert "context_token" not in response.text
        invalid = client.post("/api/schedules", headers={"host": "127.0.0.1"}, json={"name": "bad", "kind": "cron", "spec": "bad", "timezone": "UTC", "target_user_id": "u", "message": "m"})
        assert invalid.status_code == 422


def test_reconnect_forces_fresh_qr_login(tmp_path: Path):
    settings = load_settings({"DATA_DIR": str(tmp_path / "data"), "CONTROL_ALLOWLIST": str(Path(__file__).parents[1] / "config" / "control_allowlist.json")}, tmp_path)
    with TestClient(create_app(settings, QrWaitingBot)) as client:
        response = client.post("/api/wechat/reconnect", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        assert response.json()["wechat"]["state"] == "qr_ready"
        assert response.json()["wechat"]["qr_url"].startswith("data:image/png;base64,")
        assert client.app.state.services["wechat"].bot.login_force is True


def test_message_history_and_proactive_send(tmp_path: Path):
    settings = load_settings({"DATA_DIR": str(tmp_path / "data"), "CONTROL_ALLOWLIST": str(Path(__file__).parents[1] / "config" / "control_allowlist.json")}, tmp_path)
    with TestClient(create_app(settings, FakeBot)) as client:
        headers = {"host": "127.0.0.1"}
        services = client.app.state.services
        repository = services["repository"]
        repository.add_message("inbound", "user-1", "hello", message_type="text")
        repository.save_context_token("user-1", "secret-token")
        services["wechat"]._set_state("connected")

        conversations = client.get("/api/conversations", headers=headers)
        assert conversations.status_code == 200
        assert conversations.json()["items"][0]["last_text"] == "hello"
        targets = client.get("/api/message-targets", headers=headers)
        assert targets.json()["items"][0]["user_id"] == "user-1"
        assert "secret-token" not in targets.text + conversations.text

        sent = client.post("/api/messages", headers=headers, json={"target_user_id": "user-1", "text": "from web"})
        assert sent.status_code == 200
        assert sent.json()["sent_parts"] == 1
        history = client.get("/api/messages?user_id=user-1", headers=headers).json()
        assert [item["direction"] for item in history["items"]] == ["inbound", "outbound"]
        assert history["items"][-1]["text"] == "from web"
        assert "secret-token" not in str(history)

        rejected = client.post("/api/messages", headers=headers, json={"target_user_id": "unknown", "text": "no"})
        assert rejected.status_code == 422

        saved_note = client.patch("/api/user-notes/user-1", headers=headers, json={"note": "重要联系人"})
        assert saved_note.status_code == 200
        assert client.get("/api/user-notes", headers=headers).json()["items"]["user-1"] == "重要联系人"
        too_long = client.patch("/api/user-notes/user-1", headers=headers, json={"note": "x" * 121})
        assert too_long.status_code == 422


def test_provider_settings_apply_immediately_and_reject_newlines(tmp_path: Path, monkeypatch):
    written = {}
    monkeypatch.setattr("app.api._write_env", lambda path, updates: written.update(updates))
    settings = load_settings({"DATA_DIR": str(tmp_path / "data"), "CONTROL_ALLOWLIST": str(Path(__file__).parents[1] / "config" / "control_allowlist.json")}, tmp_path)
    with TestClient(create_app(settings, FakeBot)) as client:
        headers = {"host": "127.0.0.1"}
        response = client.patch(
            "/api/settings/providers",
            headers=headers,
            json={
                "AI_PROVIDER": "openai",
                "OPENAI_API_KEY": "secret",
                "OPENAI_MODEL": "model-x",
                "OPENAI_API_MODE": "chat_completions",
                "SMTP_SSL": "false",
            },
        )
        assert response.status_code == 200
        assert response.json()["restart_required"] is False
        status = client.get("/api/status", headers=headers).json()
        assert status["ai"]["provider"] == "openai"
        assert status["ai"]["model"] == "model-x"
        assert client.app.state.services["llm"].provider == "openai"
        assert written["OPENAI_API_KEY"] == "secret"
        invalid = client.patch("/api/settings/providers", headers=headers, json={"OPENAI_MODEL": "bad\nvalue"})
        assert invalid.status_code == 422


def test_web_tool_endpoints_and_real_media_send(tmp_path: Path):
    settings = load_settings({"DATA_DIR": str(tmp_path / "data"), "CONTROL_ALLOWLIST": str(Path(__file__).parents[1] / "config" / "control_allowlist.json")}, tmp_path)
    with TestClient(create_app(settings, FakeBot)) as client:
        headers = {"host": "127.0.0.1"}
        services = client.app.state.services
        repository = services["repository"]
        repository.add_message("inbound", "user-1", "hello")
        repository.save_context_token("user-1", "token")
        services["wechat"]._set_state("connected")

        media = client.post("/api/messages/media", headers=headers, json={"target_user_id": "user-1", "filename": "note.txt", "data_base64": base64.b64encode(b"hello").decode(), "caption": "附件"})
        assert media.status_code == 200
        assert services["wechat"].bot.last_media[1]["file"] == b"hello"
        assert services["wechat"].bot.last_media[1]["caption"] == "附件"
        assert repository.list_messages("user-1")[0][-1]["message_type"] == "file"

        converted = client.post("/api/tools/convert", headers=headers, json={"filename": "note.txt", "target": "md", "data_base64": base64.b64encode(b"hello").decode()})
        assert converted.status_code == 200
        assert converted.json()["filename"] == "note.md"

        created_todo = client.post("/api/tools/todos", headers=headers, json={"user_id": "user-1", "text": "完成测试"})
        assert created_todo.status_code == 201
        todo_id = created_todo.json()["id"]
        assert client.get("/api/tools/todos?user_id=user-1", headers=headers).json()["items"][0]["text"] == "完成测试"
        assert client.post(f"/api/tools/todos/{todo_id}/complete", headers=headers, json={"user_id": "user-1"}).status_code == 200

        reminder = client.post("/api/tools/schedule", headers=headers, json={"kind": "remind", "input": "20分钟后提醒我开会", "user_id": "user-1"})
        assert reminder.status_code == 201
        assert reminder.json()["kind"] == "once"


def test_web_model_tools_generated_tool_and_mail(tmp_path: Path):
    settings = load_settings({"DATA_DIR": str(tmp_path / "data"), "CONTROL_ALLOWLIST": str(Path(__file__).parents[1] / "config" / "control_allowlist.json")}, tmp_path)
    with TestClient(create_app(settings, FakeBot)) as client:
        headers = {"host": "127.0.0.1"}
        services = client.app.state.services
        calls = []

        async def generate(prompt, requester=None):
            calls.append((prompt, requester))
            if "工具定义生成器" in prompt:
                return '{"name":"润色","description":"润色文字","instructions":"把文字改得更清楚","input_hint":"原文"}'
            return "执行结果"

        async def ocr(image, mime, requester=None):
            return f"OCR:{mime}:{image.decode()}"

        async def mail(recipient, subject, body, attachment=None):
            calls.append((recipient, subject, body, attachment))
            return "邮件已发送。"

        services["llm"].generate = generate
        services["llm"].ocr = ocr
        services["mail"].send = mail

        image = base64.b64encode(b"image").decode()
        assert client.post("/api/tools/ocr", headers=headers, json={"data_base64": image, "mime": "image/png"}).json()["output"] == "OCR:image/png:image"
        tool = client.post("/api/tools/generated", headers=headers, json={"user_id": "user-1", "description": "帮我润色"})
        assert tool.status_code == 201
        tool_id = tool.json()["id"]
        disabled = client.post(f"/api/tools/generated/{tool_id}/run", headers=headers, json={"user_id": "user-1", "input": "原文"})
        assert disabled.status_code == 409
        assert client.patch(f"/api/tools/generated/{tool_id}?user_id=user-1&enabled=true", headers=headers).status_code == 200
        assert client.post(f"/api/tools/generated/{tool_id}/run", headers=headers, json={"user_id": "user-1", "input": "原文"}).json()["output"] == "执行结果"
        assert client.post("/api/tools/mail/send", headers=headers, json={"recipient": "a@example.com", "subject": "主题", "body": "正文"}).json()["output"] == "邮件已发送。"
