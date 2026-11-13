"""BYOK 密钥的对称加密：独立密钥、多版本轮换、旧密文兼容。

密文格式：
- 旧版（0036/0037）：``fernet:<token>``，密钥由 ``JWT_SECRET_KEY`` 派生；
- 新版：``fernet:<kid>:<token>``，密钥来自 ``BYOK_ENCRYPTION_KEYS``。

配置形如 ``v2=新口令,v1=旧口令``：第一项是当前写入密钥，后面的只用于解密
历史密文。轮换时把新 key 加到最前、保留旧 key，等旧密文全部重写后再删掉；
接口层永远只回掩码，明文只出现在真正调用模型的那一次请求里。
"""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import DEFAULT_JWT_SECRET_KEY, settings

_PREFIX = "fernet:"
_LEGACY_KID = "legacy"
_KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ByokKeyError(RuntimeError):
    """BYOK 加密配置不可用（格式错误或生产环境仍在使用弱默认密钥）。"""


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _parse_keyring() -> list[tuple[str, str]]:
    """解析 ``kid=secret,...``；kid 唯一且不能占用保留名 legacy。"""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in (settings.byok_encryption_keys or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        kid, sep, secret = chunk.partition("=")
        kid = kid.strip()
        if (
            not sep
            or not secret.strip()
            or not _KID_RE.match(kid)
            or kid == _LEGACY_KID
            or kid in seen
        ):
            raise ByokKeyError(
                "BYOK_ENCRYPTION_KEYS 格式应为 kid=secret,kid2=secret2；"
                "kid 只能用字母数字下划线和短横线，且不可重复或使用 legacy"
            )
        seen.add(kid)
        items.append((kid, secret.strip()))
    return items


def _keyring() -> dict[str, Fernet]:
    ring = {kid: _fernet(f"vitaflow-byok::{kid}::{secret}") for kid, secret in _parse_keyring()}
    # legacy 只用于解开历史密文，永远不会成为写入密钥（除非未配置独立密钥）。
    ring[_LEGACY_KID] = _fernet(f"vitaflow-byok::{settings.jwt_secret_key}")
    return ring


def active_kid() -> str:
    """当前写入使用的 key id。"""
    items = _parse_keyring()
    return items[0][0] if items else _LEGACY_KID


def encryption_status() -> dict[str, Any]:
    """供启动检查与设置页「密钥如何存储」展示，不含任何口令内容。"""
    items = _parse_keyring()
    return {
        "mode": "dedicated" if items else "legacy",
        "active_kid": items[0][0] if items else _LEGACY_KID,
        "rotation_keys": len(items),
        "legacy_decrypt_enabled": True,
        "weak_default": not items
        and (settings.jwt_secret_key or "").strip() == DEFAULT_JWT_SECRET_KEY,
    }


def validate_encryption_config(*, strict: bool) -> str:
    """返回需要告警的提示；strict=True（生产）时弱默认直接拒绝启动。

    「拒绝弱默认」精确定义为：不允许在弱默认 JWT_SECRET_KEY 下继续用
    JWT 派生密钥加密新 Key。存量密文仍可解密，避免升级把用户挡在门外。
    """
    status = encryption_status()
    if status["mode"] == "dedicated":
        return ""
    if status["weak_default"]:
        message = (
            "BYOK 未配置 BYOK_ENCRYPTION_KEYS，且 JWT_SECRET_KEY 仍是弱默认值；"
            "生产环境禁止以弱密钥加密用户 API Key，请先生成独立随机口令"
        )
        if strict:
            raise ByokKeyError(message)
        return message
    return (
        "BYOK 仍回退到 JWT_SECRET_KEY 派生密钥（兼容存量密文）；"
        "建议配置独立的 BYOK_ENCRYPTION_KEYS，避免轮换登录密钥时影响已存 API Key"
    )


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    kid = active_kid()
    token = _keyring()[kid].encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{kid}:{token}"


def decrypt_secret(value: str) -> str:
    """解不开（换过密钥/脏数据/未知 kid）时返回空串，由调用方按「未配置」处理。"""
    if not value:
        return ""
    body = value[len(_PREFIX):] if value.startswith(_PREFIX) else value
    kid, sep, token = body.partition(":")
    if not sep or not _KID_RE.match(kid):
        # 旧格式 fernet:<token>（Fernet token 自身不含冒号）
        kid, token = _LEGACY_KID, body
    try:
        fernet = _keyring().get(kid)
        if not fernet:
            return ""
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError, ByokKeyError):
        return ""


def needs_rotation(value: str) -> bool:
    """旧格式、或不是当前 kid 的密文都可以在读取时透明重写。"""
    if not value:
        return False
    body = value[len(_PREFIX):] if value.startswith(_PREFIX) else value
    kid, sep, _ = body.partition(":")
    return not sep or not _KID_RE.match(kid) or kid != active_kid()


def mask_secret(plain: str) -> str:
    """`sk-abcdef...wxyz` → `sk-a…wxyz`，足够确认是不是同一把钥匙。"""
    value = (plain or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:4]}…{value[-4:]}"


__all__ = [
    "ByokKeyError",
    "active_kid",
    "decrypt_secret",
    "encrypt_secret",
    "encryption_status",
    "mask_secret",
    "needs_rotation",
    "validate_encryption_config",
]
