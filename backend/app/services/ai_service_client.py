"""Proxy client toward the Node AI service (pi-agent sidecar).

This is the single integration seam of the refactor: FastAPI forwards a chat
request to the AI service and relays its NDJSON event stream back to the
browser unchanged. The AI service owns the agent loop and the streamed text;
FastAPI owns auth, database and (in a later ticket) billing.

Building the outgoing payload is also this client's job, including resolving
uploaded image attachments into base64 ``ImageContent`` blocks so the Node side
can attach them natively (ticket 06). FastAPI owns the storage read; the Node
service stays free of any MinIO/OSS knowledge.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx

from app.core.config import settings
from app.core.service_ticket import issue_ticket
from app.services.storage.storage_service import read_uploaded_file


# 上传入口只接受这三种图片；附件体的 content_type 是客户端给的，这里再收一次，
# 免得任意 mime 被塞进 provider 请求。
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


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
    # 读超时来自 core.contracts 的超时链：必须比 Node 的回合总时长多出一个余量，
    # 否则 Node 还没来得及发自己的错误事件，代理就先断流，前端只能看到
    # 「没有结束原因的断流」（RF-033）。三档关系由启动自检校验。
    return httpx.Timeout(
        connect=5,
        read=settings.ai_proxy_read_timeout_ms / 1000,
        write=10,
        pool=5,
    )


async def _attachment_image_blocks(
    attachments: list[dict[str, Any]] | None,
    allowed_object_names: set[str],
) -> list[dict[str, str]]:
    """Resolve uploaded image attachments into pi-agent ``ImageContent`` blocks.

    Each attachment carries a MinIO/OSS ``object_name``; we read the bytes and
    base64-encode them so the Node AI service can attach them natively (pi builds
    ``data:<mime>;base64,<data>`` for the provider). Non-image, or an attachment
    we cannot resolve, is skipped — a bad image must never break a text-only turn.

    ``allowed_object_names`` comes from the caller's ownership lookup. The
    attachment payload is client-controlled, so an ``object_name`` that is not in
    this set is dropped instead of read: without it, editing the request body
    would let a user feed another user's uploads to the model.
    """
    blocks: list[dict[str, str]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        content_type = (item.get("content_type") or "").strip().lower()
        object_name = item.get("object_name")
        # Only images the user actually uploaded reach this point; the url is the
        # frontend's marker of an image attachment, so guard on it too.
        if not url or content_type not in ALLOWED_IMAGE_TYPES or not object_name:
            continue
        if object_name not in allowed_object_names:
            continue
        # content_type is already guaranteed image/* above, so carry it through
        # unchanged; the storage-read content type is not needed.
        # 存储读是同步 HTTP（MinIO/OSS）。这是 async 生成器内部，直接调用会把
        # 事件循环卡在这几秒里，首字延迟跟着一起涨；丢到线程池当 IO 边界处理。
        try:
            content, _ = await anyio.to_thread.run_sync(read_uploaded_file, object_name)
        except Exception:
            continue
        blocks.append({"type": "image", "data": base64.b64encode(content).decode("ascii"), "mimeType": content_type})
    return blocks


async def ai_service_chat_events(
    message: str,
    system_prompt: str | None = None,
    context: dict[str, Any] | None = None,
    user_id: int | None = None,
    resume_id: int | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    attachments: list[dict[str, Any]] | None = None,
    model_ref: str | None = None,
    model_override: dict[str, Any] | None = None,
    skills: list[dict[str, str]] | None = None,
    allowed_attachment_names: set[str] | None = None,
    turn_id: int | None = None,
    session_id: int | None = None,
    trigger: str | None = None,
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
        # 回合票据：把「这一轮属于谁」签进去。Node 把它放进每次回调的
        # `x-turn-ticket` 头，后端据此拒绝「令牌合法但身份不符」的请求（RF-008）。
        # 只在有 user_id 时签——没有身份的回合本来就没有可越权的对象。
        payload["turn_ticket"] = issue_ticket(user_id=user_id, resume_id=resume_id, turn_id=turn_id)
    if resume_id is not None:
        payload["resume_id"] = resume_id
    # Uploaded images → base64 ImageContent blocks so the agent sees them natively
    # (ticket 06). Absent attachments, no "images" key is sent — text-only unchanged.
    # ``allowed_attachment_names`` is required-ish: omitting it means "nothing is
    # owned", so a forgotten call site drops images instead of leaking them.
    image_blocks = await _attachment_image_blocks(attachments, allowed_attachment_names or set())
    if image_blocks:
        payload["images"] = image_blocks
    # pi model ref (ticket 07): routes the turn to the mapped model in the Node
    # service's models.json and keys the per-model usage report for billing.
    if model_ref:
        payload["model"] = model_ref
    # BYOK: the Node service registers this provider for the turn and skips
    # platform billing. The plaintext key only exists in this service-to-service
    # request, never in a browser response or a database row.
    if model_override:
        payload["model_override"] = model_override
    # Enabled user skills are appended to the system prompt by the Node side.
    if skills:
        payload["skills"] = skills
    # 轨迹关联键：ai-service 拿不到数据库 id，只有代理知道这一轮回复的是哪条消息、
    # 属于哪个会话。trigger 让"重新生成"和普通提问在轨迹里可区分。
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if session_id is not None:
        payload["session_id"] = session_id
    if trigger:
        payload["trigger"] = trigger

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


async def ai_service_task_events(
    task_type: str,
    input_payload: dict[str, Any],
    *,
    model_ref: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream NDJSON events for a one-shot task (generate / import, ticket 09).

    Unlike the chat turn, a task carries no resume tools and no callback
    billing: the AI service parses the model's JSON output itself and echoes
    the aggregated per-model token usage back on the ``result`` event, so
    FastAPI settles Flow Points synchronously in the same request.
    """
    if not settings.ai_service_token:
        yield {"type": "error", "message": "AI 服务令牌未配置，无法调用 AI 服务"}
        return

    payload: dict[str, Any] = {"task": task_type, "input": input_payload}
    if model_ref:
        payload["model"] = model_ref

    url = f"{settings.ai_service_url.rstrip('/')}/v1/task"
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
    except Exception as exc:
        yield {"type": "error", "message": f"调用 AI 服务任务失败：{exc}"}
    finally:
        await client.aclose()


