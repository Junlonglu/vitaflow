from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# SQLite 只认 INTEGER PRIMARY KEY 的自增；with_variant 让单元测试能直接用
# 内存库跑真实 ORM，而 MySQL 侧仍保持与其它表一致的 BIGINT。
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class UserAiCredential(Base):
    """用户自带的大模型接入配置（BYOK）。

    每个用户最多一条：接入点、模型名、加密后的 API Key。开启后对话会走用户
    自己的额度，不再消耗平台 Flow Points；密钥只以密文落库，接口只回掩码。
    """

    __tablename__ = "user_ai_credentials"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_ai_credentials_user"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider_label: Mapped[str] = mapped_column(String(60), default="自定义")
    base_url: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(120))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    api_key_masked: Mapped[str] = mapped_column(String(40), default="")
    context_window: Mapped[int] = mapped_column(Integer, default=32000)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    supports_image: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_verified_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserSkill(Base):
    """用户上传的自定义技能。

    技能就是一段给模型的额外指令（Markdown 正文 + 描述），启用后会拼进系统
    提示词。`slug` 用于在提示词里去重和前端定位，同一用户内唯一。
    """

    __tablename__ = "user_skills"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_user_skills_user_slug"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    slug: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(255), default="")
    instructions: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
