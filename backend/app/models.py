from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["cron", "once"] = "cron"
    spec: str | None = Field(default=None, max_length=120)
    run_at: datetime | None = None
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    target_user_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10000)
    enabled: bool = True

    @field_validator("spec")
    @classmethod
    def trim_spec(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: Literal["cron", "once"] | None = None
    spec: str | None = Field(default=None, max_length=120)
    run_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    target_user_id: str | None = Field(default=None, min_length=1, max_length=200)
    message: str | None = Field(default=None, min_length=1, max_length=10000)
    enabled: bool | None = None


class Page(BaseModel):
    items: list[dict]
    total: int
    limit: int
    offset: int


class ConversationSummary(BaseModel):
    user_id: str
    message_count: int
    last_message_at: str
    last_direction: Literal["inbound", "outbound"]
    last_text: str


class MessageItem(BaseModel):
    id: int
    direction: Literal["inbound", "outbound"]
    user_id: str
    message_id: str | None = None
    message_type: str | None = None
    text: str
    created_at: str


class MessageHistory(BaseModel):
    items: list[MessageItem]
    has_more: bool
    next_before_id: int | None = None


class MessageTarget(BaseModel):
    user_id: str
    message_count: int
    last_message_at: str


class SendMessageRequest(BaseModel):
    target_user_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10000)

    @field_validator("target_user_id", "text")
    @classmethod
    def trim_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed


class SendMessageResponse(BaseModel):
    ok: bool = True
    target_user_id: str
    sent_parts: int


class DeleteMessagesRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    message_ids: list[int] = Field(min_length=1, max_length=200)
