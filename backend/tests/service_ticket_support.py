"""给「服务间回调」测试签发真实回合票据。

回调端点现在要求两样东西：服务令牌（证明你是 AI 服务）+ 回合票据（证明你正在
替这个用户办这一回合的事，RF-008）。测试里不要绕过校验——票据是真的签发再真的
校验，越权和过期这两条路径才有东西可测。
"""

from __future__ import annotations

from app.core.service_ticket import issue_ticket

SERVICE_TOKEN = "test-token"


def service_headers(
    user_id: int,
    resume_id: int | None = None,
    *,
    turn_id: int | None = None,
    token: str = SERVICE_TOKEN,
    ttl_seconds: int | None = None,
) -> dict[str, str]:
    return {
        "x-ai-service-token": token,
        "x-turn-ticket": issue_ticket(
            user_id=user_id,
            resume_id=resume_id,
            turn_id=turn_id,
            ttl_seconds=ttl_seconds,
        ),
    }
