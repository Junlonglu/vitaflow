import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.request_utils import get_client_ip
from app.core.response import success
from app.core.security import get_current_user
from app.models.ai_config import FlowPointRule, FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.resume import Resume
from app.models.user import User
from app.schemas.ai import (
    GenerateResumeRequest,
)
from app.schemas.ai_chat import (
    AiChatDecisionRequest,
    AiChatDecisionResponse,
    AiChatRegenerateRequest,
    AiChatSendRequest,
)
from app.services.ai.token_usage import build_token_usage, normalize_token_usage
from app.services.ai_chat_service import (
    clear_chat_messages,
    list_chat_messages,
    stream_ai_service_chat,
    stream_ai_service_regenerate,
    resolve_chat_change,
    validate_basics_field_update,
    validate_section_update,
    validate_propose_change,
    tool_issue_category,
    get_section_context,
)
from app.services.ai_config_service import (
    get_active_ai_config,
    get_ai_config_by_id,
    get_ai_config_by_model_ref,
    get_catalog_capabilities_sync,
    get_default_chat_ai_config,
    public_ai_capability,
)
from app.services.ai_service_client import ai_service_task_events
from app.services.ai_task_service import (
    ai_task_usage_payload,
    create_ai_task,
    finish_ai_task,
)
from app.services.app_settings_service import get_setting
from app.services.flow_points_service import (
    ACTIVE_AI_FEATURE_TYPES,
    consume_flow_points,
    ensure_default_point_rules,
    estimate_tokens,
    precheck_flow_points,
    point_number,
    redeem_flow_points,
)
from app.services.resume_normalization import normalize_resume_data
from app.services.resume_service import get_resume

router = APIRouter(prefix="/ai", tags=["ai"])


class FlowPointRedeemRequest(BaseModel):
    code: str


class UpdateBasicsFieldRequest(BaseModel):
    """Field-level basics change proposed by the AI service's update_basics_field tool."""

    user_id: int
    resume_id: int
    basics: dict[str, Any]


class UpdateSectionRequest(BaseModel):
    """Section-level change proposed by the AI service's update_section tool."""

    user_id: int
    resume_id: int
    section: str
    proposed: Any = None
    removed_ids: list[str] | None = None


class ProposeChangeRequest(BaseModel):
    """Atomic multi-section change proposed by the AI service's propose_change tool."""

    user_id: int
    resume_id: int
    updates: list[dict[str, Any]]


# 对话回合的 feature 取值（与 ai-service 的 QuickCommand name 对齐，chat 为自由对话）。
AI_CHAT_FEATURES = frozenset({"polish", "translate", "optimize", "generate", "greeting", "chat"})


class AiServiceUsageEntry(BaseModel):
    """One model's aggregated usage for a finished chat turn (ticket 07)."""

    provider: str
    model: str
    requests: int = Field(default=1, ge=1)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0


class AiServiceUsageRequest(BaseModel):
    """End-of-request usage report the Node AI service posts to FastAPI.

    ``feature`` is the quick command the turn ran (polish/translate/optimize/
    generate/greeting) or "chat" for free-form messages — it powers the admin
    feature breakdown. ``status="failed"`` turns carry no usage: they only
    record a failed turn so the success rate reflects real failures instead of
    silently dropping them (failed turns are never billed).
    """

    user_id: int
    resume_id: int | None = None
    feature: str | None = None
    tool_calls: int = Field(default=0, ge=0)
    status: str | None = None
    error_message: str | None = None
    usage: list[AiServiceUsageEntry] = Field(default_factory=list)


def require_ai_service_token(request: Request) -> None:
    """Gate the internal tool-callback endpoints to the Node AI service only."""
    supplied = request.headers.get("x-ai-service-token") or ""
    expected = settings.ai_service_token
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise AppException("未授权：需要有效的服务令牌", 401)


def ai_payload(payload: Any) -> dict[str, Any]:
    return payload.model_dump(exclude={"resume_id"})


def ai_metadata(task_type: str, payload: Any, model_name: str, model_config_id: int | None = None) -> dict[str, Any]:
    data = payload.model_dump()
    metadata: dict[str, Any] = {"model": model_name}
    if model_config_id:
        metadata["model_config_id"] = model_config_id
    for key in ("section_type", "section_title", "target_position", "optimize_style", "current_language", "target_language"):
        if data.get(key):
            metadata[key] = data[key]
    if data.get("job_description"):
        metadata["job_description_length"] = len(data["job_description"])
    if task_type == "generate_resume":
        metadata["style"] = data.get("style", "")
    metadata["request_input_tokens"] = estimate_tokens({"task_type": task_type, "payload": data, "model": model_name})
    return metadata


