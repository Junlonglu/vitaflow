from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="active")
    # 登录态版本号：改密/重置时自增，JWT 里带签发时的值，不一致即视为已吊销。
    # 不设 Redis 黑名单——黑名单要按 jti 存 24h，而「改密踢掉旧会话」用单调
    # 计数器就够了，且天然不受多实例/重启影响。
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    flow_points: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    register_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_login_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
