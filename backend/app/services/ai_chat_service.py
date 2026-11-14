from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import AppException
from app.core.resume_schema import normalize_resume_document, schema_compact
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume
from app.models.resume import ResumeVersion
from app.models.user import User
from app.schemas.ai_chat import AiChatMessageOut
from app.services.ai.token_usage import build_token_usage
from app.services.ai_config_service import (
    capability_supports_images,
    get_catalog_capabilities,
    resolve_chat_ai_config,
)
from app.services.ai_service_client import ai_service_chat_events, ai_service_task_result
from app.services.ai_task_service import create_ai_task, finish_ai_task
from app.services.flow_points_service import precheck_flow_points
from app.services.info_fragment_service import apply_proposals
from app.services.resume_normalization import localize_ai_text, strip_thinking_text
from app.services.storage.storage_service import owned_object_names
from app.services.user_ai_service import audit_chat_use, build_skills_payload, resolve_model_override

logger = logging.getLogger(__name__)

# 单次摘要压缩最多并入的消息条数：更早的留给下一轮增量压缩，避免 token 爆炸。
_COMPACT_MAX_MESSAGES = 200


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


def _sanitize_attachments(db: Session, user_id: int, attachments: list[Any] | None) -> list[dict[str, Any]]:
    """只保留确属当前用户上传的附件对象。

    ``object_name`` 完全由前端提供，直接拿去读存储等于把对象桶当成公开读接口：
    换个 object_name 就能把别人的对话图片喂给模型。这里按 uploaded_files 的
    ``(user_id, object_name)`` 归属过滤，越权项静默丢弃（不报错，避免探测）。
    没有 object_name 的附件不读存储，保持原样即可。
    """
    items = [_attachment_data(item) for item in attachments or []]
    names = [item["object_name"] for item in items if isinstance(item.get("object_name"), str) and item["object_name"]]
    owned = owned_object_names(db, user_id, names) if names else set()
    kept: list[dict[str, Any]] = []
    for item in items:
        object_name = item.get("object_name")
        if isinstance(object_name, str) and object_name and object_name not in owned:
            logger.warning("丢弃不属于当前用户的对话附件：user=%s object=%s", user_id, object_name)
            continue
        kept.append(item)
    return kept


def _message_out(message: AiChatMessage) -> AiChatMessageOut:
    return AiChatMessageOut.model_validate(
        {
            "id": message.id,
            "role": message.role,
            "content": strip_thinking_text(message.content or "") if message.role == "assistant" else message.content,
            "attachments": message.attachments or [],
            "suggestions": message.suggestions or [],
            "optimized_resume_data": message.optimized_resume_data,
            # 单测里常用 SimpleNamespace 造消息桩，缺新字段时按空处理
            "pending_info_fragments": getattr(message, "pending_info_fragments", None),
            "tool_trace": message.tool_trace,
            "timing": message.timing,
            "pending_questions": message.pending_questions,
            "action_status": message.action_status or "none",
            "create_time": message.create_time,
        }
    )


def _clip_text(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit] + "..."



DEFAULT_CHAT_SESSION_TITLE = "新对话"
# 历史遗留的默认标题：这些标题仍视为「未命名」，首条用户消息会覆盖成自动标题。
_PLACEHOLDER_SESSION_TITLES = {"", DEFAULT_CHAT_SESSION_TITLE, "AI 简历助手"}
_AUTO_SESSION_TITLE_LENGTH = 24
_SESSION_PREVIEW_LENGTH = 60
# 会话搜索：关键词最长 60 字，一次最多返回 20 条（侧栏面板只做快速跳转）。
_CHAT_SEARCH_KEYWORD_LENGTH = 60
_CHAT_SEARCH_LIMIT = 20


def _session_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:120]


def _auto_session_title(content: str | None) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return ""
    return text if len(text) <= _AUTO_SESSION_TITLE_LENGTH else text[:_AUTO_SESSION_TITLE_LENGTH] + "…"


def chat_session_data(session: AiChatSession, message_count: int = 0, last_message: str = "") -> dict[str, Any]:
    return {
        "id": session.id,
        "resume_id": session.resume_id,
        "title": session.title or DEFAULT_CHAT_SESSION_TITLE,
        "message_count": message_count,
        "last_message": last_message,
        "is_pinned": bool(getattr(session, "is_pinned", False)),
        "create_time": session.create_time,
        "update_time": session.update_time,
    }


