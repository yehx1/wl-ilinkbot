from __future__ import annotations
import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

class MailService:
    def __init__(self, host: str, port: int, user: str, password: str, sender: str, use_ssl: bool = False):
        self.configure(host, port, user, password, sender, use_ssl)

    def configure(self, host: str, port: int, user: str, password: str, sender: str, use_ssl: bool = False) -> None:
        self.host, self.port, self.user, self.password, self.sender, self.use_ssl = host, port, user, password, sender, use_ssl

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.sender)

    async def send(self, recipient: str, subject: str, body: str, attachment: tuple[bytes, str] | None = None) -> str:
        if not self.configured:
            return "尚未配置 SMTP，请在 backend/.env 中填写 SMTP_HOST、SMTP_USER、SMTP_PASSWORD、SMTP_FROM。"
        return await asyncio.to_thread(self._send, recipient, subject, body, attachment)

    def _send(self, recipient: str, subject: str, body: str, attachment: tuple[bytes, str] | None = None) -> str:
        if any("\n" in value or "\r" in value for value in (recipient, subject, self.sender)):
            raise ValueError("邮件地址和主题不能包含换行")
        if not parseaddr(recipient)[1] or "@" not in parseaddr(recipient)[1]:
            raise ValueError("收件人地址无效")
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, recipient, subject
        message.set_content(body)
        if attachment:
            data, filename = attachment
            if not data or len(data) > 20 * 1024 * 1024:
                raise ValueError("邮件附件不能为空且不能超过 20 MB")
            safe_name = Path(filename).name
            if not safe_name or "\n" in safe_name or "\r" in safe_name:
                raise ValueError("附件名称无效")
            message.add_attachment(data, maintype='application', subtype='octet-stream', filename=safe_name)
        client = smtplib.SMTP_SSL(self.host, self.port, timeout=30) if self.use_ssl else smtplib.SMTP(self.host, self.port, timeout=30)
        with client:
            if not self.use_ssl:
                client.starttls()
            client.login(self.user, self.password)
            client.send_message(message)
        return "邮件已发送。"
