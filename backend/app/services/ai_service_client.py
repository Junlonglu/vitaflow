"""Proxy client toward the Node AI service (pi-agent sidecar).

This is the single integration seam of the refactor: FastAPI forwards a chat
request to the AI service and relays its NDJSON event stream back to the
browser unchanged. The AI service owns the agent loop and the streamed text;
FastAPI owns auth, database and (in a later ticket) billing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings


def _extract_error(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        body = json.loads(text)
        return str(body.get("message") or body.get("detail") or text)
    except json.JSONDecodeError:
        return text


def _http_timeout() -> httpx.Timeout:
    # Generous read timeout: the AI service streams over a long agent turn.
    return httpx.Timeout(connect=5, read=180, write=10, pool=5)


async def ai_service_chat_events(
    message: str,
    system_prompt: str | None = None,
    context: dict[str, Any] | None = None,
    user_id: int | None = None,
    resume_id: int | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream NDJSON chat events from the AI service, yielding event dicts.

    The returned events match the frontend's existing stream contract
    (``start`` / ``phase`` / ``delta`` / ``result`` / ``error``), so the proxy is
    transparent to the client.

    ``context`` carries the resume snapshot, recent chat history and pending
    change summary -- the state the AI service renders into the prompt. It is
    optional: the skeleton can run with no context (pure dialogue).
    """
    if not settings.ai_service_token:
        yield {"type": "error", "message": "AI 服务令牌未配置，无法调用 AI 服务"}
        return

    payload: dict[str, Any] = {"message": message}
    if system_prompt:
        payload["system_prompt"] = system_prompt
    if context:
        payload["context"] = context
    # The AI service needs the identity to call back the validation endpoint when
    # its update_basics_field tool fires. Only the proxy knows the real user/resume.
    if user_id is not None:
        payload["user_id"] = user_id
    if resume_id is not None:
        payload["resume_id"] = resume_id

    url = f"{settings.ai_service_url.rstrip('/')}/v1/chat"
    headers = {"Authorization": f"Bearer {settings.ai_service_token}"}

    client = httpx.AsyncClient(transport=transport, timeout=_http_timeout())
    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                raw = await resp.aread()
                detail = _extract_error(raw)
                yield {
                    "type": "error",
                    "message": detail or f"AI 服务返回异常状态码 {resp.status_code}",
                }
                return
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
    except httpx.HTTPError as exc:
        yield {"type": "error", "message": f"AI 服务连接失败：{exc}"}
    except Exception as exc:  # defensive: never let a proxy failure bubble as a raw 500
        yield {"type": "error", "message": f"转发 AI 服务消息失败：{exc}"}
    finally:
        await client.aclose()
