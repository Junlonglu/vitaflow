"""服务间回调用的一次性票据。

**问题**：后端 → Node 的请求里写死了 `user_id` / `resume_id`，Node 回调后端时
再原样带回来，双方靠一个全局静态令牌互认。于是任何拿到该令牌的人（端口泄漏、
日志泄漏、同网段进程）都能声称自己是任意用户：读他的信息库、给他记账（RF-008）。
静态令牌证明的是「你是 Node」，证明不了「你正在替哪个用户办哪一轮事」。

**做法**：后端在发起回合时签发一张短期票据，把「这一轮属于谁、哪份简历、哪条
消息」签进去：

    v1.<user_id>.<resume_id>.<turn_id>.<exp>.<hmac-sha256>

Node 把票据原样放进每次回调的 ``x-turn-ticket`` 头，后端校验签名与有效期，
再比对**请求体里的 user_id / resume_id 是否等于票据里的**——对不上就是越权，
403 而不是静默降级。票据本身不含敏感信息（只有四个整数与过期时间），
唯一性来自 HMAC；密钥由 JWT 密钥派生，轮换密钥即吊销全部未过期票据。

**为什么不是 JWT**：不需要可读的声明、不需要多算法协商，也不需要被第三方校验。
一段定长字符串 + 一次 HMAC 足够，而且可以逐字符拒绝任何不合形状的输入。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

from app.core import contracts
from app.core.config import settings

TICKET_VERSION = "v1"
TICKET_HEADER = "x-turn-ticket"


class TicketError(Exception):
    """票据不可用。``reason`` 直接面向运维日志，不返回给调用方。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TurnTicket:
    user_id: int
    resume_id: int | None
    turn_id: int | None
    expires_at: int

    def scope_problems(self, *, user_id: int | None, resume_id: int | None = None) -> str:
        """票据作用域与请求体是否一致；一致返回空串。

        只比对请求里**带了**的维度：``resume_id`` 可以为空（不涉及简历的回合），
        这时票据里的 resume 也不该拦。但 ``user_id`` 必须永远一致——钱和隐私都
        挂在它上面。
        """
        if user_id is not None and int(user_id) != self.user_id:
            return f"票据属于用户 {self.user_id}，请求声称是 {user_id}"
        if resume_id is not None and self.resume_id is not None and int(resume_id) != self.resume_id:
            return f"票据绑定的简历是 {self.resume_id}，请求声称是 {resume_id}"
        return ""


def _signing_key() -> bytes:
    # 从 JWT 密钥派生而不是再加一个环境变量：少一个必须同步的配置，
    # 且轮换登录密钥时票据一并失效——这两者的安全等级本来就一致。
    return hmac.new(
        (settings.jwt_secret_key or "").encode("utf-8"),
        b"vitaflow::service-ticket",
        hashlib.sha256,
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_ticket(
    *,
    user_id: int,
    resume_id: int | None = None,
    turn_id: int | None = None,
    ttl_seconds: int | None = None,
    now: int | None = None,
) -> str:
    """签发一张票据。``turn_id`` 为空的回合（不涉及对话消息）也能签。"""
    issued_at = int(now if now is not None else time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else contracts.SERVICE_TICKET_TTL_SECONDS)
    expires_at = issued_at + ttl
    payload = ".".join(
        [
            TICKET_VERSION,
            str(int(user_id)),
            "" if resume_id is None else str(int(resume_id)),
            "" if turn_id is None else str(int(turn_id)),
            str(expires_at),
        ]
    )
    signature = _b64(hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def _parse_int(text: str, field: str) -> int:
    if not text.isdigit():
        raise TicketError(f"{field} 不是整数")
    return int(text)


def verify_ticket(ticket: str, *, now: int | None = None) -> TurnTicket:
    """校验并解出作用域；任何不合形状的输入都抛 :class:`TicketError`。"""
    if not ticket or not isinstance(ticket, str):
        raise TicketError("缺少回合票据")
    parts = ticket.split(".")
    if len(parts) != 6:
        raise TicketError("票据格式不正确")
    version, raw_user, raw_resume, raw_turn, raw_exp, raw_sig = parts
    if version != TICKET_VERSION:
        raise TicketError(f"票据版本不支持：{version}")

    payload = ".".join([version, raw_user, raw_resume, raw_turn, raw_exp])
    expected = _b64(hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest())
    # constant-time：比较的是签名，泄漏「前几位对上了」没有意义但也没必要给。
    if not hmac.compare_digest(expected, raw_sig):
        raise TicketError("票据签名不匹配")

    user_id = _parse_int(raw_user, "user_id")
    resume_id = _parse_int(raw_resume, "resume_id") if raw_resume else None
    turn_id = _parse_int(raw_turn, "turn_id") if raw_turn else None
    expires_at = _parse_int(raw_exp, "exp")
    current = int(now if now is not None else time.time())
    if expires_at <= current:
        raise TicketError(f"票据已过期（{expires_at} <= {current}）")
    return TurnTicket(user_id=user_id, resume_id=resume_id, turn_id=turn_id, expires_at=expires_at)


def describe(ticket: str) -> dict[str, Any]:
    """把票据解成人可读的字段，仅供日志/排查；不校验签名。"""
    parts = (ticket or "").split(".")
    if len(parts) != 6:
        return {}
    return {
        "version": parts[0],
        "user_id": parts[1],
        "resume_id": parts[2] or None,
        "turn_id": parts[3] or None,
        "expires_at": parts[4],
    }