async def ai_service_task_result(
    task_type: str,
    input_payload: dict[str, Any],
    *,
    model_ref: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run a one-shot task to completion and return ``(result_data, usage)``.

    Raises :class:`AppException` with the AI service's message on failure so the
    caller can settle the AiTask as failed (points refund path).
    """
    from app.core.exceptions import AppException

    data: dict[str, Any] | None = None
    usage: list[dict[str, Any]] = []
    async for event in ai_service_task_events(task_type, input_payload, model_ref=model_ref, transport=transport):
        event_type = event.get("type")
        if event_type == "result":
            data = event.get("data") or {}
            usage = event.get("usage") or []
        elif event_type == "error":
            raise AppException(str(event.get("message") or "AI 任务失败"), 502)
    if data is None:
        raise AppException("AI 服务未返回任务结果", 502)
    return data, usage


def ai_service_task_result_sync(
    task_type: str,
    input_payload: dict[str, Any],
    *,
    model_ref: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Sync bridge for threadpool (``def``) routes: run the async one-shot task on a fresh loop."""
    return asyncio.run(ai_service_task_result(task_type, input_payload, model_ref=model_ref))


def _catalog_headers() -> dict[str, str]:
    if not settings.ai_service_token:
        from app.core.exceptions import AppException

        raise AppException("AI 服务令牌未配置，无法访问模型目录", 503)
    return {"Authorization": f"Bearer {settings.ai_service_token}"}


def _catalog_url(path: str) -> str:
    return f"{settings.ai_service_url.rstrip('/')}{path}"


def call_catalog(method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
    """Sync catalog proxy for admin routes (ticket 11).

    Returns the parsed JSON body; raises :class:`AppException` carrying the AI
    service's message on any non-2xx so the admin UI sees the real reason.
    """
    from app.core.exceptions import AppException

    with httpx.Client(timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5)) as client:
        resp = client.request(method, _catalog_url(path), json=json_body, headers=_catalog_headers())
    if resp.status_code >= 400:
        raise AppException(_extract_error(resp.content) or f"模型目录请求失败（{resp.status_code}）", 502)
    return resp.json()


async def afetch_catalog_providers(*, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """Async catalog fetch for capability derivation (multimodal guard).

    Raises on transport/auth failure so callers can decide their own fallback.
    """
    body = await afetch_catalog_payload(transport=transport)
    return body.get("providers") or []


async def afetch_catalog_payload(*, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Full catalog body (providers + routing_groups) for capability derivation."""
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5)) as client:
        resp = await client.get(_catalog_url("/v1/catalog"), headers=_catalog_headers())
    if resp.status_code >= 400:
        from app.core.exceptions import AppException

        raise AppException(_extract_error(resp.content) or f"模型目录请求失败（{resp.status_code}）", 502)
    return resp.json()
