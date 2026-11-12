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
    # 目标会话：省略时落到该简历最近一次的会话（兼容旧前端的 1:1 行为）。
    session_id: Optional[int] = None


# 会话标题上限，与 ai_chat_sessions.title 的 String(120) 留出余量。
MAX_CHAT_SESSION_TITLE_LENGTH = 60


class AiChatSessionCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=MAX_CHAT_SESSION_TITLE_LENGTH)


class AiChatSessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_CHAT_SESSION_TITLE_LENGTH)


class AiChatSessionOut(BaseModel):
    id: int
    resume_id: int
    title: str
    message_count: int = 0
    last_message: str = ""
    create_time: datetime
    update_time: datetime


class AiChatRegenerateRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=MAX_CHAT_CONTENT_LENGTH)
    attachments: Optional[list[AiChatAttachment]] = Field(default=None, max_length=MAX_CHAT_ATTACHMENTS)
    model_config_id: Optional[int] = None
    session_id: Optional[int] = None


class AiChatDecisionRequest(BaseModel):
    action: Literal["apply", "reject"]
    # 审阅模式部分采纳：前端勾选变更后合并到当前简历数据提交；缺省走整包快照
    merged_resume_data: Optional[dict[str, Any]] = None


class AiChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    attachments: list[AiChatAttachment] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    optimized_resume_data: Optional[dict[str, Any]] = None
    tool_trace: Optional[list[dict[str, Any]]] = None
    timing: Optional[dict[str, Any]] = None
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
