"""BYOK 密钥的对称加密。

用户自带 API Key 只以密文落库：密钥由 `JWT_SECRET_KEY` 派生（同一部署内的
所有进程都能解开，换 `JWT_SECRET_KEY` 会让旧密文失效——这是有意的轮换语义）。
接口层永远只回掩码，明文只出现在真正调用模型的那一次请求里。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "fernet:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"vitaflow-byok::{settings.jwt_secret_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    """解不开（换过密钥/脏数据）时返回空串，由调用方按「未配置」处理。"""
    if not value:
        return ""
    token = value[len(_PREFIX):] if value.startswith(_PREFIX) else value
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return ""


def mask_secret(plain: str) -> str:
    """`sk-abcdef...wxyz` → `sk-a...wxyz`，足够确认是不是同一把钥匙。"""
    value = (plain or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:4]}…{value[-4:]}"
