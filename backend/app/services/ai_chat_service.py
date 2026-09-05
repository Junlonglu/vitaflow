from __future__ import annotations

import re
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import AppException
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume
from app.models.resume import ResumeVersion
from app.models.user import User
from app.schemas.ai_chat import AiChatMessageOut
from app.services.ai_config_service import (
    capability_supports_images,
    get_catalog_capabilities,
    resolve_chat_ai_config,
)
from app.services.ai_service_client import ai_service_chat_events
from app.services.flow_points_service import precheck_flow_points
from app.services.resume_normalization import localize_ai_text, strip_thinking_text


SECTION_LABELS = {
    "basics": "基本信息",
    "summary": "个人简介",
    "education": "教育经历",
    "skills": "专业技能",
    "work": "实习/工作经历",
    "projects": "项目经历",
    "awards": "荣誉奖项",
}
def _attachment_data(attachment: Any) -> dict[str, Any]:
    if hasattr(attachment, "model_dump"):
        return attachment.model_dump()
    if isinstance(attachment, dict):
        return attachment
    return {}


def _message_out(message: AiChatMessage) -> AiChatMessageOut:
    return AiChatMessageOut.model_validate(
        {
            "id": message.id,
            "role": message.role,
            "content": strip_thinking_text(message.content or "") if message.role == "assistant" else message.content,
            "attachments": message.attachments or [],
            "suggestions": message.suggestions or [],
            "optimized_resume_data": message.optimized_resume_data,
            "tool_trace": message.tool_trace,
            "action_status": message.action_status or "none",
            "create_time": message.create_time,
        }
    )


