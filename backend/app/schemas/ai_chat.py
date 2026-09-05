from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AiChatAttachment(BaseModel):
    url: str
    name: str = ""
    content_type: str = "image"
    object_name: Optional[str] = None


# 与前端 MAX_ATTACHMENTS 对齐；后端兜底防绕过。
MAX_CHAT_ATTACHMENTS = 9
# 消息正文上限（约万字以内），防超长文本打爆模型上下文与计费。
MAX_CHAT_CONTENT_LENGTH = 8000


class AiChatSendRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CHAT_CONTENT_LENGTH)
    attachments: list[AiChatAttachment] = Field(default_factory=list, max_length=MAX_CHAT_ATTACHMENTS)
    model_config_id: Optional[int] = None


class AiChatRegenerateRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=MAX_CHAT_CONTENT_LENGTH)
    attachments: Optional[list[AiChatAttachment]] = Field(default=None, max_length=MAX_CHAT_ATTACHMENTS)
    model_config_id: Optional[int] = None


class AiChatDecisionRequest(BaseModel):
    action: Literal["apply", "reject"]


class AiChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    attachments: list[AiChatAttachment] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    optimized_resume_data: Optional[dict[str, Any]] = None
    tool_trace: Optional[list[dict[str, Any]]] = None
    pending_questions: Optional[list[dict[str, Any]]] = None
    action_status: str = "none"
    create_time: datetime

    model_config = {"from_attributes": True}


class AiChatSendResponse(BaseModel):
    messages: list[AiChatMessageOut]
    assistant_message: AiChatMessageOut


class AiChatDecisionResponse(BaseModel):
    assistant_message: AiChatMessageOut
    resume_data: Optional[dict[str, Any]] = None
