"""失败计数限频（Redis）。

登录密码与邮箱验证码都是"可被穷举的短凭据"：6 位数字十位数级别的空间，
不限次就可以在有效期内试完。这里只做一件事——按身份累计失败次数，到上限后
在一段窗口里拒绝请求。

Redis 不可用时**放行**（fail-open）：限频是加固手段，不该在缓存故障时把
登录整条链路一起拖垮；同时留 warning 供运维发现。真正的密码校验始终在。
"""

from __future__ import annotations

import hashlib
import logging

from app.core.config import settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


def _key(scope: str, identity: str) -> str:
    # 身份里含邮箱和 IP，哈希后入 key，避免把明文邮箱写进 Redis。
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"{settings.redis_key_prefix}:throttle:{scope}:{digest}"


def failure_count(scope: str, identity: str) -> int:
    try:
        return int(redis_client.get(_key(scope, identity)) or 0)
    except Exception:
        logger.warning("读取限频计数失败：scope=%s", scope, exc_info=True)
        return 0


def retry_after(scope: str, identity: str) -> int:
    """剩余锁定秒数；用于提示"请 N 分钟后再试"。"""
    try:
        return max(int(redis_client.ttl(_key(scope, identity)) or 0), 0)
    except Exception:
        return 0


def record_failure(scope: str, identity: str, window_seconds: int) -> int:
    """记一次失败并返回当前累计次数；TTL 每次刷新，窗口从最后一次失败算起。"""
    key = _key(scope, identity)
    try:
        with redis_client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds)
            count, _ = pipe.execute()
        return int(count or 0)
    except Exception:
        logger.warning("记录限频失败计数出错：scope=%s", scope, exc_info=True)
        return 0


def clear(scope: str, identity: str) -> None:
    try:
        redis_client.delete(_key(scope, identity))
    except Exception:
        logger.warning("清理限频计数失败：scope=%s", scope, exc_info=True)


def lock_remaining_minutes(scope: str, identity: str) -> int:
    """给用户看的剩余时间，向上取整到分钟，至少 1 分钟。"""
    return max((retry_after(scope, identity) + 59) // 60, 1)
