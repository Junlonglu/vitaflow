"""Token accounting utilities (post-LangChain, ticket 09).

The estimator feeds Flow Points billing for one-shot tasks (generate/import):
actual provider usage arrives from the Node AI service on the task result event,
so FastAPI only needs to normalize/merge the reported numbers into the AiTask.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any


_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def stringify_for_tokens(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except TypeError:
            value = value.model_dump()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def estimate_tokens(value: Any) -> int:
    text = stringify_for_tokens(value)
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    non_cjk_text = _CJK_RE.sub("", text)
    non_cjk_compact = re.sub(r"\s+", "", non_cjk_text)
    # Chinese text is usually close to one token per character; English/code/JSON
    # is closer to four compact characters per token. This is intentionally
    # conservative for billing and monitoring when providers do not return usage.
    return max(1, cjk_count + math.ceil(len(non_cjk_compact) / 4))


def build_token_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    calls: list[dict[str, Any]] | None = None,
    *,
    estimated: bool = True,
) -> dict[str, Any]:
    input_value = max(0, int(input_tokens or 0))
    output_value = max(0, int(output_tokens or 0))
    return {
        "input_tokens": input_value,
        "output_tokens": output_value,
        "total_tokens": input_value + output_value,
        "estimated": estimated,
        "calls": calls or [],
    }


def normalize_token_usage(input_data: dict[str, Any] | None, fallback_total: int = 0) -> dict[str, Any]:
    data = input_data or {}
    raw = data.get("token_usage") if isinstance(data, dict) else None
    try:
        request_input_tokens = max(0, int(data.get("request_input_tokens") or 0)) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        request_input_tokens = 0
    if not isinstance(raw, dict):
        total = max(0, int(fallback_total or 0))
        if not total:
            return build_token_usage(request_input_tokens, 0, [], estimated=True) if request_input_tokens else build_token_usage()
        input_tokens = min(request_input_tokens, total) if request_input_tokens else 0
        output_tokens = max(0, total - input_tokens) if input_tokens else total
        return build_token_usage(input_tokens, output_tokens, [], estimated=True)
    input_tokens = int(raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    total_tokens = int(raw.get("total_tokens") or 0)
    if request_input_tokens and input_tokens <= 0:
        input_tokens = request_input_tokens
    if total_tokens and not input_tokens and not output_tokens:
        output_tokens = total_tokens
    elif total_tokens and input_tokens + output_tokens != total_tokens:
        output_tokens = max(0, total_tokens - input_tokens)
    elif not total_tokens:
        total_tokens = input_tokens + output_tokens
    normalized_total = max(0, total_tokens if total_tokens else input_tokens + output_tokens)
    normalized_total = max(normalized_total, max(0, input_tokens) + max(0, output_tokens))
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": normalized_total,
        "estimated": bool(raw.get("estimated", True)),
        "calls": raw.get("calls") if isinstance(raw.get("calls"), list) else [],
    }


def merge_token_usage(input_data: dict[str, Any] | None, token_usage: dict[str, Any]) -> dict[str, Any]:
    data = dict(input_data or {})
    data["token_usage"] = normalize_token_usage({"token_usage": token_usage}, token_usage.get("total_tokens", 0))
    return data
