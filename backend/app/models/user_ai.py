from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# SQLite 只认 INTEGER PRIMARY KEY 的自增；with_variant 让单元测试能直接用
# 内存库跑真实 ORM，而 MySQL 侧仍保持与其它表一致的 BIGINT。
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class UserAiCredential(Base):
    """用户自带的大模型接入配置（BYOK）。

    一个用户可保存多条：不同服务商 / 不同模型 / 不同推理强度各一条，用
    ``is_default`` 标出默认项，也可在对话里按 id 临时切换。开启后对话走用户
    自己的额度，不再消耗平台 Flow Points；密钥只以密文落库，接口只回掩码。
    """

    __tablename__ = "user_ai_credentials"
    __table_args__ = (Index("ix_user_ai_credentials_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    provider_label: Mapped[str] = mapped_column(String(60), default="自定义")
    base_url: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(120))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    api_key_masked: Mapped[str] = mapped_column(String(40), default="")
    context_window: Mapped[int] = mapped_column(Integer, default=32000)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    supports_image: Mapped[bool] = mapped_column(Boolean, default=False)
    # 推理强度：auto 表示不额外下发参数，off 明确关闭，其余映射到 pi-agent
    # thinkingLevel（最终变成 OpenAI 兼容端的 reasoning_effort）。
    reasoning_effort: Mapped[str] = mapped_column(String(20), default="auto")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_verified_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserAiAuditLog(Base):
    """BYOK 密钥的使用与变更审计。

    只存动作、配置名与脱敏后的说明，绝不存在明文或完整密文；配置删除后
    记录仍保留（credential_id 成为历史引用），让用户能核对「谁在何时用了
    哪条配置」。同一用户之外不可见。
    """

    __tablename__ = "user_ai_audit_logs"
    __table_args__ = (Index("ix_user_ai_audit_user_id", "user_id", "id"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(40))
    credential_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(String(255), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


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
