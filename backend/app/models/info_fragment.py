from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# SQLite 只认 INTEGER PRIMARY KEY 的自增；with_variant 让单测能跑真实 ORM。
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class InfoFragment(Base):
    """原子信息：把简历拆成一条条可复用的事实。

    与简历的 ``resume_data`` 不同，这里存的是「与我有关的一条信息」而不是
    「某份简历的一段排版」：同一段项目经历可以同时用于多份简历、也可以在
    对话里被检索和改写。``kind`` 决定它属于哪个模块（项目/教育/奖项……），
    ``payload`` 就是该模块的标准条目结构，导入简历时可以直接落到对应模块。
    """

    __tablename__ = "info_fragments"
    __table_args__ = (
        Index("ix_info_fragments_user_kind", "user_id", "kind"),
        Index("ix_info_fragments_user_updated", "user_id", "update_time"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # 分类：basic / education / work / internship / project / skill / award / certificate …
    kind: Mapped[str] = mapped_column(String(24), default="custom", index=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 检索用的扁平文本（标题 + 摘要 + 标签 + 各字段值）：JSON 列在 MySQL/SQLite
    # 上的文本检索写法不通用，落一列普通文本让 LIKE 在两种数据库都成立。
    search_text: Mapped[str] = mapped_column(Text, default="")
    # 内容指纹：同分类 + 同内容判重，避免同一段经历被反复入库
    fingerprint: Mapped[str] = mapped_column(String(40), default="", index=True)
    # manual（手填）/ chat（对话生成）/ resume（从简历提取）/ import（批量导入）
    source: Mapped[str] = mapped_column(String(20), default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
