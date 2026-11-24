"""私有对象的短时效签名。

**问题**：对话图片是 `<img src>` 直接加载的，带不了 Authorization 头，于是前端
只能把登录 JWT 拼在 URL 上（`?token=...`）。一旦这个 URL 进入浏览器历史、代理
访问日志、错误上报，等于把 24 小时有效的账号凭据写在了日志里（RF-025）。

**做法**：后端签发一段只对该对象、只在该时段有效的签名：

    /api/files/ai-chat/7_ab12.png?sig=<hmac>&exp=<unix>

签名覆盖「对象名 + 过期时间」，所以换个文件名就失效；有效期默认 6 小时，
够一屏历史图片慢慢加载，又不至于成为长期凭据。密钥由 JWT 密钥派生——
轮换登录密钥时，所有未过期的图片签名一起失效。

**为什么在序列化时签**：历史消息里的 URL 是落库的，上传时就签死会过期。
每次把消息返回给前端时重新签，等于「每次打开对话都是新的、短的、只对这几张图
有效的凭据」。
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.core import contracts
from app.core.config import settings

SIG_PARAM = "sig"
EXP_PARAM = "exp"


def _signing_key() -> bytes:
    return hmac.new(
        (settings.jwt_secret_key or "").encode("utf-8"),
        b"vitaflow::signed-file-url",
        hashlib.sha256,
    ).digest()


def signature_for(object_name: str, expires_at: int) -> str:
    payload = f"{object_name}\n{int(expires_at)}".encode("utf-8")
    return hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()


def signed_query(object_name: str, *, ttl_seconds: int | None = None, now: int | None = None) -> str:
    """返回 `sig=...&exp=...`，由调用方拼到已有 URL 上（保留原有 query）。"""
    issued = int(now if now is not None else time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else contracts.FILE_URL_TTL_SECONDS)
    expires_at = issued + ttl
    return f"{SIG_PARAM}={signature_for(object_name, expires_at)}&{EXP_PARAM}={expires_at}"


def verify_signature(object_name: str, sig: str | None, exp: str | None, *, now: int | None = None) -> bool:
    """签名对得上且没过期。任何缺项/畸形一律 False，不区分原因（不泄露细节）。"""
    if not sig or not exp:
        return False
    try:
        expires_at = int(exp)
    except (TypeError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if expires_at <= current:
        return False
    expected = signature_for(object_name, expires_at)
    return hmac.compare_digest(expected, (sig or "").strip())


def append_signature(url: str, object_name: str, *, ttl_seconds: int | None = None) -> str:
    """把签名拼到 URL 上；已带 `sig=` 的不重复签。"""
    if not url:
        return url
    query = signed_query(object_name, ttl_seconds=ttl_seconds)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{query}"
