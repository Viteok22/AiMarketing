from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Company:
    id: str
    name: str
    description: str = ""
    products: str = ""
    audience: str = ""
    advantages: str = ""
    tone: str = ""
    forbidden: str = ""
    knowledge: str = ""
    channel_id: str = ""
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Task:
    id: str
    company_id: str
    user_id: int
    request: str
    status: str = "drafting"
    post_text: str = ""
    image_path: str = ""
    revision_comment: str = ""
    published_message_id: Optional[int] = None
    published_chat_id: Optional[int] = None
    selected_text_id: Optional[str] = None
    selected_image_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    error: str = ""
    chatgpt_url: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Variant:
    id: str
    task_id: str
    kind: str
    batch_no: int
    index_no: int
    content: str = ""
    image_path: str = ""
    image_prompt: str = ""
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class ChatMessage:
    role: str
    text: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class PostDraft:
    headline: str
    body: str
    hashtags: str
    full_text: str
    image_prompt: str = ""


STATUS_LABELS = {
    "drafting": "Готовится",
    "pending_approval": "На согласовании",
    "scheduled": "Запланировано",
    "published": "Опубликовано",
    "rejected": "Отклонено",
    "failed": "Ошибка",
}

STATUS_CSS = {
    "drafting": "status-drafting",
    "pending_approval": "status-pending",
    "scheduled": "status-scheduled",
    "published": "status-published",
    "rejected": "status-rejected",
    "failed": "status-failed",
}

CALENDAR_STATUS_LABELS = {
    "planned": "Запланировано",
    "generating": "Генерируется",
    "draft": "Черновик в работе",
    "published": "Опубликовано",
    "cancelled": "Отменено",
    "failed": "Ошибка",
}

CALENDAR_STATUS_CSS = {
    "planned": "status-scheduled",
    "generating": "status-drafting",
    "draft": "status-pending",
    "published": "status-published",
    "cancelled": "status-rejected",
    "failed": "status-failed",
}