def new_task(db: Session, user: User, task_type: str, payload: Any, resume_id: int | None = None):
    if task_type not in ACTIVE_AI_FEATURE_TYPES:
        raise AppException("该 AI 功能已停用")
    is_admin = getattr(user, "role", "") == "admin"
    requested_model_id = getattr(payload, "model_config_id", None) if task_type == "ai_chat" else None
    config = (
        get_ai_config_by_id(db, int(requested_model_id), require_chat_selectable=not is_admin)
        if requested_model_id
        else get_default_chat_ai_config(db) if task_type == "ai_chat" else get_active_ai_config(db)
    )
    metadata = ai_metadata(task_type, payload, config.model_ref or config.name, config.id)
    metadata["model_name"] = config.name
    if task_type == "ai_chat":
        metadata["supports_multimodal"] = get_catalog_capabilities_sync().get(
            config.model_ref or "", {}
        ).get("multimodal")
        metadata["billing_override"] = config.chat_billing_override()
    request_tokens = int(metadata.get("request_input_tokens") or 0)
    charge_on_finish = task_type == "ai_chat"
    precheck_flow_points(
        db,
        user,
        task_type,
        input_tokens=0 if charge_on_finish else request_tokens,
        pricing_override=metadata.get("billing_override") if charge_on_finish else None,
    )
    task = create_ai_task(
        db,
        user.id,
        task_type,
        resume_id=resume_id if resume_id is not None else getattr(payload, "resume_id", None),
        input_data=metadata,
        model_name=config.model_ref or config.name,
    )
    if not charge_on_finish:
        consume_flow_points(
            db,
            user,
            task_type,
            task=task,
            tokens_used=request_tokens,
            input_tokens=request_tokens,
            description=f"{ai_feature_label(task_type)}调用扣减",
        )
    return task


def ai_feature_label(task_type: str) -> str:
    labels = {
        "generate_resume": "AI 简历生成",
        "import_resume": "导入简历",
        "ai_chat": "AI 对话",
        "resume_score": "简历诊断",
        "jd_optimize": "JD 优化",
        "resume_translate": "简历翻译",
        "section_optimize": "AI 润色",
        "redeem": "兑换码充值",
        "admin_adjust": "管理员调整",
        "admin_grant_all": "全员发放",
        "admin_grant_all_revoke": "全员发放撤回",
        "signup_gift": "注册赠送",
    }
    return labels.get(task_type, task_type)


def user_visible_task_model_name(task: AiTask | None) -> str | None:
    if not task or task.task_type != "ai_chat":
        return None
    input_data = task.input_data or {}
    return str(input_data.get("model_name") or "AI 对话模型")


