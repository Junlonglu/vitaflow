from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.contracts import RESUME_TITLE_MAX
from app.core.database import Base

# SQLite 只认 INTEGER PRIMARY KEY 的自增；with_variant 让单测能跑真实 ORM
# （否则 BigInteger 主键插入必然 NOT NULL 失败）。
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class Resume(Base):
    __tablename__ = "resumes"
    # MySQL 的唯一索引把多个 NULL 视为互不相同，历史行（request_id 为空）不受影响。
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_resumes_user_request"),
        # 简历库的默认查询是「我的 + 按时间排序 + 分页」，单列索引只能命中过滤，
        # 排序仍要回表排序；组合索引让「过滤 + 排序」一次走完（RF-061）。
        Index("ix_resumes_user_created", "user_id", "create_time"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(RESUME_TITLE_MAX))
    language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    resume_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    template_id: Mapped[str] = mapped_column(String(50), default="tech")
    template_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    share_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    share_token: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, unique=True, index=True)
    share_expire_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    share_created_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    share_mask_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    # 幂等键：双击「新建」或网络重试不该产生两份简历。前端每次操作生成一个，
    # 唯一约束 (user_id, request_id) 兜底，重复提交回放首次结果（RF-065）。
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 写入版本号（乐观锁）：每次经 `persist_resume_data` 的写入 +1。
    # 秒级的 update_time 在同一秒内的两次写入上无法区分，因此不能当锁用。
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    create_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    update_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    # 列表按「某份简历的最近版本」倒序取，单列 resume_id 索引还要回表排序。
    __table_args__ = (Index("ix_resume_versions_resume_created", "resume_id", "create_time"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    resume_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    template_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # manual / ai / restore：清理策略只淘汰非手动版本，手动存档永久保留。
    source: Mapped[str] = mapped_column(String(20), default="manual")
    # 内容指纹：与上一版完全相同时不再新增一行（自动保存会反复触发）。
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 这份快照写于简历的第几版；恢复时可据此判断快照与编辑器的相对新旧。
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 序列化后的字节数，列表页展示体积时不必再读整份 JSON。
    size_bytes: Mapped[int] = mapped_column(default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    file_type: Mapped[str] = mapped_column(String(50))
    file_name: Mapped[str] = mapped_column(String(255))
    object_name: Mapped[str] = mapped_column(String(500))
    file_url: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ResumeStarter(Base):
    __tablename__ = "resume_starters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    starter_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    industry_id: Mapped[str] = mapped_column(String(50), index=True)
    industry_name: Mapped[str] = mapped_column(String(100))
    industry_description: Mapped[str] = mapped_column(String(255), default="")
    role_title: Mapped[str] = mapped_column(String(100))
    role_subtitle: Mapped[str] = mapped_column(String(120), default="")
    default_template_id: Mapped[str] = mapped_column(String(50), default="tech")
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    focus: Mapped[list[str]] = mapped_column(JSON, default=list)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_visible: Mapped[int] = mapped_column(default=1)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ResumeStarterIndustryTemplate(Base):
    __tablename__ = "resume_starter_industry_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    industry_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    industry_name: Mapped[str] = mapped_column(String(100))
    industry_description: Mapped[str] = mapped_column(String(255), default="")
    default_template_id: Mapped[str] = mapped_column(String(50), default="tech")
    note: Mapped[str] = mapped_column(String(255), default="")
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[int] = mapped_column(default=1)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