def _session_payloads(db: Session, sessions: list[AiChatSession]) -> list[dict[str, Any]]:
    """给一批会话补上「消息条数 + 最后一条预览」。

    一次聚合查询 + 一次按 id 取内容，避免逐条会话再查一遍（N+1）。列表接口和
    搜索接口共用，保证两处的行数据形状一致。
    """
    if not sessions:
        return []

    session_ids = [item.id for item in sessions]
    stats = {
        row[0]: (int(row[1] or 0), row[2])
        for row in db.execute(
            select(
                AiChatMessage.session_id,
                func.count(AiChatMessage.id),
                func.max(AiChatMessage.id),
            )
            .where(AiChatMessage.session_id.in_(session_ids))
            .group_by(AiChatMessage.session_id)
        ).all()
    }
    last_ids = [last_id for _, last_id in stats.values() if last_id is not None]
    previews = (
        {
            row[0]: re.sub(r"\s+", " ", str(row[1] or "")).strip()[:_SESSION_PREVIEW_LENGTH]
            for row in db.execute(
                select(AiChatMessage.id, AiChatMessage.content).where(AiChatMessage.id.in_(last_ids))
            ).all()
        }
        if last_ids
        else {}
    )

    result: list[dict[str, Any]] = []
    for session in sessions:
        count, last_id = stats.get(session.id, (0, None))
        result.append(chat_session_data(session, count, previews.get(last_id, "") if last_id else ""))
    return result


def list_chat_sessions(db: Session, user_id: int, resume_id: int) -> list[dict[str, Any]]:
    """List the caller's sessions of one resume: pinned first, then newest activity."""
    sessions = list(
        db.scalars(
            select(AiChatSession)
            .where(AiChatSession.user_id == user_id, AiChatSession.resume_id == resume_id)
            .order_by(
                AiChatSession.is_pinned.desc(),
                AiChatSession.update_time.desc(),
                AiChatSession.id.desc(),
            )
        )
    )
    return _session_payloads(db, sessions)


def _search_like_pattern(keyword: str | None) -> str:
    """把用户输入转成 LIKE 模式，转义 ``%`` / ``_`` / ``\\``。

    不转义的话搜一个 ``%`` 会命中全部会话；``\\`` 自身必须先转义，否则会吞掉
    后面接的转义符。
    """
    text = re.sub(r"\s+", " ", str(keyword or "")).strip()[:_CHAT_SEARCH_KEYWORD_LENGTH]
    if not text:
        return ""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_chat_sessions(
    db: Session, user_id: int, keyword: str | None, limit: int = _CHAT_SEARCH_LIMIT
) -> list[dict[str, Any]]:
    """跨简历搜会话：命中标题或任意一条消息内容。

    结果带上 ``resume_title``，侧栏搜索面板要能直接显示「属于哪份简历」。
    """
    pattern = _search_like_pattern(keyword)
    if not pattern:
        return []

    matched_message = (
        select(AiChatMessage.id)
        .where(
            AiChatMessage.session_id == AiChatSession.id,
            AiChatMessage.user_id == user_id,
            AiChatMessage.content.like(pattern, escape="\\"),
        )
        .exists()
    )
    rows = db.execute(
        select(AiChatSession, Resume.title)
        .join(Resume, Resume.id == AiChatSession.resume_id)
        .where(
            AiChatSession.user_id == user_id,
            Resume.user_id == user_id,
            or_(AiChatSession.title.like(pattern, escape="\\"), matched_message),
        )
        .order_by(
            AiChatSession.is_pinned.desc(),
            AiChatSession.update_time.desc(),
            AiChatSession.id.desc(),
        )
        .limit(max(1, min(int(limit or _CHAT_SEARCH_LIMIT), _CHAT_SEARCH_LIMIT)))
    ).all()

    sessions = [row[0] for row in rows]
    resume_titles = {row[0].id: row[1] for row in rows}
    payloads = _session_payloads(db, sessions)
    for payload in payloads:
        payload["resume_title"] = resume_titles.get(payload["id"]) or ""
    return payloads


