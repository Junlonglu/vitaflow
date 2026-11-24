from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_JWT_SECRET_KEY, settings
from app.core.database import get_db
from app.models.user import User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

MIN_JWT_SECRET_BYTES = 32


class InsecureJwtSecretError(RuntimeError):
    """生产环境 JWT 密钥不安全时拒绝启动。"""


def jwt_secret_issue() -> str:
    """描述 JWT 密钥的问题；没问题返回空串。

    与 BYOK 的 ``validate_encryption_config`` 刻意分开：那条校验只在「没有独立
    BYOK 密钥」时才顺带看一眼 JWT，配了 BYOK 就会静默放行弱登录密钥。登录态是
    更前置的信任边界，必须独立判断。
    """
    secret = (settings.jwt_secret_key or "").strip()
    if not secret:
        return "JWT_SECRET_KEY 为空"
    if secret == DEFAULT_JWT_SECRET_KEY:
        return "JWT_SECRET_KEY 仍是示例默认值"
    size = len(secret.encode("utf-8"))
    if size < MIN_JWT_SECRET_BYTES:
        return f"JWT_SECRET_KEY 只有 {size} 字节，少于 {MIN_JWT_SECRET_BYTES} 字节"
    return ""


def validate_jwt_secret(*, strict: bool) -> str:
    """返回告警文案；``strict=True``（生产）时直接抛错拒绝启动。"""
    issue = jwt_secret_issue()
    if not issue:
        return ""
    message = f"{issue}，任何拿到该值的人都能伪造任意用户的登录态"
    hint = '生成：python -c "import secrets; print(secrets.token_urlsafe(48))"'
    if strict:
        raise InsecureJwtSecretError(f"{message}。{hint}")
    return f"{message}。{hint}"


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(
    subject: Union[str, int],
    expires_delta: Optional[timedelta] = None,
    *,
    token_version: int = 0,
) -> str:
    """签发登录态。

    ``token_version`` 是用户的登录态版本号（``users.token_version``）。改密或
    重置密码会让它自增，于是所有旧 token 立刻失效——不需要黑名单，也不需要
    Redis：签发时把版本号写进 ``tv``，校验时和库里的值比一次即可。单调计数
    天然不受多实例、重启、时钟回拨影响。
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "tv": int(token_version or 0)}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _token_claims(token: str) -> tuple[int, int] | None:
    """解出 (用户 id, 登录态版本)；签名/过期/格式异常一律返回 None。

    没有 ``tv`` 的历史 token 按 0 处理：升级瞬间用户不会被集体踢下线，而一旦
    用户改过密码（版本号变 1），那些老 token 就再也对不上。
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        subject = payload.get("sub")
        if subject is None:
            return None
        try:
            version = int(payload.get("tv") or 0)
        except (TypeError, ValueError):
            return None
        return int(subject), version
    except (JWTError, ValueError):
        return None


def _token_subject(token: str) -> int | None:
    claims = _token_claims(token)
    return claims[0] if claims else None


def _version_matches(user: User, version: int) -> bool:
    return int(user.token_version or 0) == version


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已过期，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    claims = _token_claims(token)
    if claims is None:
        raise credentials_exception
    user_id, version = claims
    user = db.get(User, user_id)
    if user is None:
        raise credentials_exception
    # 改密/重置后版本号自增，旧 token 在这里被拒——「踢掉被盗会话」的唯一入口。
    if not _version_matches(user, version):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="密码已变更，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被停用")
    return user


def user_from_token(db: Session, token: str) -> User | None:
    """按 token 取可用用户，取不到返回 None。

    给「浏览器直接发起的图片请求」用：<img> 带不了 Authorization 头，
    只能用 query 里的 token，失败时由调用方决定回 401 还是 404。
    """
    claims = _token_claims(token)
    if claims is None:
        return None
    user_id, version = claims
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        return None
    if not _version_matches(user, version):
        return None
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可以访问")
    return current_user
