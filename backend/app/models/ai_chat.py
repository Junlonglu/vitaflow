from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiChatSession(Base):
    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(120), default="AI 简历助手")
    # 滚动摘要压缩：窗口外旧消息的 LLM 摘要 + 覆盖到的最后一条消息 id，
    # 每轮注入系统提示词（# 更早的对话摘要），让长对话保留早期意图与决定。
    context_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AiChatMessage(Base):
    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    attachments: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    suggestions: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    optimized_resume_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    tool_trace: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    # 回合环节耗时（毫秒）：first_token_ms 首字响应 / tool_ms 工具执行合计 /
    # total_ms 整回合；生成回复 = total - tool，由看板推导。
    timing: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    pending_questions: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    action_status: Mapped[str] = mapped_column(String(20), default="none")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
