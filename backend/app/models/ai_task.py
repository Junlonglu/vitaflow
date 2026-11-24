from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# SQLite 只认 INTEGER PRIMARY KEY 的自增；with_variant 让单测能跑真实 ORM
# （清扫器要插入任务行，BigInteger 主键在 SQLite 上必然 NOT NULL 失败）。
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class AiTask(Base):
    __tablename__ = "ai_tasks"
    # 回合级幂等键：同一回合的用量上报只能落一次任务。一个回合可能有多个模型
    # （路由组/中途换人），所以唯一键是「回合 + 槽位」而不是 turn_id 单列。
    __table_args__ = (
        UniqueConstraint("turn_id", "turn_slot", name="uq_ai_tasks_turn_slot"),
        # 「我的 AI 记录」与「我的点数流水」都是 user_id 过滤 + create_time 倒序，
        # 单列索引会让每次翻页都做一次 filesort（RF-061）。
        Index("ix_ai_tasks_user_created", "user_id", "create_time"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    # 被回复的用户消息 id。回合与消息、轨迹、任务之间目前唯一的连接键，
    # 也是计费幂等的判重依据（RF-007）。非对话任务与历史数据为 NULL。
    turn_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    # 同一回合内第几个用量条目（0 起）。单模型回合恒为 0。
    turn_slot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_type: Mapped[str] = mapped_column(String(50), index=True)
    # 对话回合的快捷指令（polish/translate/optimize/generate/greeting/chat），
    # 非对话任务为 NULL；功能分布按「ai_chat 行取 feature、其余行取 task_type」合并。
    feature: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    input_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    output_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # 只表示「执行结果」：pending / success / failed。结算成败在 billing_status 里，
    # 否则「内容已交付但扣费失败」只能二选一，看板成功率与真实交付对不上（RF-017）。
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # 计费结果：unbilled（已建任务未结算）/ billed（已结算）/ refunded（失败已退款）/
    # failed（结算过程自身异常，需要人工看）。与 status 正交。
    billing_status: Mapped[str] = mapped_column(
        String(20), default="unbilled", server_default="unbilled", index=True
    )
    # 租约到期时间：非终态必须有它，否则进程被杀后任务永远停在 pending，
    # 预扣的点数也永远不回。清扫器按这一列收尾（RF-009）。
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    points_used: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
