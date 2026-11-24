from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# SQLite 只认 INTEGER PRIMARY KEY 的自增；with_variant 让单测能跑真实 ORM
# （消息由服务端插入、不带 id，BigInteger 主键在 SQLite 上必然 NOT NULL 失败）。
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class AiChatSession(Base):
    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(120), default="AI 简历助手")
    # 置顶：排序时排在所有会话之前，与「最近活动」解耦（用户主动钉住的对话不被新对话顶下去）。
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 滚动摘要压缩：窗口外旧消息的 LLM 摘要 + 覆盖到的最后一条消息 id，
    # 每轮注入系统提示词（# 更早的对话摘要），让长对话保留早期意图与决定。
    context_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AiChatMessage(Base):
    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    attachments: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    suggestions: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    optimized_resume_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # Agent 提议写入信息库的条目（用户确认前只挂在这条消息上，不落信息库）
    pending_info_fragments: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    tool_trace: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    # 回合环节耗时（毫秒）：first_token_ms 首字响应 / tool_ms 工具执行合计 /
    # total_ms 整回合；生成回复 = total - tool，由看板推导。
    timing: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    pending_questions: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    action_status: Mapped[str] = mapped_column(String(20), default="none")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
