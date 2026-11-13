"""Agent 轨迹（L1）事实表：一次对话回合一行，events 存事件流。

与消息表、任务表刻意解耦：

- 轨迹是分析资产，删简历、注销用户后仍要保留（L1 里没有 PII，只有结构、
  计数、耗时、成败与分段版本 hash），所以不加外键、不做级联删除。
- 也就不能靠 join 拿简历标题之类的信息 —— RSI 用不到，反而会成为留存负担。

L2（用户原文、工具入参原文）不在这张表里，走对象存储 + TTL。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiAgentTrace(Base):
    __tablename__ = "ai_agent_traces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 一次回合一个 uuid，同时也是 feedback 与 L2 blob 的关联键；上报重试靠它幂等。
    trace_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 触发本回合的用户消息 id（ai-service 侧不知道 assistant 行，只能拿到这个）。
    turn_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    session_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    # "provider/model"：模型维度的效果对比是 RSI 的第一层分组。
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    # 分段版本 {"rules","schema","skill","prompt_chars","agent"}，缺了它旧数据永久失去归因能力。
    prompt_versions: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    events: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    # 冗余一列便于 GROUP BY 时估算轨迹体量，不必解 JSON。
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
