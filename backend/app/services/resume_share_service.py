from __future__ import annotations

import re
import secrets
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.models.resume import Resume
from app.services.resume_locale import resolve_resume_language, resume_locale


SENSITIVE_BASIC_FIELDS = {
    "phone",
    "email",
    "location",
    "status",
    "expected_salary",
    "website",
    "github",
    "avatar",
    "gender",
    "age",
    "birthday",
    "wechat",
    "qq",
    "id_card",
}

# 下限 8 位：3 位 token（abc / cv / job）可以被字典直接枚举命中，而分享链接
# 是公开可读的。已存在的短 token 仍然可读（get_shared_resume 不限长度），
# 只限制「新设置」的动作，避免把老链接改坏。
CUSTOM_SHARE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
CUSTOM_SHARE_TOKEN_MIN = 8
CUSTOM_SHARE_TOKEN_MAX = 64

# 字段白名单只覆盖 basics，但「联系方式：138xxxxxxxx」写在自我介绍里是常见
# 写法（AI 生成时尤其容易带上）。用户勾了「隐藏敏感信息」之后，这些文本里的
# 号码仍然是公开的——所以再叠一层全文模式掩码。
CONTACT_PATTERNS = (
    # 中国大陆手机号，允许 +86 / 86 前缀与空格、短横线分隔。
    re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)"),
    # 固话：区号 0xx(x) + 7~8 位号码。
    re.compile(r"(?<!\d)0\d{2,3}[\s-]?\d{7,8}(?!\d)"),
    re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"),
)
MASKED_TEXT = "***"


def normalize_expire_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def share_is_expired(expire_time: datetime | None) -> bool:
    return bool(expire_time and expire_time <= datetime.now())


def generate_share_token(db: Session) -> str:
    for _ in range(8):
        token = secrets.token_urlsafe(32)
        exists = db.scalar(select(Resume.id).where(Resume.share_token == token))
        if not exists:
            return token
    raise AppException("分享链接生成失败，请稍后重试", 500)


def normalize_custom_share_token(db: Session, value: str | None, resume_id: int | None = None) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if not CUSTOM_SHARE_TOKEN_PATTERN.fullmatch(token):
        raise AppException(
            f"自定义链接需 {CUSTOM_SHARE_TOKEN_MIN}-{CUSTOM_SHARE_TOKEN_MAX} 位字母、数字、短横线或下划线，且需以字母或数字开头",
            400,
        )
    query = select(Resume.id).where(Resume.share_token == token)
    if resume_id is not None:
        query = query.where(Resume.id != resume_id)
    if db.scalar(query):
        raise AppException("该自定义链接已被使用，请换一个", 409)
    return token


def get_shared_resume(db: Session, token: str) -> Resume:
    normalized = str(token or "").strip()
    if not normalized or len(normalized) > 80:
        raise AppException("分享不存在或已失效", 404)
    resume = db.scalar(select(Resume).where(Resume.share_token == normalized))
    if not resume or not resume.share_enabled or share_is_expired(resume.share_expire_time):
        raise AppException("分享不存在或已失效", 404)
    return resume


def _mask_contact_text(value: Any) -> Any:
    """递归遍历简历数据，把文本字段里的联系方式替换成掩码。

    只认「看起来像联系方式」的串，不动其它数字：年份、项目规模、GPA 之类不该
    被误伤。字典的键（字段名）不参与匹配，值才匹配。
    """
    if isinstance(value, str):
        masked = value
        for pattern in CONTACT_PATTERNS:
            masked = pattern.sub(MASKED_TEXT, masked)
        return masked
    if isinstance(value, list):
        return [_mask_contact_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _mask_contact_text(item) for key, item in value.items()}
    return value


def prepare_public_share_content(resume: Resume) -> tuple[dict[str, Any], dict[str, Any], str]:
    resume_data = deepcopy(resume.resume_data or {})
    template_config = deepcopy(resume.template_config or {})
    title = resume.title
    if not resume.share_mask_sensitive:
        return resume_data, template_config, title

    basics = resume_data.get("basics")
    if not isinstance(basics, dict):
        basics = {}
        resume_data["basics"] = basics
    labels = resume_locale(resolve_resume_language(resume.language, resume_data))
    basics["name"] = labels["candidate"]
    for field in SENSITIVE_BASIC_FIELDS:
        basics[field] = ""
    basics["custom_fields"] = []
    template_config["show_avatar"] = False
    # 结构化字段清空之后再过一遍全文：summary、自定义模块、经历描述里的
    # 手机号/邮箱同样是「用户以为已经脱敏」的部分。
    resume_data = _mask_contact_text(resume_data)
    return resume_data, template_config, labels["anonymous_resume"]