def _clip_text(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _resume_intent_summary(resume_data: dict[str, Any] | None) -> dict[str, Any]:
    data = resume_data or {}
    basics = data.get("basics") if isinstance(data.get("basics"), dict) else {}
    sections: list[str] = []
    for key in ("summary", "education", "skills", "work", "projects", "awards", "custom_sections"):
        value = data.get(key)
        if isinstance(value, list) and value:
            sections.append(key)
        elif isinstance(value, dict) and any(str(item or "").strip() for item in value.values()):
            sections.append(key)
    return {
        "name": basics.get("name", ""),
        "target_position": basics.get("title", ""),
        "sections": sections,
        "layout_order": (data.get("layout") or {}).get("section_order", []) if isinstance(data.get("layout"), dict) else [],
    }



def get_or_create_chat_session(db: Session, user_id: int, resume_id: int) -> AiChatSession:
    session = db.scalar(
        select(AiChatSession)
        .where(AiChatSession.user_id == user_id, AiChatSession.resume_id == resume_id)
        .order_by(AiChatSession.id.asc())
    )
    if session:
        return session
    session = AiChatSession(user_id=user_id, resume_id=resume_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_chat_messages(db: Session, user_id: int, resume_id: int, limit: int = 80) -> list[AiChatMessageOut]:
    session = get_or_create_chat_session(db, user_id, resume_id)
    messages = list(
        db.scalars(
            select(AiChatMessage)
            .where(AiChatMessage.session_id == session.id, AiChatMessage.user_id == user_id)
            .order_by(AiChatMessage.id.desc())
            .limit(limit)
        )
    )
    return [_message_out(message) for message in reversed(messages)]


def clear_chat_messages(db: Session, user_id: int, resume_id: int) -> None:
    session = get_or_create_chat_session(db, user_id, resume_id)
    db.execute(delete(AiChatMessage).where(AiChatMessage.session_id == session.id, AiChatMessage.user_id == user_id))
    session.update_time = datetime.now()
    db.add(session)
    db.commit()



def _save_assistant_message(
    db: Session,
    session: AiChatSession,
    user: User,
    resume: Resume,
    reply: str,
    suggestions: list[str],
    optimized_resume_data: dict[str, Any] | None,
    tool_trace: list[dict[str, Any]] | None = None,
) -> AiChatMessageOut:
    reply = strip_thinking_text(localize_ai_text(reply or "")).strip()
    assistant_message = AiChatMessage(
        session_id=session.id,
        user_id=user.id,
        resume_id=resume.id,
        role="assistant",
        content=reply,
        suggestions=suggestions,
        optimized_resume_data=optimized_resume_data,
        tool_trace=tool_trace or None,
        action_status="pending" if optimized_resume_data else "none",
    )
    session.update_time = datetime.now()
    db.add(assistant_message)
    db.add(session)
    db.commit()
    db.refresh(assistant_message)
    return _message_out(assistant_message)


def build_ai_service_context(
    db: Session,
    user: User,
    resume: Resume,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build the context block injected into the AI service prompt.

    Mirrors the shape FastAPI reads from MySQL: resume snapshot, the recent chat
    history (capped at ``settings.ai_service_context_messages``) and the
    outstanding pending-change summary. The AI service renders this into the
    system prompt and never touches MySQL itself, keeping the Node session
    stateless while MySQL remains the single source of truth.
    """
    context_limit = limit or max(1, min(int(settings.ai_service_context_messages or 12), 40))
    history = list_chat_messages(db, user.id, resume.id, limit=context_limit + 8)
    pending_change = db.scalar(
        select(AiChatMessage)
        .where(
            AiChatMessage.user_id == user.id,
            AiChatMessage.resume_id == resume.id,
            AiChatMessage.role == "assistant",
            AiChatMessage.action_status == "pending",
            AiChatMessage.optimized_resume_data.is_not(None),
        )
        .order_by(AiChatMessage.id.desc())
    )
    resume_data = resume.resume_data or {}
    basics = resume_data.get("basics") if isinstance(resume_data.get("basics"), dict) else {}
    return {
        "resume": {
            "id": resume.id,
            "title": resume.title,
            "target_position": basics.get("title", ""),
            "summary": _resume_intent_summary(resume.resume_data),
        },
        "history": [{"role": item.role, "content": item.content} for item in history[-context_limit:]],
        "pending_change": {
            "exists": pending_change is not None,
            "message_id": pending_change.id if pending_change else None,
            "summary": pending_change.suggestions if pending_change else [],
        },
    }


def _resolve_turn_model_config(db: Session, user: User, model_config_id: int | None):
    try:
        return resolve_chat_ai_config(db, user, model_config_id)
    except AppException:
        return None


def _turn_image_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    # Match the proxy's drop predicate (_attachment_image_blocks): only a real
    # image (url + image content_type) counts, so a stray non-image attachment
    # never rejects what is otherwise a text-only turn.
    return [
        item
        for item in (attachments or [])
        if isinstance(item, dict)
        and item.get("url")
        and (item.get("content_type") or "").startswith("image/")
    ]


def _reject_turn_images(model_config, supports_images: bool, image_attachments: list[dict[str, Any]]) -> str | None:
    # Multimodal guard: only forward uploaded images when the selected model can
    # see them (ADR-0005: capability derives from the AI service catalog). A
    # text-only model silently drops image blocks, which would make the user
    # think their picture was used — so refuse up front instead.
    if image_attachments and model_config and not supports_images:
        return "当前模型未开启多模态能力，暂不支持在对话中上传图片"
    return None


def _precheck_turn_points(db: Session, user: User, model_config) -> None:
    # Charge_on_finish parity: precheck the points_per_call floor up front so an
    # empty balance fails the turn before any model call; the exact token-based
    # charge settles later via the /service/usage callback.
    if model_config:
        precheck_flow_points(
            db,
            user,
            "ai_chat",
            input_tokens=0,
            pricing_override=model_config.chat_billing_override(),
        )


async def _stream_ai_service_reply(
    db: Session,
    user: User,
    resume: Resume,
    session: AiChatSession,
    user_message: AiChatMessage,
    model_config,
) -> AsyncIterator[dict[str, Any]]:
    """Relay one AI-service turn for an already-persisted user message.

    Builds the context from MySQL (resume snapshot, recent history, pending
    change), relays the AI service's NDJSON stream, accumulates the assistant
    text and, on ``result``, persists the assistant message and rewrites
    ``result.data.assistant_message`` with the DB row. MySQL stays the single
    source of truth and the AI service stays stateless. Shared by send and
    regenerate (ticket 09).
    """
    context = build_ai_service_context(
        db,
        user,
        resume,
        # 历史注入条数跟随所选模型策略；未选策略时回退全局默认。
        limit=model_config.context_messages if model_config else None,
    )
    text_parts: list[str] = []
    events = ai_service_chat_events(
        user_message.content or "",
        context=context,
        user_id=user.id,
        resume_id=resume.id,
        attachments=user_message.attachments or [],
        model_ref=model_config.model_ref if model_config else None,
    )
    try:
        async for event in events:
            event_type = event.get("type")
            if event_type == "delta":
                text_parts.append(event.get("text", ""))
                yield event
            elif event_type == "result":
                reply = "".join(text_parts)
                # The agent called update_basics_field and the Node side echoed the
                # validated payload back on the result event. Persist it as a pending
                # change so the existing /decision route can apply/reject it.
                data = event.get("data") or {}
                pending = data.get("pending_change")
                # Tool steps the agent executed this turn (curated labels only) are
                # persisted with the message so history shows the same steps the
                # user watched live.
                trace = data.get("tool_trace")
                tool_trace = [item for item in trace if isinstance(item, dict)] if isinstance(trace, list) else None
                if isinstance(pending, dict) and pending.get("optimized_resume_data"):
                    assistant = _save_assistant_message(
                        db,
                        session,
                        user,
                        resume,
                        reply,
                        list(pending.get("suggestions") or []),
                        pending.get("optimized_resume_data"),
                        tool_trace,
                    )
                else:
                    assistant = _save_assistant_message(db, session, user, resume, reply, [], None, tool_trace)
                yield {"type": "result", "data": {"assistant_message": assistant.model_dump(mode="json")}}
            else:
                yield event
    except Exception as exc:
        yield {"type": "error", "message": f"AI 对话处理失败：{exc}"}


async def stream_ai_service_chat(
    db: Session,
    user: User,
    resume: Resume,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat turn backed by the Node AI service.

    The chat model config (selected or default) drives the multimodal guard,
    the model mapping sent to the Node service (``model_ref``) and the
    points_per_call precheck (ticket 07). It is resolved tolerantly: with no
    model configured at all (pure faux demo) the turn still runs, unmapped and
    unbilled.
    """
    message = (content or "").strip()
    if not message:
        yield {"type": "error", "message": "请输入要咨询的问题"}
        return

    model_config = _resolve_turn_model_config(db, user, model_config_id)
    image_attachments = _turn_image_attachments(attachments)
    supports_images = capability_supports_images(
        await get_catalog_capabilities(), model_config.model_ref if model_config else None
    )
    image_issue = _reject_turn_images(model_config, supports_images, image_attachments)
    if image_issue:
        yield {"type": "error", "message": image_issue}
        return
    _precheck_turn_points(db, user, model_config)

    session = get_or_create_chat_session(db, user.id, resume.id)
    user_message = AiChatMessage(
        session_id=session.id,
        user_id=user.id,
        resume_id=resume.id,
        role="user",
        content=message,
        attachments=attachments or [],
        suggestions=[],
        optimized_resume_data=None,
    )
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    async for event in _stream_ai_service_reply(db, user, resume, session, user_message, model_config):
        yield event


async def stream_ai_service_regenerate(
    db: Session,
    user: User,
    resume: Resume,
    message_id: int,
    override_content: str | None = None,
    override_attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Regenerate the reply to a user message via the Node AI service (ticket 09).

    Keeps the legacy semantics: the target must be the caller's own user
    message, every later message in the session is dropped, optional
    content/attachment overrides are applied in place, then the turn re-runs
    through the same relay as a fresh send.
    """
    user_message = db.get(AiChatMessage, message_id)
    if (
        not user_message
        or user_message.user_id != user.id
        or user_message.resume_id != resume.id
        or user_message.role != "user"
    ):
        yield {"type": "error", "message": "只能重新生成自己的用户消息"}
        return

    applying_overrides = override_content is not None or override_attachments is not None
    next_content = ""
    next_attachments: list[dict[str, Any]] = []
    if applying_overrides:
        next_content = (override_content if override_content is not None else user_message.content or "").strip()
        next_attachments = override_attachments if override_attachments is not None else (user_message.attachments or [])
        if not next_content and not next_attachments:
            yield {"type": "error", "message": "重新生成内容不能为空"}
            return

    # 守卫与点数预检必须先于删除：余额不足、图片不被模型支持等失败不能
    # 连带把后面的消息历史丢掉。
    model_config = _resolve_turn_model_config(db, user, model_config_id)
    effective_attachments = next_attachments if applying_overrides else (user_message.attachments or [])
    supports_images = capability_supports_images(
        await get_catalog_capabilities(), model_config.model_ref if model_config else None
    )
    image_issue = _reject_turn_images(model_config, supports_images, _turn_image_attachments(effective_attachments))
    if image_issue:
        yield {"type": "error", "message": image_issue}
        return
    _precheck_turn_points(db, user, model_config)

    if applying_overrides:
        user_message.content = next_content or "请结合我上传的图片分析简历。"
        user_message.attachments = [_attachment_data(item) for item in next_attachments]
        flag_modified(user_message, "attachments")
        db.add(user_message)

    db.execute(
        delete(AiChatMessage).where(
            AiChatMessage.session_id == user_message.session_id,
            AiChatMessage.user_id == user.id,
            AiChatMessage.resume_id == resume.id,
            AiChatMessage.id > user_message.id,
        )
    )
    session = get_or_create_chat_session(db, user.id, resume.id)
    session.update_time = datetime.now()
    db.add(session)
    db.commit()
    db.refresh(user_message)

    yield {"type": "start", "data": {"user_message_id": user_message.id}}
    async for event in _stream_ai_service_reply(db, user, resume, session, user_message, model_config):
        yield event



def _latest_pending_change(db: Session, user_id: int, resume_id: int) -> AiChatMessage | None:
    return db.scalar(
        select(AiChatMessage)
        .where(
            AiChatMessage.user_id == user_id,
            AiChatMessage.resume_id == resume_id,
            AiChatMessage.role == "assistant",
            AiChatMessage.action_status == "pending",
            AiChatMessage.optimized_resume_data.is_not(None),
        )
        .order_by(AiChatMessage.id.desc())
    )


def _merge_confirmed_resume_data(
    current_data: dict[str, Any] | None,
    optimized_data: dict[str, Any],
    replace_sections: set[str] | None = None,
) -> dict[str, Any]:
    """Apply the proposed content without dropping omitted resume configuration."""
    current = deepcopy(current_data or {})
    optimized = _unwrap_resume_data(optimized_data)
    merged = {**current, **optimized}
    replace_sections = replace_sections or set()

    for key in ("basics", "layout"):
        current_section = current.get(key)
        optimized_section = optimized.get(key)
        if (
            key not in replace_sections
            and isinstance(current_section, dict)
            and isinstance(optimized_section, dict)
        ):
            merged[key] = {**current_section, **optimized_section}

    return merged


_RESUME_SECTION_KEYS = {
    "basics",
    "summary",
    "education",
    "skills",
    "work",
    "projects",
    "awards",
    "custom_sections",
    "layout",
}
_RESUME_WRAPPER_KEYS = ("optimized_resume_data", "resume_data", "data", "result", "resume")


def _unwrap_resume_data(value: dict[str, Any]) -> dict[str, Any]:
    """Remove occasional model response wrappers before persisting resume JSON."""
    candidate: Any = deepcopy(value)
    for _ in range(5):
        if not isinstance(candidate, dict):
            raise AppException("AI 修改结果不是有效的简历数据")
        if _RESUME_SECTION_KEYS.intersection(candidate):
            return candidate
        nested = next(
            (
                candidate.get(key)
                for key in _RESUME_WRAPPER_KEYS
                if isinstance(candidate.get(key), dict)
            ),
            None,
        )
        if nested is None:
            break
        candidate = nested
    raise AppException("AI 修改结果缺少可写入的简历模块，请重新生成")


_NUMERIC_CLAIM_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:%|\+)?")
_NUMERIC_SKIP_KEYS = {"id", "row", "order", "icon", "field_config", "layout"}


def _numeric_claim_variants(token: str) -> set[str]:
    """Treat common date normalizations as the same fact, e.g. 2025.9 -> 2025-09."""
    token = token.strip()
    if not token:
        return set()
    variants = {token}
    bare = token.rstrip("%+")
    variants.add(bare)
    if "." in bare:
        for part in bare.split("."):
            if not part:
                continue
            variants.add(part)
            if part.isdigit():
                variants.add(str(int(part)))
                if len(part) == 1:
                    variants.add(f"0{part}")
    elif bare.isdigit():
        variants.add(str(int(bare)))
        if len(bare) == 1:
            variants.add(f"0{bare}")
    return variants


def _numeric_claims(value: Any, key: str | None = None) -> set[str]:
    """Collect user-visible numeric claims while ignoring structural configuration."""
    if key in _NUMERIC_SKIP_KEYS:
        return set()
    if isinstance(value, dict):
        claims: set[str] = set()
        for child_key, child_value in value.items():
            claims.update(_numeric_claims(child_value, str(child_key)))
        return claims
    if isinstance(value, list):
        claims: set[str] = set()
        for item in value:
            claims.update(_numeric_claims(item, key))
        return claims
    if isinstance(value, str):
        claims: set[str] = set()
        for token in _NUMERIC_CLAIM_RE.findall(value):
            claims.update(_numeric_claim_variants(token))
        return claims
    return set()


def _unverified_numeric_claims(payload: dict[str, Any], optimized_data: dict[str, Any]) -> set[str]:
    # Multimodal chat can extract dates, phone numbers, and other numeric facts
    # directly from the uploaded image. The text-only evidence check cannot see
    # those pixels, so do not treat image-derived numbers as hallucinations here.
    if any(isinstance(item, dict) and item.get("url") for item in payload.get("attachments") or []):
        return set()

    source = {
        "resume_data": payload.get("resume", {}).get("resume_data") or {},
        "history": payload.get("history") or [],
        "user_message": payload.get("user_message") or "",
    }
    return _numeric_claims(optimized_data) - _numeric_claims(source)


def _validated_optimized_data(
    payload: dict[str, Any],
    current_data: dict[str, Any] | None,
    optimized_data: dict[str, Any] | None,
    replace_sections: set[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if not optimized_data:
        return None, ""

    merged = _merge_confirmed_resume_data(
        current_data,
        optimized_data,
        replace_sections=replace_sections,
    )
    if merged == (current_data or {}):
        return None, "模型没有生成实际内容变化，未创建待确认修改。"
    if _unverified_numeric_claims(payload, merged):
        return None, "检测到方案包含简历和对话中未提供的量化结果，已阻止写入。"
    return merged, ""


def validate_basics_field_update(
    current_data: dict[str, Any] | None,
    basics: dict[str, Any] | None,
    history: list[dict[str, Any]] | None = None,
    user_message: str = "",
) -> tuple[dict[str, Any] | None, list[str], str]:
    """Validate a field-level basics change proposed by ``update_basics_field``.

    The AI service calls this through the ``/api/ai/service/tool/update-basics-field``
    endpoint. The tool proposes only the changed basics *fields* (e.g. ``{location:
    "深圳"}``) rather than a whole resume, so the guard is the same numeric
    anti-hallucination the LangChain path uses: a quantified result the model
    proposes must already appear in the resume or conversation, otherwise the
    pending change is blocked.

    Returns ``(optimized_data, suggestions, issue)``:

    - ``issue`` empty: ``optimized_data`` is the FULL merged resume (safe to persist
      as a pending change) with a human-readable ``suggestions`` list.
    - ``issue`` set: ``optimized_data`` is ``None`` and the change must be blocked.
    """
    if not isinstance(basics, dict) or not any(str(value or "").strip() for value in basics.values()):
        return None, [], "未提供需要修改的基本信息字段"

    payload: dict[str, Any] = {
        "resume": {"resume_data": current_data or {}},
        "history": history or [],
        "user_message": user_message,
        "attachments": [],
    }
    # The tool submits only the changed fields; merge them into the current basics
    # (leaving the rest of the basics section intact) rather than replacing it.
    merged, issue = _validated_optimized_data(
        payload,
        current_data,
        {"basics": basics},
        replace_sections=set(),
    )
    if issue:
        return None, [], issue
    suggestions = _actual_change_suggestions(current_data, merged)
    return merged, suggestions, ""


_LIST_SECTION_KEYS = {"education", "skills", "work", "projects", "awards", "custom_sections"}
_DICT_SECTION_KEYS = {"basics", "summary", "layout"}
# Prefix used when minting a fresh id for a list entry the model introduced without one.
_SECTION_ITEM_ID_PREFIX = {
    "education": "edu",
    "skills": "skill",
    "work": "work",
    "projects": "project",
    "awards": "award",
    "custom_sections": "custom",
}


# Fields a list-section entry may carry (kept in sync with the resume template's
# renderers and the starter data). The tool schema accepts arbitrary fields, and
# models occasionally invent shapes the template silently renders as nothing (e.g.
# a skills entry with `items` instead of `keywords`, an award with `title` instead
# of `name`) — block them at this trust boundary so bad shapes never reach storage.
SECTION_ITEM_ALLOWED_FIELDS: dict[str, frozenset[str] | None] = {
    "education": frozenset({"school", "major", "degree", "start_date", "end_date", "description"}),
    "skills": frozenset({"name", "keywords", "description"}),
    "work": frozenset({"company", "position", "start_date", "end_date", "description", "highlights"}),
    "projects": frozenset({"name", "role", "start_date", "end_date", "tech_stack", "description", "highlights"}),
    "awards": frozenset({"name", "date", "description"}),
    # custom_sections entries vary by preset type; the template renders a wide
    # field superset, so stay permissive there.
    "custom_sections": None,
}


def _section_item_field_issue(section: str, proposed: Any) -> str:
    """Reject list entries carrying fields outside the section's canonical shape."""
    allowed = SECTION_ITEM_ALLOWED_FIELDS.get(section)
    if allowed is None or not isinstance(proposed, list):
        return ""
    for index, item in enumerate(proposed, start=1):
        if not isinstance(item, dict):
            continue
        unknown = sorted(str(key) for key in item if key != "id" and key not in allowed)
        if unknown:
            supported = ", ".join(sorted(allowed))
            return f"第 {index} 条记录包含不支持的字段：{'、'.join(unknown)}（该模块支持的字段：{supported}）"
    return ""


def _section_item_id(section: str, n: int) -> str:
    prefix = _SECTION_ITEM_ID_PREFIX.get(section, section.rstrip("s"))
    return f"{prefix}_{n}"


def _merge_list_section(section: str, current: list, proposed: list, removed_ids: list[str] | None) -> list:
    """Merge a list section by item id, preserving untouched entries and position.

    - A current item whose id matches a proposed item is patched in place (its own
      id and untnamed fields survive); it stays where it was.
    - A current item not named by the model is untouched.
    - An id in ``removed_ids`` is dropped.
    - A proposed item with a model-supplied id that does not exist in the current
      section (or with no id at all) is appended as a NEW entry with a freshly
      generated id — the model cannot claim a known entry under a different id, so it
      cannot reorder or renumber existing work/projects.
    """
    if not isinstance(proposed, list):
        return deepcopy(current)

    removed = {str(item) for item in (removed_ids or [])}
    result: list = []
    for item in current:
        if not isinstance(item, dict):
            # Non-dict legacy entries are preserved verbatim instead of silently
            # dropped — a drop here could empty a section the user still sees.
            result.append(deepcopy(item))
            continue
        item_id = item.get("id")
        if item_id is not None and str(item_id) in removed:
            continue
        patch = next(
            (p for p in proposed if isinstance(p, dict) and p.get("id") is not None and str(p.get("id")) == str(item_id)),
            None,
        )
        if patch is None:
            result.append(deepcopy(item))
            continue
        merged_item = deepcopy(item)
        for key, value in patch.items():
            if key == "id":
                continue
            merged_item[key] = value
        result.append(merged_item)

    # Second pass: append genuinely new items the model introduced.
    existing_ids = {
        str(item.get("id"))
        for item in result
        if isinstance(item, dict) and item.get("id") is not None
    }
    next_n = 1
    for patch in proposed:
        if not isinstance(patch, dict):
            continue
        patch_id = patch.get("id")
        if patch_id is not None and str(patch_id) in existing_ids:
            continue
        new_item = deepcopy(patch)
        while _section_item_id(section, next_n) in existing_ids:
            next_n += 1
        new_item["id"] = _section_item_id(section, next_n)
        existing_ids.add(new_item["id"])
        result.append(new_item)
    return result


def merge_section_update(
    current_data: dict[str, Any] | None,
    section: str,
    proposed: Any,
    removed_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Reconcile a section-level update against the current resume snapshot.

    ``section`` is a top-level resume key (basics/summary/education/skills/work/
    projects/awards/custom_sections/layout). Dict sections merge field-level; list
    sections merge by item id via :func:`_merge_list_section`, so a change touches
    only the named entries. The returned value is the full merged resume data.
    """
    current = deepcopy(current_data or {})
    if section in _LIST_SECTION_KEYS:
        current_section = current.get(section)
        if not isinstance(current_section, list):
            current_section = deepcopy(current_section) if current_section is not None else []
            current[section] = current_section
        current[section] = _merge_list_section(section, current_section, proposed, removed_ids)
    elif section in _DICT_SECTION_KEYS:
        current_section = current.get(section)
        merged_section = deepcopy(current_section) if isinstance(current_section, dict) else {}
        if isinstance(proposed, dict):
            merged_section.update(proposed)
        current[section] = merged_section
    return current


def _section_update_has_content(section: str, proposed: Any, removed_ids: list[str] | None) -> bool:
    """Is there any real content to stage, or is the proposal effectively empty?"""
    if section in _LIST_SECTION_KEYS:
        if removed_ids:
            return True
        return any(
            isinstance(item, dict) and any(
                key != "id" and str(value or "").strip()
                for key, value in item.items()
            )
            for item in (proposed or [])
        )
    return isinstance(proposed, dict) and any(str(value or "").strip() for value in proposed.values())


def validate_section_update(
    current_data: dict[str, Any] | None,
    section: str,
    proposed: Any,
    history: list[dict[str, Any]] | None = None,
    user_message: str = "",
    removed_ids: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str], str]:
    """Validate a section-level change proposed by the AI service's ``update_section`` tool.

    ``proposed`` is scoped to a single top-level resume section (basics/summary/education/
    skills/work/projects/awards/custom_sections/layout). List sections are merged by item
    id (:func:`merge_section_update`) so only the named entries change; dict sections merge
    field-level. The same numeric anti-hallucination guard as :func:`validate_basics_field_update`
    then applies to the merged snapshot, and the full merged resume is returned to be staged
    as a pending change (it is NOT persisted here).

    Returns ``(merged, suggestions, issue)`` — ``merged`` is ``None`` when ``issue`` is set.
    """
    if section not in _LIST_SECTION_KEYS and section not in _DICT_SECTION_KEYS:
        return None, [], f"不支持修改的简历模块：{section}"
    if not _section_update_has_content(section, proposed, removed_ids):
        return None, [], "未提供需要修改的模块内容"
    field_issue = _section_item_field_issue(section, proposed)
    if field_issue:
        return None, [], field_issue

    merged = merge_section_update(current_data, section, proposed, removed_ids)
    if merged == (current_data or {}):
        return None, [], "模型没有生成实际内容变化，未创建待确认修改。"

    payload: dict[str, Any] = {
        "resume": {"resume_data": current_data or {}},
        "history": history or [],
        "user_message": user_message,
        "attachments": [],
    }
    if _unverified_numeric_claims(payload, merged):
        return None, [], "检测到方案包含简历和对话中未提供的量化结果，已阻止写入。"
    suggestions = _actual_change_suggestions(current_data, merged)
    return merged, suggestions, ""


def validate_propose_change(
    current_data: dict[str, Any] | None,
    updates: list[dict[str, Any]] | None,
    history: list[dict[str, Any]] | None = None,
    user_message: str = "",
) -> tuple[dict[str, Any] | None, list[str], str]:
    """Validate an atomic change spanning several sections (``propose_change`` tool).

    ``updates`` is a list of ``{section, proposed, removed_ids?}`` entries covering the
    whole scope the agent wants to stage. Each is id-bounded-merged onto the SAME running
    snapshot, then a single numeric anti-hallucination guard runs over the final result.
    This lets the agent declare a change to multiple modules at once (the "change scope")
    without the model re-emitting an entire resume — and without one section's change
    being validated against a stale snapshot.

    Returns ``(merged, suggestions, issue)`` — ``merged`` is ``None`` when ``issue`` is set.
    """
    if not isinstance(updates, list) or not updates:
        return None, [], "未提供需要修改的内容"

    merged = deepcopy(current_data or {})
    for entry in updates:
        if not isinstance(entry, dict):
            return None, [], "非法的修改提案"
        section = entry.get("section")
        proposed = entry.get("proposed")
        removed_ids = entry.get("removed_ids")
        if (
            section not in _LIST_SECTION_KEYS
            and section not in _DICT_SECTION_KEYS
        ):
            return None, [], f"不支持修改的简历模块：{section}"
        if not _section_update_has_content(section, proposed, removed_ids):
            return None, [], "未提供需要修改的模块内容"
        field_issue = _section_item_field_issue(section, proposed)
        if field_issue:
            return None, [], field_issue
        merged = merge_section_update(merged, section, proposed, removed_ids)

    if merged == (current_data or {}):
        return None, [], "模型没有生成实际内容变化，未创建待确认修改。"

    payload: dict[str, Any] = {
        "resume": {"resume_data": current_data or {}},
        "history": history or [],
        "user_message": user_message,
        "attachments": [],
    }
    if _unverified_numeric_claims(payload, merged):
        return None, [], "检测到方案包含简历和对话中未提供的量化结果，已阻止写入。"
    suggestions = _actual_change_suggestions(current_data, merged)
    return merged, suggestions, ""


def get_section_context(current_data: dict[str, Any] | None, section: str) -> dict[str, Any]:
    """Return the requested section(s) from the resume, safely.

    ``section`` may be a single top-level key or a comma-separated list. Returns the
    matched sections keyed by section name so the AI service can read a section on demand
    without shipping the whole resume. Unknown keys are omitted.
    """
    data = current_data or {}
    wanted = [part.strip() for part in str(section).split(",") if part.strip()]
    result: dict[str, Any] = {}
    for key in wanted:
        if key in _LIST_SECTION_KEYS and isinstance(data.get(key), list):
            result[key] = data.get(key)
        elif key in _DICT_SECTION_KEYS and isinstance(data.get(key), dict):
            result[key] = data.get(key)
    return result


def _actual_change_suggestions(
    current_data: dict[str, Any] | None,
    optimized_data: dict[str, Any] | None,
) -> list[str]:
    if not optimized_data:
        return []
    current = current_data or {}
    suggestions: list[str] = []
    for section in ("basics", "summary", "education", "skills", "work", "projects", "awards", "custom_sections"):
        if current.get(section) == optimized_data.get(section):
            continue
        label = SECTION_LABELS.get(section, "自定义模块")
        before = current.get(section)
        after = optimized_data.get(section)
        if section == "basics" and isinstance(before, dict) and isinstance(after, dict):
            basic_labels = {
                "name": "姓名", "title": "求职方向", "status": "当前状态", "phone": "电话",
                "email": "邮箱", "location": "所在城市", "expected_salary": "期望薪资",
                "highest_degree": "最高学历", "website": "个人网站", "github": "代码仓库",
            }
            changed = [
                basic_labels[key]
                for key in basic_labels
                if before.get(key) != after.get(key)
            ]
            detail = "、".join(changed) if changed else "内容"
            suggestions.append(f"已修改：基本信息（{detail}）")
        elif isinstance(before, list) and isinstance(after, list):
            item_names = [
                str(item.get("name") or item.get("school") or item.get("company") or "").strip()
                for item in after
                if isinstance(item, dict)
            ]
            item_names = [name for name in item_names if name]
            names = "、".join(item_names[:4])
            detail = f"{len(before)} 条变为 {len(after)} 条"
            if names:
                detail += f"：{names}"
            suggestions.append(f"已修改：{label}（{detail}）")
        else:
            suggestions.append(f"已修改：{label}内容")
    before_order = (current.get("layout") or {}).get("section_order", [])
    after_order = (optimized_data.get("layout") or {}).get("section_order", [])
    if before_order != after_order:
        suggestions.append("已修改：模块展示顺序")
    return suggestions


def _persist_resume_change(
    db: Session,
    user: User,
    resume: Resume,
    optimized_data: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Persist and verify one AI change before the assistant reports success."""
    db.add(
        ResumeVersion(
            resume_id=resume.id,
            resume_data=deepcopy(resume.resume_data),
            template_config=deepcopy(resume.template_config),
            reason=reason,
        )
    )
    before_data = deepcopy(resume.resume_data or {})
    # Pending AI changes are stored as a fully validated resume snapshot.
    next_data = deepcopy(_unwrap_resume_data(optimized_data))
    if next_data == before_data:
        raise AppException("本次方案没有产生实际内容变化，请重新生成修改")
    resume.resume_data = next_data
    flag_modified(resume, "resume_data")
    resume.update_by = user.id
    db.add(resume)
    db.flush()
    db.expire(resume, ["resume_data"])
    persisted_data = deepcopy(resume.resume_data or {})
    if persisted_data != next_data:
        db.rollback()
        raise AppException("修改写入校验失败，简历未发生变化，请重试")
    return persisted_data


def resolve_chat_change(
    db: Session,
    user: User,
    resume: Resume,
    message_id: int,
    action: str,
) -> tuple[AiChatMessageOut, dict[str, Any] | None]:
    message = db.get(AiChatMessage, message_id)
    if (
        not message
        or message.user_id != user.id
        or message.resume_id != resume.id
        or message.role != "assistant"
    ):
        raise AppException("待确认修改不存在", 404)
    if message.action_status != "pending":
        raise AppException("这次修改已经处理过了", 409)

    resume_data: dict[str, Any] | None = None
    if action == "apply":
        if not message.optimized_resume_data:
            raise AppException("这次对话没有可写入的修改")
        persisted_data = _persist_resume_change(
            db,
            user,
            resume,
            message.optimized_resume_data,
            "采纳 AI 助手修改",
        )
        message.action_status = "applied"
        resume_data = persisted_data
    elif action == "reject":
        message.action_status = "rejected"
    else:
        raise AppException("不支持的确认操作", 400)

    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_out(message), resume_data


