from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import Database
from app.repositories import Repository
from app.router import MessageRouter


@pytest.mark.asyncio
async def test_todo_mail_generated_tool_and_ocr_flows(tmp_path: Path):
    repository = Repository(Database(tmp_path / "db.sqlite3"))
    sent = []
    llm_prompts = []
    mailed = []
    ocr_calls = []
    scheduled = []
    sent_files = []

    async def send(user, text):
        sent.append((user, text))
        return 1

    async def llm(prompt, requester=None):
        llm_prompts.append(prompt)
        if "工具定义生成器" in prompt:
            return '{"name":"改写","description":"改写文本","instructions":"把输入改写得简洁","input_hint":"原文"}'
        return "模型结果"

    async def mail(recipient, subject, body, attachment=None):
        mailed.append((recipient, subject, body, attachment))
        return "邮件已发送。"

    async def ocr(image, mime, requester=None):
        ocr_calls.append((image, mime, requester))
        return "图片文字"

    async def unused(*_args):
        return "unused"

    async def schedule(kind, text, user_id):
        scheduled.append((kind, text, user_id))
        return "任务已创建"

    async def send_file(user, data, filename, caption=None):
        sent_files.append((user, data, filename, caption))

    artifact = tmp_path / "screen.png"
    artifact.write_bytes(b"png")
    router = MessageRouter(send, unused, unused, repository, llm=llm, mail=mail, ocr=ocr, schedule=schedule, send_file=send_file, codex_workdir=tmp_path)

    await router.handle(SimpleNamespace(user_id="u", text="/todo 写报告", message_id="1", type="text"))
    assert repository.list_todos("u")[0]["text"] == "写报告"

    await router.handle(SimpleNamespace(user_id="u", text="/mail a@example.com | 周报 | 正文", message_id="2", type="text"))
    assert not mailed
    await router.handle(SimpleNamespace(user_id="u", text="/mail confirm", message_id="3", type="text"))
    assert mailed[0][:3] == ("a@example.com", "周报", "正文")

    await router.handle(SimpleNamespace(user_id="u", text="/create-tool 帮我改写", message_id="4", type="text"))
    tool = repository.list_generated_tools("u")[0]
    assert not tool["enabled"]
    repository.set_generated_tool_enabled("u", tool["id"], True)
    await router.handle(SimpleNamespace(user_id="u", text=f"/run-tool confirm {tool['id']} 一段原文", message_id="5", type="text"))
    assert "一段原文" in llm_prompts[-1]
    assert sent[-1][1] == "模型结果"

    media = SimpleNamespace(type="image", data=b"img", mime_type="image/png")
    await router.handle(SimpleNamespace(user_id="u", text="/ocr", message_id="6", type="image", _downloaded_media=media))
    assert ocr_calls == [(b"img", "image/png", "u")]
    assert sent[-1][1] == "图片文字"

    await router.handle(SimpleNamespace(user_id="u", text="/remind 20分钟后提醒我开会", message_id="7", type="text"))
    await router.handle(SimpleNamespace(user_id="u", text="/repeat 每天9点发送天气", message_id="8", type="text"))
    assert scheduled == [("remind", "20分钟后提醒我开会", "u"), ("repeat", "每天9点发送天气", "u")]

    await router._send_codex_result("u", f"[send_to_wechat:{artifact}]")
    assert sent_files == [("u", b"png", "screen.png", None)]

    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"pdf")
    await router._send_codex_result("u", f"[send_to_wechat:{outside}]")
    assert sent_files[-1] == ("u", b"pdf", "outside.pdf", None)

    escaped = tmp_path.parent / "escaped.png"
    escaped.write_bytes(b"escape")
    await router._send_codex_result("u", "[send_to_wechat:../escaped.png]")
    assert sent_files[-1] == ("u", b"pdf", "outside.pdf", None)

    unsupported = tmp_path.parent / "secret.env"
    unsupported.write_bytes(b"secret")
    await router._send_codex_result("u", f"[send_to_wechat:{unsupported}]")
    assert sent_files[-1] == ("u", b"pdf", "outside.pdf", None)