def create_chat_session(
    db: Session, user_id: int, resume_id: int, title: str | None = None
) -> AiChatSession:
    session = AiChatSession(
        user_id=user_id,
        resume_id=resume_id,
        title=_session_title(title) or DEFAULT_CHAT_SESSION_TITLE,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_chat_session(db: Session, user_id: int, resume_id: int, session_id: int) -> AiChatSession:
    session = db.get(AiChatSession, session_id)
    if not session or session.user_id != user_id or session.resume_id != resume_id:
        raise AppException("会话不存在", 404)
    return session


def rename_chat_session(
    db: Session, user_id: int, resume_id: int, session_id: int, title: str
) -> AiChatSession:
    session = get_chat_session(db, user_id, resume_id, session_id)
    next_title = _session_title(title)
    if not next_title:
        raise AppException("会话标题不能为空", 400)
    session.title = next_title
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def set_chat_session_pinned(
    db: Session, user_id: int, resume_id: int, session_id: int, is_pinned: bool
) -> AiChatSession:
    session = get_chat_session(db, user_id, resume_id, session_id)
    session.is_pinned = bool(is_pinned)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def delete_chat_session(db: Session, user_id: int, resume_id: int, session_id: int) -> None:
    session = get_chat_session(db, user_id, resume_id, session_id)
    db.execute(
        delete(AiChatMessage).where(
            AiChatMessage.session_id == session.id, AiChatMessage.user_id == user_id
        )
    )
    db.delete(session)
    db.commit()


def _maybe_apply_auto_title(db: Session, session: AiChatSession, content: str | None) -> bool:
    """Name a still-untitled session after its first user message.

    Only runs while the session has no user turn yet, so a manual rename (or an
    already-named conversation) is never overwritten by a later message.
    """
    if _session_title(session.title) not in _PLACEHOLDER_SESSION_TITLES:
        return False
    user_turns = db.scalar(
        select(func.count())
        .select_from(AiChatMessage)
        .where(AiChatMessage.session_id == session.id, AiChatMessage.role == "user")
    )
    if user_turns:
        return False
    title = _auto_session_title(content)
    if not title:
        return False
    session.title = title
    return True


def get_or_create_chat_session(
    db: Session, user_id: int, resume_id: int, session_id: int | None = None
) -> AiChatSession:
    """Resolve the working session.

    With ``session_id`` the session must belong to this user and resume (404
    otherwise). Without it we fall back to the most recently active session —
    the pre-multi-session behaviour for callers that have not been migrated —
    and create one when the resume has no session at all.
    """
    if session_id is not None:
        return get_chat_session(db, user_id, resume_id, session_id)
    session = db.scalar(
        select(AiChatSession)
        .where(AiChatSession.user_id == user_id, AiChatSession.resume_id == resume_id)
        .order_by(AiChatSession.update_time.desc(), AiChatSession.id.desc())
    )
    if session:
        return session
    return create_chat_session(db, user_id, resume_id)


def list_chat_messages(
    db: Session,
    user_id: int,
    resume_id: int,
    limit: int = 80,
    after_id: int | None = None,
    session_id: int | None = None,
) -> list[AiChatMessageOut]:
    session = get_or_create_chat_session(db, user_id, resume_id, session_id)
    query = select(AiChatMessage).where(AiChatMessage.session_id == session.id, AiChatMessage.user_id == user_id)
    if after_id is not None:
        query = query.where(AiChatMessage.id > after_id)
    messages = list(db.scalars(query.order_by(AiChatMessage.id.desc()).limit(limit)))
    return [_message_out(message) for message in reversed(messages)]


def clear_chat_messages(
    db: Session, user_id: int, resume_id: int, session_id: int | None = None
) -> None:
    session = get_or_create_chat_session(db, user_id, resume_id, session_id)
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
    pending_questions: list[dict[str, Any]] | None = None,
    timing: dict[str, Any] | None = None,
    pending_info_fragments: list[dict[str, Any]] | None = None,
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
        pending_info_fragments=pending_info_fragments or None,
        tool_trace=tool_trace or None,
        timing=timing or None,
        pending_questions=pending_questions or None,
        action_status="pending" if (optimized_resume_data or pending_info_fragments) else "none",
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
    summary: str | None = None,
    summary_message_id: int | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    """Build the context block injected into the AI service prompt.

    Mirrors the shape FastAPI reads from MySQL: resume snapshot, the recent chat
    history (capped at ``settings.ai_service_context_messages``) and the
    outstanding pending-change summary. ``summary`` is the rolling compaction of
    everything up to ``summary_message_id`` — injected so long conversations keep
    their early context. ``session_id`` scopes both the history and the pending
    change to one conversation: a proposal staged in another chat must never leak
    into this turn's context. The AI service renders this into the system prompt and
    never touches MySQL itself, keeping the Node session stateless while MySQL
    remains the single source of truth.
    """
    context_limit = limit or max(1, min(int(settings.ai_service_context_messages or 12), 40))
    history = list_chat_messages(
        db,
        user.id,
        resume.id,
        limit=context_limit + 8,
        after_id=summary_message_id,
        session_id=session_id,
    )
    pending_filters = [
        AiChatMessage.user_id == user.id,
        AiChatMessage.resume_id == resume.id,
        AiChatMessage.role == "assistant",
        AiChatMessage.action_status == "pending",
        AiChatMessage.optimized_resume_data.is_not(None),
    ]
    if session_id is not None:
        pending_filters.append(AiChatMessage.session_id == session_id)
    pending_change = db.scalar(
        select(AiChatMessage)
        .where(*pending_filters)
        .order_by(AiChatMessage.id.desc())
    )
    resume_data = resume.resume_data or {}
    return {
        "resume": {
            "id": resume.id,
            "title": resume.title,
            # 简历全文随上下文下发：Agent 要改内容时不必再单独读一次，
            # 字段名也能照着现有 JSON 写，省掉一轮工具往返。
            "data": resume_data,
        },
        "context_summary": summary or "",
        "history": [{"role": item.role, "content": item.content} for item in history[-context_limit:]],
        # 字段清单（一行一个模块）由 core/resume_schema.py 单一维护，比整份契约省一个数量级。
        "resume_fields": schema_compact(),
        "pending_change": {
            "exists": pending_change is not None,
            "message_id": pending_change.id if pending_change else None,
            "summary": pending_change.suggestions if pending_change else [],
        },
    }


def _context_message_limit(model_config) -> int:
    """对话历史注入条数：跟随所选模型策略，未选时回退全局默认（夹在 1..40）。"""
    configured = model_config.context_messages if model_config else None
    return max(1, min(int(configured or settings.ai_service_context_messages or 12), 40))


async def _compact_chat_context(session_id: int, context_limit: int, model_ref: str | None) -> None:
    """后台滚动摘要压缩：把窗口外的旧消息增量并进会话摘要。

    在回合结束后的后台任务里运行（asyncio.create_task，fire-and-forget），自开
    DB 会话；任何失败只落 failed 任务行供观察，绝不影响用户的对话回合。摘要是
    平台成本：chat_summary 任务行只记 token，不向用户结算积分。
    """
    db = SessionLocal()
    try:
        session = db.get(AiChatSession, session_id)
        if not session:
            return
        after = session.summary_message_id or 0
        # desc 取「窗口 + 待压缩上限」条；最新 context_limit 条保留原文，其余是候选。
        rows = list(
            db.scalars(
                select(AiChatMessage)
                .where(AiChatMessage.session_id == session.id, AiChatMessage.id > after)
                .order_by(AiChatMessage.id.desc())
                .limit(context_limit + _COMPACT_MAX_MESSAGES)
            )
        )
        if len(rows) <= context_limit:
            return
        # 升序取最旧的至多 _COMPACT_MAX_MESSAGES 条；更早的下一轮继续，不会丢消息。
        to_compact = list(reversed(rows[context_limit:]))[:_COMPACT_MAX_MESSAGES]
        new_upto = to_compact[-1].id
        payload_messages = [{"role": item.role, "content": item.content or ""} for item in to_compact]

        task = create_ai_task(
            db,
            session.user_id,
            "chat_summary",
            resume_id=session.resume_id,
            model_name=model_ref or None,
            input_data={"model": model_ref or "", "messages": len(payload_messages)},
        )
        try:
            data, usage = await ai_service_task_result(
                "chat_summary",
                {"previous_summary": session.context_summary or "", "messages": payload_messages},
                model_ref=model_ref,
            )
        except Exception as exc:
            logger.warning("chat summary compaction failed: %s", exc)
            finish_ai_task(db, task, "failed", error_message=str(exc)[:4000])
            return

        token_usage = build_token_usage(
            sum(int(entry.get("input_tokens") or 0) for entry in usage),
            sum(int(entry.get("output_tokens") or 0) for entry in usage),
            estimated=False,
        )
        summary_text = str((data or {}).get("summary") or "").strip()
        # 即使摘要为空也推进 watermark：避免同一段消息被反复压缩成空摘要。
        session.context_summary = summary_text or None
        session.summary_message_id = new_upto
        db.add(session)
        db.commit()
        finish_ai_task(
            db,
            task,
            "success",
            output_data={"source": "chat_summary", "covered_message_id": new_upto, "messages": len(payload_messages)},
            token_usage=token_usage,
            settle=False,
        )
    except Exception:
        db.rollback()
        logger.exception("chat summary compaction crashed")
    finally:
        db.close()


def _schedule_chat_summary(db: Session, session: AiChatSession, context_limit: int, model_ref: str | None) -> None:
    """窗口外积压超过阈值时，安排一次后台摘要压缩（fire-and-forget）。"""
    after = session.summary_message_id or 0
    pending_count = db.scalar(
        select(func.count())
        .select_from(AiChatMessage)
        .where(AiChatMessage.session_id == session.id, AiChatMessage.id > after)
    ) or 0
    if pending_count > context_limit + 8:
        _spawn_background(_compact_chat_context(session.id, context_limit, model_ref))


# asyncio 只对任务持有弱引用：不自己存一份，任务可能在跑完前被 GC 回收，
# 摘要就会偶发不执行。这里同时兜住异常，避免出现 "Task exception was never
# retrieved" 的静默失败。
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_release_background_task)
    return task


