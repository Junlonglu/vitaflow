"""日志与对外错误信息的密钥脱敏。

BYOK 的明文 API Key 只应存在于「后端 → 模型厂商」这一次请求的内存里。
但 httpx 异常、厂商响应体、异常栈、APM 与结构化日志都可能把它带出来，
因此在 logging 出口统一挂一层 filter，并提供 ``redact`` 给需要回显错误
文本的接口显式调用。
"""
from __future__ import annotations

import logging
import re
import traceback
from typing import Any, Iterable

_MASK = "[REDACTED]"

# 只匹配「键名 + 分隔 + 值」的常见形态，避免把普通英文句子误伤。
_PATTERNS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (
        re.compile(
            r"(?i)(authorization|x-api-key|api[_-]?key|access[_-]?token|secret)"
            r"(\"?\s*[:=]\s*\"?)([^\s,;\"'&}\]\)]+)"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{_MASK}",
    ),
    (
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{6,}"),
        lambda m: f"{m.group(0).split()[0]} {_MASK}",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), lambda _m: _MASK),
    (
        re.compile(r"(?i)([?&](?:key|api_key|token|access_token)=)([^&\s\"']+)"),
        lambda m: f"{m.group(1)}{_MASK}",
    ),
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """先精确剔除已知明文，再按通用形态兜底。"""
    if not text:
        return text
    value = text
    for secret in sorted((item for item in secrets if item and len(item) >= 4), key=len, reverse=True):
        value = value.replace(secret, _MASK)
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class SecretRedactingFilter(logging.Filter):
    """挂在每个 handler 上：无论 logger 是否 propagate，输出前都会过一遍。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # 先把模板与参数合并成最终文本再脱敏。直接改 msg 会吃掉 %s 占位符，
        # 同时改 args 又会让 getMessage() 因参数数量不匹配而抛 TypeError。
        if record.args:
            try:
                merged = str(record.msg) % record.args
            except (TypeError, ValueError, KeyError):
                merged = f"{record.msg} {record.args}"
            record.msg = redact(merged)
            record.args = ()
        else:
            record.msg = redact(str(record.msg))
        if record.exc_info:
            # 预先生成 exc_text，Formatter 会直接采用，避免原始栈绕过脱敏。
            try:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            except Exception:
                record.exc_text = _MASK
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True