def _ndjson_stream_response(iterator) -> StreamingResponse:
    return StreamingResponse(
        iterator,
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _ai_service_stream_response(
    db: Session,
    user: User,
    resume: Resume,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    model_config_id: int | None = None,
) -> StreamingResponse:
    """Proxy a chat turn to the AI service, persisting messages in MySQL.

    The AI service stream is transparent to the client except that the
    ``result`` event's ``assistant_message`` is the DB-backed row (MySQL is the
    single source of truth for the UI message list).
    """
    events = stream_ai_service_chat(db, user, resume, content, attachments, model_config_id)

    async def iterator():
        try:
            async for event in events:
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # defensive: never let a relay failure surface as a raw 500
            yield json.dumps({"type": "error", "message": f"AI 对话处理失败：{exc}"}, ensure_ascii=False) + "\n"

    return _ndjson_stream_response(iterator())


def _ai_service_task_stream_response(
    db: Session,
    task: AiTask,
    task_type: str,
    input_payload: dict[str, Any],
    *,
    model_ref: str | None = None,
    normalize_context: dict[str, Any] | None = None,
) -> StreamingResponse:
    """Proxy a one-shot AI-service task (ticket 09) and settle its Flow Points.

    The AI service parses the model's JSON output and echoes aggregated
    per-model token usage on the ``result`` event, so this relay settles the
    pre-created AiTask synchronously — the same finish/refund path the legacy
    LangChain stream used, minus the estimated-token tracker.
    """
    events = ai_service_task_events(task_type, input_payload, model_ref=model_ref)

    async def iterator():
        finished = False
        try:
            async for event in events:
                event_type = event.get("type")
                if event_type == "result":
                    result_data = event.get("data") or {}
                    usage_entries = event.get("usage") or []
                    if not isinstance(result_data.get("resume_data"), dict):
                        message = "AI 返回的生成结果缺少 resume_data"
                        finish_ai_task(db, task, "failed", error_message=message)
                        finished = True
                        yield json.dumps({"type": "error", "message": message}, ensure_ascii=False) + "\n"
                        return
                    result_data["resume_data"] = normalize_resume_data(result_data["resume_data"], normalize_context or {})
                    token_usage = build_token_usage(
                        sum(int(item.get("input_tokens") or 0) for item in usage_entries),
                        sum(int(item.get("output_tokens") or 0) for item in usage_entries),
                        usage_entries,
                        estimated=False,
                    )
                    finish_ai_task(
                        db,
                        task,
                        "success",
                        output_data={"stream": True, "result": result_data},
                        token_usage=token_usage,
                    )
                    db.refresh(task)
                    finished = True
                    yield json.dumps(
                        {"type": "result", "data": {**result_data, "usage": ai_task_usage_payload(task)}},
                        ensure_ascii=False,
                    ) + "\n"
                elif event_type == "error":
                    finish_ai_task(db, task, "failed", error_message=str(event.get("message") or "AI 调用失败"))
                    finished = True
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                else:
                    yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # defensive: never let a relay failure surface as a raw 500
            if not finished:
                finish_ai_task(db, task, "failed", error_message=str(exc) or "AI 调用失败")
            yield json.dumps({"type": "error", "message": f"AI 任务处理失败：{exc}"}, ensure_ascii=False) + "\n"
        finally:
            if not finished:
                finish_ai_task(db, task, "failed", error_message="前端连接已断开，AI 流式响应未完成")

    return _ndjson_stream_response(iterator())


def page_data(items: list[dict[str, Any]], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def ai_task_data(task: AiTask, resume_title: str | None = None) -> dict[str, Any]:
    input_data = dict(task.input_data or {})
    input_data["token_usage"] = normalize_token_usage(input_data, task.tokens_used or 0)
    input_data.pop("model", None)
    input_data.pop("model_config_id", None)
    input_data.pop("billing_override", None)
    return {
        "id": task.id,
        "resume_id": task.resume_id,
        "resume_title": resume_title,
        "task_type": task.task_type,
        "task_label": ai_feature_label(task.task_type),
        "status": task.status,
        "model_name": user_visible_task_model_name(task),
        "points_used": point_number(task.points_used),
        "tokens_used": task.tokens_used,
        "input_data": input_data,
        "output_data": task.output_data or {},
        "error_message": task.error_message,
        "create_time": task.create_time,
        "update_time": task.update_time,
    }


@router.get("/capability")
async def ai_capability(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return success(await public_ai_capability(db, include_all_chat_models=current_user.role == "admin"))


@router.get("/records")
def my_ai_records(
    page: int = 1,
    page_size: int = 20,
    task_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    query = (
        select(AiTask, Resume.title)
        .outerjoin(Resume, (Resume.id == AiTask.resume_id) & (Resume.user_id == current_user.id))
        .where(AiTask.user_id == current_user.id)
    )
    count_query = select(func.count()).select_from(AiTask).where(AiTask.user_id == current_user.id)
    if task_type:
        query = query.where(AiTask.task_type == task_type)
        count_query = count_query.where(AiTask.task_type == task_type)
    total = db.scalar(count_query) or 0
    rows = db.execute(
        query.order_by(AiTask.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return success(page_data([ai_task_data(task, resume_title) for task, resume_title in rows], total, page, page_size))


@router.get("/histories")
def my_ai_histories(
    task_type: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if task_type not in {"resume_score", "jd_optimize", "resume_translate"}:
        raise AppException("只支持查看简历诊断、JD 优化和简历翻译历史")
    return my_ai_records(page, page_size, task_type, db, current_user)


@router.get("/flow-points")
def my_flow_points(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_default_point_rules(db)
    spent = db.scalar(
        select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0))
        .where(FlowPointTransaction.user_id == current_user.id, FlowPointTransaction.points_delta < 0)
    ) or 0
    earned = db.scalar(
        select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0))
        .where(FlowPointTransaction.user_id == current_user.id, FlowPointTransaction.points_delta > 0)
    ) or 0
    rules = list(
        db.scalars(
            select(FlowPointRule)
            .where(FlowPointRule.feature_type.in_(ACTIVE_AI_FEATURE_TYPES))
            .order_by(FlowPointRule.id.asc())
        )
    )
    return success(
        {
            "balance": point_number(current_user.flow_points),
            "spent": point_number(abs(spent)),
            "earned": point_number(earned),
            "hint": get_setting(db, "ai_records_hint"),
            "rules": [
                {
                    "feature_type": item.feature_type,
                    "display_name": item.display_name,
                    "points_per_call": point_number(item.points_per_call),
                    "points_per_1k_tokens": point_number(item.points_per_1k_tokens),
                    "points_per_million_tokens": point_number(item.points_per_million_tokens),
                    "points_per_million_input_tokens": point_number(item.points_per_million_input_tokens),
                    "points_per_million_output_tokens": point_number(item.points_per_million_output_tokens),
                    "enabled": item.enabled,
                }
                for item in rules
            ],
        }
    )


@router.get("/flow-points/transactions")
def my_flow_point_transactions(
    page: int = 1,
    page_size: int = 20,
    feature_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    condition = FlowPointTransaction.user_id == current_user.id
    if feature_type:
        condition = condition & (FlowPointTransaction.feature_type == feature_type)
    total = db.scalar(select(func.count()).select_from(FlowPointTransaction).where(condition)) or 0
    rows = list(
        db.execute(
            select(FlowPointTransaction, AiTask)
            .outerjoin(AiTask, AiTask.id == FlowPointTransaction.task_id)
            .where(condition)
            .order_by(FlowPointTransaction.create_time.desc(), FlowPointTransaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [
        {
            "id": item.id,
            "feature_type": item.feature_type,
            "feature_label": ai_feature_label(item.feature_type),
            "points_delta": point_number(item.points_delta),
            "balance_after": point_number(item.balance_after),
            "tokens_used": item.tokens_used,
            "model_name": user_visible_task_model_name(task),
            "description": item.description,
            "create_time": item.create_time,
        }
        for item, task in rows
    ]
    return success(page_data(items, total, page, page_size))


@router.post("/flow-points/redeem")
def redeem_points(
    payload: FlowPointRedeemRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = redeem_flow_points(db, current_user, payload.code, get_client_ip(request))
    return success(
        {
            "points": point_number(record.points),
            "balance": point_number(current_user.flow_points),
        },
        "兑换成功",
    )


@router.post("/generate-resume/stream")
def generate_resume_stream_api(payload: GenerateResumeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 先解析模型配置再扣点建任务：解析失败时直接报错，不留一个永远 pending、
    # 无法退款的任务。
    config = get_active_ai_config(db)
    task = new_task(db, current_user, "generate_resume", payload)
    return _ai_service_task_stream_response(
        db,
        task,
        "generate_resume",
        ai_payload(payload),
        model_ref=config.model_ref,
        normalize_context=ai_payload(payload),
    )


@router.get("/resume-chat/{resume_id}/messages")
def resume_chat_messages(resume_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_resume(db, current_user.id, resume_id)
    return success([item.model_dump() for item in list_chat_messages(db, current_user.id, resume_id)])


@router.post("/resume-chat/{resume_id}/messages/stream")
def resume_chat_send_stream(
    resume_id: int,
    payload: AiChatSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = get_resume(db, current_user.id, resume_id)
    # 对话代理到 Node AI 服务，前端播放其事件流。context（简历快照 + 最近
    # N 条历史 + 待确认变更摘要）由 FastAPI 从 MySQL 读出并注入，AI 服务保持
    # 无状态；消息在 MySQL 落库，UI 列表读取现有会话表。
    content = (payload.content or "").strip()
    if not content:
        async def empty_message():
            yield json.dumps({"type": "error", "message": "请输入要咨询的问题"}, ensure_ascii=False) + "\n"

        return _ndjson_stream_response(empty_message())
    attachments = [item.model_dump() for item in payload.attachments]
    return _ai_service_stream_response(db, current_user, resume, content, attachments, payload.model_config_id)


@router.post("/resume-chat/{resume_id}/messages/{message_id}/regenerate/stream")
def resume_chat_regenerate_stream(
    resume_id: int,
    message_id: int,
    payload: AiChatRegenerateRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = get_resume(db, current_user.id, resume_id)
    events = stream_ai_service_regenerate(
        db,
        current_user,
        resume,
        message_id,
        override_content=payload.content if payload and payload.content is not None else None,
        override_attachments=[item.model_dump() for item in payload.attachments]
        if payload and payload.attachments is not None
        else None,
        model_config_id=payload.model_config_id if payload else None,
    )

    async def iterator():
        try:
            async for event in events:
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # defensive: never let a relay failure surface as a raw 500
            yield json.dumps({"type": "error", "message": f"AI 对话处理失败：{exc}"}, ensure_ascii=False) + "\n"

    return _ndjson_stream_response(iterator())


@router.post("/resume-chat/{resume_id}/messages/{message_id}/decision")
def resume_chat_decision(
    resume_id: int,
    message_id: int,
    payload: AiChatDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = get_resume(db, current_user.id, resume_id)
    assistant_message, resume_data = resolve_chat_change(
        db, current_user, resume, message_id, payload.action, payload.merged_resume_data
    )
    return success(
        AiChatDecisionResponse(
            assistant_message=assistant_message,
            resume_data=resume_data,
        ).model_dump()
    )


@router.delete("/resume-chat/{resume_id}/messages")
def resume_chat_clear(resume_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_resume(db, current_user.id, resume_id)
    clear_chat_messages(db, current_user.id, resume_id)
    return success(True)


@router.post("/service/tool/update-basics-field")
def service_update_basics_field(
    payload: UpdateBasicsFieldRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_ai_service_token),
):
    """Internal tool callback: validate the agent's field-level basics change.

    The Node AI service calls this when its ``update_basics_field`` tool fires. It
    loads the resume and recent conversation from MySQL (source of truth), runs the
    numeric anti-hallucination guard, and returns the validated pending-change
    payload. It does NOT persist here — ``stream_ai_service_chat`` writes the
    pending-change row when the agent produces its ``result`` event, keeping MySQL
    the single mutation point.
    """
    resume = get_resume(db, payload.user_id, payload.resume_id)
    history = [
        {"role": item.role, "content": item.content}
        for item in list_chat_messages(db, payload.user_id, payload.resume_id, limit=40)
    ]
    user_message = next(
        (item["content"] for item in reversed(history) if item.get("role") == "user"),
        "",
    )
    optimized_data, suggestions, issue = validate_basics_field_update(
        resume.resume_data,
        payload.basics,
        history,
        user_message,
    )
    if issue:
        return success({"ok": False, "category": tool_issue_category(issue), "retryable": False, "reason": issue})
    return success({"ok": True, "optimized_resume_data": optimized_data, "suggestions": suggestions})


@router.post("/service/tool/update-section")
def service_update_section(
    payload: UpdateSectionRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_ai_service_token),
):
    """Internal tool callback: validate a section-level change.

    Called by the Node AI service's ``update_section`` tool. Loads the resume and
    recent conversation from MySQL, merges the proposed section change by item id
    (so only named entries change), runs the numeric anti-hallucination guard, and
    returns the validated pending payload. It does NOT persist here — the
    ``stream_ai_service_chat`` result event writes the pending-change row, keeping
    MySQL the single mutation point.
    """
    resume = get_resume(db, payload.user_id, payload.resume_id)
    history = [
        {"role": item.role, "content": item.content}
        for item in list_chat_messages(db, payload.user_id, payload.resume_id, limit=40)
    ]
    user_message = next(
        (item["content"] for item in reversed(history) if item.get("role") == "user"),
        "",
    )
    optimized_data, suggestions, issue = validate_section_update(
        resume.resume_data,
        payload.section,
        payload.proposed,
        history,
        user_message,
        payload.removed_ids,
    )
    if issue:
        return success({"ok": False, "category": tool_issue_category(issue), "retryable": False, "reason": issue})
    return success({"ok": True, "optimized_resume_data": optimized_data, "suggestions": suggestions})


@router.post("/service/tool/propose-change")
def service_propose_change(
    payload: ProposeChangeRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_ai_service_token),
):
    """Internal tool callback: validate an atomic change spanning several sections.

    Called by the Node AI service's ``propose_change`` tool. Applies all section
    updates to one snapshot (id-bounded merge), runs a single numeric anti-hallucination
    guard, and returns the staged pending payload — without persisting here.
    """
    resume = get_resume(db, payload.user_id, payload.resume_id)
    history = [
        {"role": item.role, "content": item.content}
        for item in list_chat_messages(db, payload.user_id, payload.resume_id, limit=40)
    ]
    user_message = next(
        (item["content"] for item in reversed(history) if item.get("role") == "user"),
        "",
    )
    optimized_data, suggestions, issue = validate_propose_change(
        resume.resume_data,
        payload.updates,
        history,
        user_message,
    )
    if issue:
        return success({"ok": False, "category": tool_issue_category(issue), "retryable": False, "reason": issue})
    return success({"ok": True, "optimized_resume_data": optimized_data, "suggestions": suggestions})


@router.get("/service/tool/get-resume-context")
def service_get_resume_context(
    user_id: int,
    resume_id: int,
    sections: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_ai_service_token),
):
    """Internal tool callback: read the requested section(s) on demand.

    The Node AI service calls this when its ``get_resume_context`` tool fires, so the
    agent can pull exactly the section it needs instead of shipping the whole resume.
    Reads-only, never mutates.
    """
    resume = get_resume(db, user_id, resume_id)
    ctx = get_section_context(resume.resume_data, sections)
    return success({"ok": True, "sections": ctx})


@router.post("/service/usage")
def report_ai_service_usage(
    payload: AiServiceUsageRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Settle Flow Points for a finished AI-service chat turn (ticket 07).

    The Node AI service aggregates provider-reported token usage per model from
    the pi event stream and posts it here once the turn ends. Each entry is
    billed at its own model granularity: the config mapped by ``model_ref``
    supplies the chat rates (points_per_call + per-million-token), and the
    settlement goes through ``settle_task_flow_points`` — the exact ledger path
    the legacy LangChain chat uses, so balances and transactions reconcile.

    An unmapped ref still bills (never lose a charge) at the default ai_chat
    rule. Failed turns are never reported, matching the legacy refund rule.
    """
    require_ai_service_token(request)
    user = db.get(User, payload.user_id)
    if not user:
        raise AppException("用户不存在", 404)

    # 对话回合的功能维度：快捷指令取其规范名，自由对话记 "chat"；未知值丢弃。
    feature = payload.feature if payload.feature in AI_CHAT_FEATURES else None

    # 失败回合：不结算任何积分，只落一条 failed 任务行，让成功率统计反映真实失败。
    if payload.status == "failed":
        task = create_ai_task(
            db,
            user.id,
            "ai_chat",
            resume_id=payload.resume_id,
            model_name=None,
            feature=feature,
        )
        finish_ai_task(db, task, "failed", error_message=(payload.error_message or "AI 回合失败")[:4000])
        return success({"entries": []})

    entries: list[dict[str, Any]] = []
    for item in payload.usage:
        ref = f"{item.provider.strip()}/{item.model.strip()}"
        config = get_ai_config_by_model_ref(db, ref)
        token_usage = build_token_usage(
            item.input_tokens,
            item.output_tokens,
            estimated=False,
        )
        input_data: dict[str, Any] = {
            "model": ref,
            "model_name": config.name if config else ref,
            "pi_model_ref": ref,
            "token_usage": token_usage,
            "billing_override": config.chat_billing_override() if config else None,
        }
        if payload.tool_calls:
            input_data["tool_calls"] = payload.tool_calls
        task = create_ai_task(
            db,
            user.id,
            "ai_chat",
            resume_id=payload.resume_id,
            input_data=input_data,
            model_name=input_data["model_name"],
            feature=feature,
        )
        try:
            finish_ai_task(db, task, "success", output_data={"source": "ai_service_usage"}, token_usage=token_usage)
        except AppException as exc:
            # 结算不足（402）等失败：按旧链路口径把任务落成 failed（退还预扣），
            # 不能让已建任务永远悬挂在 pending。
            finish_ai_task(db, task, "failed", error_message=exc.message)
            entries.append(
                {
                    "pi_model_ref": ref,
                    "mapped": config is not None,
                    "model_config_id": config.id if config else None,
                    "points_used": point_number(task.points_used),
                    "tokens_used": int(task.tokens_used or 0),
                    "requests": item.requests,
                    "billed": False,
                    "error": exc.message,
                }
            )
            continue
        entries.append(
            {
                "pi_model_ref": ref,
                "mapped": config is not None,
                "model_config_id": config.id if config else None,
                "points_used": point_number(task.points_used),
                "tokens_used": int(task.tokens_used or 0),
                "requests": item.requests,
                "billed": True,
            }
        )
    return success({"entries": entries})