def _release_background_task(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("后台摘要任务失败", exc_info=error)


def _resolve_turn_model_config(db: Session, user: User, model_config_id: int | None):
    try:
        return resolve_chat_ai_config(db, user, model_config_id)
    except AppException:
        return None


def _resolve_user_ai(
    db: Session, user_id: int, credential_id: int | None = None
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """BYOK 与自定义技能是可选增强：读不到就静默回退平台默认。

    迁移未执行、配置表异常、密钥轮换后解不开等都不该让整轮对话失败；
    这里统一兜底并留一条 warning 供运维排查。``credential_id`` 由前端的
    模型选择器下发，用于在不改默认项的前提下临时切换自带模型。
    """
    try:
        override = resolve_model_override(db, user_id, credential_id)
        skills = build_skills_payload(db, user_id)
        if override:
            # 审计走独立 session，不碰本轮流式请求的事务；失败只告警。
            audit_chat_use(user_id, override)
        return override, skills
    except Exception:
        logger.warning("读取用户自定义模型/技能失败，本轮回退平台默认", exc_info=True)
        return None, []


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


def _precheck_turn_points(db: Session, user: User, model_config, *, byok_active: bool = False) -> None:
    # Charge_on_finish parity: precheck the points_per_call floor up front so an
    # empty balance fails the turn before any model call; the exact token-based
    # charge settles later via the /service/usage callback.
    # BYOK turns consume the user's own quota: never gate them on platform points.
    if byok_active or not model_config:
        return
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
    model_override: dict[str, Any] | None = None,
    skills: list[dict[str, str]] | None = None,
    trigger: str = "free_text",
) -> AsyncIterator[dict[str, Any]]:
    """Relay one AI-service turn for an already-persisted user message.

    Builds the context from MySQL (resume snapshot, recent history, pending
    change), relays the AI service's NDJSON stream, accumulates the assistant
    text and, on ``result``, persists the assistant message and rewrites
    ``result.data.assistant_message`` with the DB row. MySQL stays the single
    source of truth and the AI service stays stateless. Shared by send and
    regenerate (ticket 09).
    """
    context_limit = _context_message_limit(model_config)
    context = build_ai_service_context(
        db,
        user,
        resume,
        # 历史注入条数跟随所选模型策略；未选策略时回退全局默认。
        limit=context_limit,
        summary=session.context_summary,
        summary_message_id=session.summary_message_id,
        session_id=session.id,
    )
    text_parts: list[str] = []
    # 转发前再按归属收一次：历史消息里可能残留早期未校验的 object_name，
    # 代理层只允许读这个集合内的对象。
    attachment_names = [
        item["object_name"]
        for item in (_attachment_data(raw) for raw in (user_message.attachments or []))
        if isinstance(item.get("object_name"), str) and item["object_name"]
    ]
    allowed_attachment_names = owned_object_names(db, user.id, attachment_names)
    events = ai_service_chat_events(
        user_message.content or "",
        context=context,
        user_id=user.id,
        resume_id=resume.id,
        attachments=user_message.attachments or [],
        allowed_attachment_names=allowed_attachment_names,
        model_ref=model_config.model_ref if model_config else None,
        model_override=model_override,
        skills=skills,
        # 轨迹关联键（docs/ai/ai-trajectory.md）：被回复的用户消息 id + 会话 id，
        # 让 ai-service 落盘的回合能和消息表对上；trigger 区分重新生成。
        # 用 getattr 而非直取：轨迹是"锦上添花"的旁路，不能因为一个缺字段的
        # 会话对象把整个回复打挂。
        turn_id=getattr(user_message, "id", None),
        session_id=getattr(session, "id", None),
        trigger=trigger,
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
                # Structured gap questions (ask_user_questions tool) ride the result
                # event too and are persisted so the answer dialog survives reloads.
                questions = data.get("pending_questions")
                pending_questions = [item for item in questions if isinstance(item, dict)] if isinstance(questions, list) else None
                # 回合环节耗时（首字/工具/总时长）只保留数值字段，供看板聚合。
                raw_timing = data.get("timing")
                timing = (
                    {key: value for key, value in raw_timing.items() if isinstance(value, (int, float)) or value is None}
                    if isinstance(raw_timing, dict)
                    else None
                )
                pending_fragments = data.get("pending_info_fragments")
                pending_info_fragments = (
                    [item for item in pending_fragments if isinstance(item, dict)]
                    if isinstance(pending_fragments, list)
                    else None
                )
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
                        pending_questions=pending_questions,
                        timing=timing,
                        pending_info_fragments=pending_info_fragments,
                    )
                else:
                    assistant = _save_assistant_message(
                        db,
                        session,
                        user,
                        resume,
                        reply,
                        [str(item.get("title") or "信息") for item in (pending_info_fragments or [])],
                        None,
                        tool_trace,
                        pending_questions=pending_questions,
                        timing=timing,
                        pending_info_fragments=pending_info_fragments,
                    )
                # 窗口外积压够了就在后台压缩成滚动摘要（下轮生效），不阻塞本回合。
                # 计数失败只丢一次调度，不能把已收完的回复变成 error 事件。
                try:
                    _schedule_chat_summary(
                        db,
                        session,
                        context_limit,
                        model_config.model_ref if model_config else None,
                    )
                except Exception:
                    logger.warning("schedule chat summary failed", exc_info=True)
                yield {"type": "result", "data": {"assistant_message": assistant.model_dump(mode="json")}}
            else:
                yield event
    except Exception as exc:
        yield {"type": "error", "message": f"AI 对话处理失败：{exc}"}


async def stream_ai_service_chat(
    user: User,
    resume: Resume,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
    session_id: int | None = None,
    user_model_id: int | None = None,
    *,
    db_factory: Callable[[], Session] = SessionLocal,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat turn backed by the Node AI service.

    The chat model config (selected or default) drives the multimodal guard,
    the model mapping sent to the Node service (``model_ref``) and the
    points_per_call precheck (ticket 07). It is resolved tolerantly: with no
    model configured at all (pure faux demo) the turn still runs, unmapped and
    unbilled.

    ``session_id`` picks the conversation; omitting it keeps the legacy
    "latest session of this resume" behaviour.

    会话由生成器自己持有：FastAPI 的 ``Depends(get_db)`` 在响应体开始发送前就
    会 close，把那个请求级会话带进流里等于在已关闭的 Session 上 add/commit。
    所以这里只接收 ``db_factory``，在流结束时关闭；客户端中途断开时
    ``already-committed`` 的消息保留，未提交的部分随 close 回滚。
    """
    db = db_factory()
    try:
        async for event in _stream_chat_turn(
            db, user, resume, content, attachments, model_config_id, session_id, user_model_id
        ):
            yield event
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _stream_chat_turn(
    db: Session,
    user: User,
    resume: Resume,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
    session_id: int | None = None,
    user_model_id: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    message = (content or "").strip()
    if not message:
        yield {"type": "error", "message": "请输入要咨询的问题"}
        return

    # 附件对象名来自前端，先按归属收一遍，再落库/转发给 AI 服务。
    attachments = _sanitize_attachments(db, user.id, attachments)

    model_config = _resolve_turn_model_config(db, user, model_config_id)
    # 用户自带模型（BYOK）与启用中的技能每轮解析一次：关闭或密钥失效时自动
    # 回落到平台默认，不需要前端传任何开关。
    model_override, skills = _resolve_user_ai(db, user.id, user_model_id)
    image_attachments = _turn_image_attachments(attachments)
    supports_images = bool(model_override and model_override.get("supports_image")) or capability_supports_images(
        await get_catalog_capabilities(), model_config.model_ref if model_config else None
    )
    image_issue = _reject_turn_images(model_config, supports_images, image_attachments)
    if image_issue:
        yield {"type": "error", "message": image_issue}
        return
    _precheck_turn_points(db, user, model_config, byok_active=bool(model_override))

    session = get_or_create_chat_session(db, user.id, resume.id, session_id)
    # 首条消息给未命名会话起标题（必须在插入本条之前判定「有没有历史回合」）。
    if _maybe_apply_auto_title(db, session, message):
        db.add(session)
        db.commit()
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

    async for event in _stream_ai_service_reply(
        db, user, resume, session, user_message, model_config, model_override, skills
    ):
        yield event


async def stream_ai_service_regenerate(
    user: User,
    resume: Resume,
    message_id: int,
    override_content: str | None = None,
    override_attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
    session_id: int | None = None,
    user_model_id: int | None = None,
    *,
    db_factory: Callable[[], Session] = SessionLocal,
) -> AsyncIterator[dict[str, Any]]:
    """Regenerate the reply to a user message via the Node AI service (ticket 09).

    Keeps the legacy semantics: the target must be the caller's own user
    message, every later message in the session is dropped, optional
    content/attachment overrides are applied in place, then the turn re-runs
    through the same relay as a fresh send.

    The drop stays inside the message's own session, so regenerating in one
    conversation can never truncate another one.

    与 ``stream_ai_service_chat`` 一样，会话由生成器自持而不是复用请求级依赖。
    """
    db = db_factory()
    try:
        async for event in _regenerate_turn(
            db,
            user,
            resume,
            message_id,
            override_content,
            override_attachments,
            model_config_id,
            session_id,
            user_model_id,
        ):
            yield event
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _regenerate_turn(
    db: Session,
    user: User,
    resume: Resume,
    message_id: int,
    override_content: str | None = None,
    override_attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
    session_id: int | None = None,
    user_model_id: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    user_message = db.get(AiChatMessage, message_id)
    if (
        not user_message
        or user_message.user_id != user.id
        or user_message.resume_id != resume.id
        or user_message.role != "user"
    ):
        yield {"type": "error", "message": "只能重新生成自己的用户消息"}
        return
    if session_id is not None and user_message.session_id != session_id:
        yield {"type": "error", "message": "这条消息不属于当前会话"}
        return

    applying_overrides = override_content is not None or override_attachments is not None
    next_content = ""
    next_attachments: list[dict[str, Any]] = []
    if applying_overrides:
        next_content = (override_content if override_content is not None else user_message.content or "").strip()
        next_attachments = _sanitize_attachments(
            db,
            user.id,
            override_attachments if override_attachments is not None else (user_message.attachments or []),
        )
        if not next_content and not next_attachments:
            yield {"type": "error", "message": "重新生成内容不能为空"}
            return

    # 守卫与点数预检必须先于删除：余额不足、图片不被模型支持等失败不能
    # 连带把后面的消息历史丢掉。
    model_config = _resolve_turn_model_config(db, user, model_config_id)
    model_override, skills = _resolve_user_ai(db, user.id, user_model_id)
    effective_attachments = next_attachments if applying_overrides else (user_message.attachments or [])
    supports_images = bool(model_override and model_override.get("supports_image")) or capability_supports_images(
        await get_catalog_capabilities(), model_config.model_ref if model_config else None
    )
    image_issue = _reject_turn_images(model_config, supports_images, _turn_image_attachments(effective_attachments))
    if image_issue:
        yield {"type": "error", "message": image_issue}
        return
    _precheck_turn_points(db, user, model_config, byok_active=bool(model_override))

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
    session = get_or_create_chat_session(db, user.id, resume.id, user_message.session_id)
    session.update_time = datetime.now()
    db.add(session)
    db.commit()
    db.refresh(user_message)

    yield {"type": "start", "data": {"user_message_id": user_message.id}}
    async for event in _stream_ai_service_reply(
        db, user, resume, session, user_message, model_config, model_override, skills, trigger="regenerate"
    ):
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


def _normalize_keeping_section_titles(data: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """normalize_resume_data 但保留调用方的模块标题：标题是内容的一部分
    （翻译提案会把标题换成另一语言的默认名，被 locale 重置等于吞掉该变更）。"""
    titles_in = deepcopy((data.get("layout") or {}).get("section_titles"))
    normalized = normalize_resume_document(data, payload)
    if isinstance(titles_in, dict) and titles_in:
        normalized.setdefault("layout", {})["section_titles"] = {**normalized.get("layout", {}).get("section_titles", {}), **titles_in}
    return normalized


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
        # web_search 本回合检索到的结果（/service/tool/search-evidence 落的记忆）
        # 也算证据：行业/JD 类数字可以引用搜索到的公开事实
        "search_evidence": payload.get("search_evidence") or [],
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


def tool_issue_category(issue: str) -> str:
    """Classify a tool-validation issue for the error envelope (统一错误信封).

    - ``business``：反幻觉拦截 / 无实际变化——模型原样重试没有意义，必须换数据或问用户；
    - ``validation``：字段、模块、结构问题——模型修正参数后可以重新调用。
    """
    if "量化结果" in issue or "没有生成实际内容变化" in issue:
        return "business"
    return "validation"


def validate_basics_field_update(
    current_data: dict[str, Any] | None,
    basics: dict[str, Any] | None,
    history: list[dict[str, Any]] | None = None,
    user_message: str = "",
    search_evidence: list[str] | None = None,
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
        "search_evidence": search_evidence or [],
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
    search_evidence: list[str] | None = None,
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
        "search_evidence": search_evidence or [],
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
    search_evidence: list[str] | None = None,
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
        "search_evidence": search_evidence or [],
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
        raise AppException("本次方案没有产生实际内容变化，请重新生成修改", 400)
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
    merged_resume_data: dict[str, Any] | None = None,
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
        # 信息库提案和简历修改可以同时存在（Agent 一轮里既整理信息又改简历）：
        # 先入库，再写简历；两者都没有才是非法确认。
        pending_fragments = getattr(message, "pending_info_fragments", None)
        had_fragments = bool(pending_fragments)
        if had_fragments:
            apply_proposals(db, user.id, pending_fragments)
            message.pending_info_fragments = None
        if message.optimized_resume_data:
            # 审阅模式部分采纳：前端把勾选的变更合并到「当前」简历数据后提交；
            # 缺省走整包快照（整包替换会冲掉提案之后的手动编辑）。
            # 标题属于内容（翻译/重命名后与简历 language 列无关），规范化后必须原样保留，
            # 否则已知默认标题（Basic Information 等）会被本地化重置回简历原语言。
            if merged_resume_data is not None:
                if not isinstance(merged_resume_data, dict) or not merged_resume_data:
                    raise AppException("提交的修改数据无效", 400)
                pending_data = _normalize_keeping_section_titles(deepcopy(merged_resume_data), {"language": resume.language})
            else:
                pending_data = message.optimized_resume_data
            resume_data = _persist_resume_change(
                db,
                user,
                resume,
                pending_data,
                "采纳 AI 助手修改",
            )
        elif not had_fragments:
            raise AppException("这次对话没有可写入的修改", 400)
        message.action_status = "applied"
    elif action == "reject":
        message.pending_info_fragments = None
        message.action_status = "rejected"
    else:
        raise AppException("不支持的确认操作", 400)

    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_out(message), resume_data


