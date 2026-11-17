from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.api.admin.common import WINDOW_DAYS
from app.models.ai_config import AiModelConfig, FlowPointRedeemCode, FlowPointRedeemRecord, FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.export_record import ExportRecord
from app.models.resume import Resume, UploadedFile
from app.models.user import User
from app.services.ai.token_usage import normalize_token_usage
from app.services.flow_points_service import point_amount, point_number


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


# 上游美元价 → 点数 的既定换算系数（定价页把「点数/百万」定为美元价 × 21600）。
# 反算成本时用它还原上游单价：美元/百万 = 点数/百万 ÷ 21600。
_POINTS_PER_USD = Decimal("21600")

# 失败原因去重上限：先取原始分组，归一化后再合并同名原因。
_FAILURE_REASON_SCAN = 60
_FAILURE_REASON_LIMIT = 8


def _breakdown_key(task_type: object, feature: object) -> str:
    """功能分布的聚合键：对话回合取 feature，其余（feature 为 NULL 的历史任务）取 task_type。"""
    if str(task_type) == "ai_chat" and feature:
        return str(feature)
    return str(task_type)


# 工具失败类别的固定展示顺序；类别缺失的历史失败步骤归入 unknown。
_TRACE_CATEGORIES = ("validation", "business", "infra", "unknown")

# 回合环节的固定展示顺序；loader/session/provider 是首字延迟的细分，前端把它们
# 折叠进「首字响应」的下钻面板。
_TURN_STAGE_ORDER = ("first_token_ms", "loader_ms", "session_create_ms", "provider_ttfb_ms", "generation_ms", "total_ms")


def _percentile(durations: list[int], ratio: float) -> int | None:
    if not durations:
        return None
    ordered = sorted(durations)
    return ordered[math.ceil(len(ordered) * ratio) - 1]


def _p95(durations: list[int]) -> int | None:
    """nearest-rank p95；样本 <20 时分布不稳，返回 None 让前端隐藏。"""
    return _percentile(durations, 0.95) if len(durations) >= 20 else None


def _stage_quality_breakdown(rows: list) -> dict:
    """Aggregate per-stage latency + failure quality (对话环节质量卡).

    ``rows`` are (input_data, task_status) pairs, newest-first (the caller
    orders by id desc and caps the scan). A row's ``turn_timing`` dict feeds
    the turn stages (回合环节), its ``tool_quality`` steps feed one stage per
    tool name — tools and turn phases are sibling stages in one card. A stage's
    近10次 sample is the first 10 durations seen in row order;
    ``recent_delta_pct`` compares it against the overall average. Failed task
    rows count toward the turn failure rate (denominator = rows carrying a turn
    sample, timed or failed).
    """

    def _new_stage() -> dict[str, Any]:
        return {"samples": 0, "durations": [], "recent": [], "success": 0, "failures": {}, "failure_durations": {}, "reasons": []}

    stages: dict[str, dict[str, Any]] = {}
    turns = 0
    turn_rows = 0
    failed_rows = 0
    for input_data, status in rows:
        is_failed = status == "failed"
        if is_failed:
            failed_rows += 1
        if isinstance(input_data, dict):
            timing = input_data.get("turn_timing")
            if isinstance(timing, dict):
                raw_total = timing.get("total_ms")
                if isinstance(raw_total, (int, float)) and raw_total >= 0:
                    turn_rows += 1
                    turns += 1
                    total = int(raw_total)
                    raw_tool = timing.get("tool_ms")
                    tool = int(raw_tool) if isinstance(raw_tool, (int, float)) and raw_tool >= 0 else 0
                    phase_samples = (
                        ("first_token_ms", timing.get("first_token_ms")),
                        ("loader_ms", timing.get("loader_ms")),
                        ("session_create_ms", timing.get("session_create_ms")),
                        ("provider_ttfb_ms", timing.get("provider_ttfb_ms")),
                        ("generation_ms", max(total - tool, 0)),
                        ("total_ms", total),
                    )
                    for key, value in phase_samples:
                        if isinstance(value, (int, float)) and value >= 0:
                            item = stages.setdefault(key, _new_stage())
                            duration = int(value)
                            item["samples"] += 1
                            item["durations"].append(duration)
                            if len(item["recent"]) < 10:
                                item["recent"].append(duration)
                elif is_failed:
                    turn_rows += 1
            elif is_failed:
                turn_rows += 1
            for step in input_data.get("tool_quality") or []:
                if not isinstance(step, dict) or step.get("status") != "end":
                    continue
                item = stages.setdefault(str(step.get("name") or "unknown"), _new_stage())
                item["samples"] += 1
                raw_duration = step.get("duration_ms")
                duration = int(raw_duration) if isinstance(raw_duration, (int, float)) and raw_duration >= 0 else None
                if duration is not None:
                    item["durations"].append(duration)
                    if len(item["recent"]) < 10:
                        item["recent"].append(duration)
                if step.get("ok") is True:
                    item["success"] += 1
                else:
                    category = str(step.get("category") or "unknown")
                    item["failures"][category] = item["failures"].get(category, 0) + 1
                    if duration is not None:
                        item["failure_durations"].setdefault(category, []).append(duration)
                    # 行序即新→旧：reason 去重后保留最近出现的说法，最多 3 条
                    reason = step.get("reason")
                    if isinstance(reason, str) and reason and reason not in item["reasons"] and len(item["reasons"]) < 3:
                        item["reasons"].append(reason)
        elif is_failed:
            turn_rows += 1

    def _pack(item: dict[str, Any]) -> dict[str, Any]:
        durations: list[int] = item["durations"]
        avg = round(sum(durations) / len(durations)) if durations else None
        recent = item["recent"]
        recent_avg = round(sum(recent) / len(recent)) if recent else None
        # 单样本（样本数 <2）没有趋势可言，delta 置空让前端显示「—」
        delta = round((recent_avg - avg) * 100 / avg) if avg and recent_avg is not None and avg > 0 and len(durations) >= 2 else None
        return {
            "samples": item["samples"],
            "avg_ms": avg,
            # p50（中位数）不受离群值影响：与 avg 差距大说明分布被尾部拉偏
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _p95(durations),
            "max_ms": max(durations) if durations else None,
            "recent_avg_ms": recent_avg,
            "recent_delta_pct": delta,
        }

    stage_list = []
    for key in _TURN_STAGE_ORDER:
        item = stages.pop(key, None)
        if item and item["samples"]:
            stage_list.append({"key": key, "kind": "turn", **_pack(item)})
    for key, item in sorted(stages.items(), key=lambda entry: entry[1]["samples"], reverse=True):
        if not item["samples"]:
            continue
        failure_pack = []
        for category in list(_TRACE_CATEGORIES) + sorted(set(item["failures"]) - set(_TRACE_CATEGORIES)):
            durations = item["failure_durations"].get(category, [])
            failure_pack.append({
                "category": category,
                "count": item["failures"].get(category, 0),
                "avg_ms": round(sum(durations) / len(durations)) if durations else None,
            })
        stage_list.append({
            "key": key,
            "kind": "tool",
            **_pack(item),
            "success_rate": round(item["success"] * 100 / item["samples"], 1),
            "failures": failure_pack,
            "recent_reasons": item["reasons"],
        })
    return {
        "turns": turns,
        "turn_failure_rate": round(failed_rows * 100 / turn_rows, 1) if turn_rows else 0.0,
        "stages": stage_list,
    }


def _resolve_window(key: str) -> dict[str, Any]:
    """把前端传的 range 收敛成 [start, now) 与同长度的上一窗口 [prev_start, start)。"""
    normalized = key if key in WINDOW_DAYS else "7d"
    days = WINDOW_DAYS[normalized]
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    return {
        "key": normalized,
        "days": days,
        "now": now,
        "today": today,
        "start": start,
        "prev_start": start - timedelta(days=days),
    }


def _pct_delta(value: float, prev: float) -> float | None:
    """环比百分比；上一窗口为 0 时无可比基数，返回 None 让前端显示「—」。"""
    if prev <= 0:
        return None
    return round((value - prev) * 100 / prev, 1)


def _kpi(value: float, prev: float) -> dict[str, Any]:
    return {"value": value, "prev": prev, "delta_pct": _pct_delta(value, prev)}


def _day_key(raw: object) -> str:
    """`func.date()` 在 MySQL 返回 date、SQLite 返回字符串，统一成 ISO 日期。"""
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    return str(raw)[:10]


def _active_user_ids(db: Session, start: datetime) -> set[int]:
    """窗口内有过任一动作的去重用户（简历新建/编辑、AI 调用、导出、分享）。"""
    ids: set[int] = set()
    ids.update(
        uid
        for uid in db.scalars(
            select(Resume.user_id).where(or_(Resume.create_time >= start, Resume.update_time >= start))
        ).all()
        if uid
    )
    ids.update(uid for uid in db.scalars(select(AiTask.user_id).where(AiTask.create_time >= start)).all() if uid)
    ids.update(uid for uid in db.scalars(select(ExportRecord.user_id).where(ExportRecord.create_time >= start)).all() if uid)
    ids.update(uid for uid in db.scalars(select(Resume.user_id).where(Resume.share_created_time >= start)).all() if uid)
    return ids


def _redeem_revenue_query():
    revenue_expr = case(
        (FlowPointRedeemRecord.price > 0, FlowPointRedeemRecord.price),
        else_=func.coalesce(FlowPointRedeemCode.price, 0),
    )
    return (
        select(func.coalesce(func.sum(revenue_expr), 0))
        .select_from(FlowPointRedeemRecord)
        .outerjoin(FlowPointRedeemCode, FlowPointRedeemCode.id == FlowPointRedeemRecord.code_id)
    )


def _redeem_revenue(db: Session, start: datetime | None = None, end: datetime | None = None) -> float:
    query = _redeem_revenue_query()
    if start is not None:
        query = query.where(FlowPointRedeemRecord.create_time >= start)
    if end is not None:
        query = query.where(FlowPointRedeemRecord.create_time < end)
    return point_amount(db.scalar(query) or 0)


def _points_consumed(db: Session, start: datetime | None = None) -> float:
    query = select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0)).where(
        FlowPointTransaction.points_delta < 0
    )
    if start is not None:
        query = query.where(FlowPointTransaction.create_time >= start)
    return point_amount(abs(db.scalar(query) or 0))


def _funnel(db: Session, start: datetime) -> dict[str, Any]:
    """新用户转化漏斗：注册 → 建简历 → 用过 AI → 导出过。

    以窗口内注册的用户为 cohort（他们后续任一时间的动作都算），窗口内没有新注册
    时退回全站口径，避免看板整块空白。
    """
    cohort = db.scalar(select(func.count()).select_from(User).where(User.create_time >= start)) or 0
    if cohort:
        basis = "cohort"
        scope = select(User.id).where(User.create_time >= start)
        total = cohort
    else:
        basis = "all"
        scope = select(User.id)
        total = db.scalar(select(func.count()).select_from(User)) or 0
    if not total:
        return {"basis": basis, "steps": []}

    def _reached(model, column) -> int:
        return db.scalar(select(func.count(func.distinct(column))).where(column.in_(scope))) or 0

    steps = [
        {"key": "registered", "label": "注册", "value": total},
        {"key": "resume", "label": "创建简历", "value": _reached(Resume, Resume.user_id)},
        {"key": "ai", "label": "用过 AI", "value": _reached(AiTask, AiTask.user_id)},
        {"key": "export", "label": "导出过", "value": _reached(ExportRecord, ExportRecord.user_id)},
    ]
    for step in steps:
        step["rate"] = round(step["value"] * 100 / total, 1)
    return {"basis": basis, "steps": steps}


def _normalize_reason(text: str) -> str:
    """失败原因归一：折掉空白与换行，截断过长内容，方便同名原因合并计数。"""
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= 140 else collapsed[:137] + "..."


def _failure_reasons(db: Session, start: datetime, failed_total: int) -> list[dict[str, Any]]:
    """窗口内失败任务的 error_message Top N（归一化后合并同名原因）。"""
    rows = db.execute(
        select(AiTask.error_message, func.count())
        .where(
            AiTask.status == "failed",
            AiTask.create_time >= start,
            AiTask.error_message.isnot(None),
            AiTask.error_message != "",
        )
        .group_by(AiTask.error_message)
        .order_by(func.count().desc())
        .limit(_FAILURE_REASON_SCAN)
    ).all()
    merged: dict[str, int] = {}
    for message, count in rows:
        reason = _normalize_reason(message)
        merged[reason] = merged.get(reason, 0) + int(count or 0)
    ordered = sorted(merged.items(), key=lambda item: item[1], reverse=True)[:_FAILURE_REASON_LIMIT]
    return [
        {
            "reason": reason,
            "count": count,
            "share": round(count * 100 / failed_total, 1) if failed_total else 0.0,
        }
        for reason, count in ordered
    ]


def _model_pricing(db: Session) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    """模型定价表：ref → (输入美元/百万, 输出美元/百万)，以及裸模型 id → ref 的唯一映射。

    只收录输入输出价都有值的配置；缺价的模型不计成本（宁缺勿错）。
    """
    price_by_ref: dict[str, tuple[float, float]] = {}
    refs_by_bare: dict[str, list[str]] = {}
    for config in db.scalars(select(AiModelConfig)).all():
        ref = str(getattr(config, "model_ref", None) or "").strip()
        if not ref:
            continue
        refs_by_bare.setdefault(ref.split("/", 1)[-1], []).append(ref)
        raw_input = getattr(config, "chat_points_per_million_input_tokens", None)
        raw_output = getattr(config, "chat_points_per_million_output_tokens", None)
        if raw_input is None or raw_output is None:
            continue
        price_by_ref[ref] = (
            float(Decimal(str(raw_input)) / _POINTS_PER_USD),
            float(Decimal(str(raw_output)) / _POINTS_PER_USD),
        )
    bare_to_ref = {bare: refs[0] for bare, refs in refs_by_bare.items() if len(refs) == 1}
    return price_by_ref, bare_to_ref


def _resolve_model_key(input_data: dict, raw_model_name: str | None, bare_to_ref: dict[str, str]) -> str:
    """历史任务的 model_name 口径不一（裸 id / 策略名 / ref），统一收敛到目录 ref。"""
    candidate = str((input_data or {}).get("model") or "").strip()
    if candidate and "/" not in candidate and candidate in bare_to_ref:
        candidate = bare_to_ref[candidate]
    if candidate:
        return candidate
    fallback = str(raw_model_name or (input_data or {}).get("model_name") or "").strip()
    if fallback and "/" not in fallback and fallback in bare_to_ref:
        fallback = bare_to_ref[fallback]
    return fallback


@router.get("/dashboard")
def dashboard(window: str = Query("7d", alias="range"), db: Session = Depends(get_db)):
    resolved = _resolve_window(window)
    start: datetime = resolved["start"]
    prev_start: datetime = resolved["prev_start"]
    today: datetime = resolved["today"]
    now: datetime = resolved["now"]

    def _count(model, *conditions) -> int:
        stmt = select(func.count()).select_from(model)
        if conditions:
            stmt = stmt.where(*conditions)
        return db.scalar(stmt) or 0

    total_users = _count(User)
    total_resumes = _count(Resume)
    total_ai = _count(AiTask)
    total_exports = _count(ExportRecord)
    first_user_time = db.scalar(select(func.min(User.create_time)))
    operating_days = (today.date() - first_user_time.date()).days + 1 if first_user_time else 0
    storage_bytes = db.scalar(select(func.coalesce(func.sum(UploadedFile.file_size), 0))) or 0

    # ---- 窗口内计数 + 上一窗口同比 ----
    window_ai = _count(AiTask, AiTask.create_time >= start)
    window_ai_failed = _count(AiTask, AiTask.create_time >= start, AiTask.status == "failed")
    window_ai_success = _count(AiTask, AiTask.create_time >= start, AiTask.status == "success")
    prev_ai = _count(AiTask, AiTask.create_time >= prev_start, AiTask.create_time < start)
    prev_ai_failed = _count(AiTask, AiTask.create_time >= prev_start, AiTask.create_time < start, AiTask.status == "failed")
    window_resumes = _count(Resume, Resume.create_time >= start)
    prev_resumes = _count(Resume, Resume.create_time >= prev_start, Resume.create_time < start)
    window_exports = _count(ExportRecord, ExportRecord.create_time >= start)
    window_exports_success = _count(ExportRecord, ExportRecord.create_time >= start, ExportRecord.status == "success")
    prev_exports = _count(ExportRecord, ExportRecord.create_time >= prev_start, ExportRecord.create_time < start)
    window_users = _count(User, User.create_time >= start)
    prev_users = _count(User, User.create_time >= prev_start, User.create_time < start)
    active_now = len(_active_user_ids(db, start))
    active_prev = len(_active_user_ids(db, prev_start) - _active_user_ids(db, start))

    window_revenue = _redeem_revenue(db, start)
    prev_window_revenue = _redeem_revenue(db, prev_start, start)
    today_revenue = _redeem_revenue(db, today)

    # ---- 窗口内的 AI 任务一次性扫一遍，供 token / 模型 / 成本 / 按日 token 复用 ----
    task_rows = db.execute(
        select(
            AiTask.task_type,
            AiTask.feature,
            AiTask.create_time,
            AiTask.tokens_used,
            AiTask.input_data,
            AiTask.model_name,
            AiTask.points_used,
        ).where(AiTask.create_time >= start)
    ).all()
    price_by_ref, bare_to_ref = _model_pricing(db)

    token_summary = {"total": 0, "today": 0, "input": 0, "output": 0}
    token_by_type: dict[str, dict[str, int]] = {}
    points_by_type: dict[str, Decimal] = {}
    model_map: dict[str, dict[str, Any]] = {}
    tokens_by_day: dict[str, int] = {}
    cost_usd = 0.0
    priced_calls = 0
    for task_type, task_feature, create_time, fallback_tokens, input_data, raw_model_name, points_used in task_rows:
        payload = input_data or {}
        usage = normalize_token_usage(payload, fallback_tokens or 0)
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        total_tokens = int(usage["total_tokens"])

        breakdown_key = _breakdown_key(task_type, task_feature)
        item = token_by_type.setdefault(breakdown_key, {"total": 0, "input": 0, "output": 0})
        item["total"] += total_tokens
        item["input"] += input_tokens
        item["output"] += output_tokens
        points_by_type[breakdown_key] = points_by_type.get(breakdown_key, Decimal("0.00")) + point_amount(points_used or 0)
        token_summary["total"] += total_tokens
        token_summary["input"] += input_tokens
        token_summary["output"] += output_tokens
        if create_time and create_time >= today:
            token_summary["today"] += total_tokens
        if create_time:
            day_key = create_time.date().isoformat()
            tokens_by_day[day_key] = tokens_by_day.get(day_key, 0) + total_tokens

        model_key = _resolve_model_key(payload, raw_model_name, bare_to_ref)
        if model_key:
            model_item = model_map.setdefault(
                model_key,
                {
                    "model_name": model_key,
                    "model_raw_name": str(raw_model_name or ""),
                    "config_names": set(),
                    "total": 0,
                    "tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "points_used": Decimal("0.00"),
                },
            )
            config_name = payload.get("model_name")
            if config_name and str(config_name) != model_key:
                model_item["config_names"].add(str(config_name))
            model_item["total"] = int(model_item["total"]) + 1
            model_item["tokens"] = int(model_item["tokens"]) + total_tokens
            model_item["input_tokens"] = int(model_item["input_tokens"]) + input_tokens
            model_item["output_tokens"] = int(model_item["output_tokens"]) + output_tokens
            model_item["points_used"] = point_amount(model_item["points_used"]) + point_amount(points_used or 0)

        price = price_by_ref.get(model_key) if model_key else None
        if price is not None:
            priced_calls += 1
            cost_usd += input_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1]

    # ---- 功能分布（窗口内） ----
    breakdown_rows = db.execute(
        select(
            AiTask.task_type,
            AiTask.feature,
            func.count(AiTask.id),
            func.sum(case((AiTask.status == "success", 1), else_=0)),
            func.sum(case((AiTask.status == "failed", 1), else_=0)),
        )
        .where(AiTask.create_time >= start)
        .group_by(AiTask.task_type, AiTask.feature)
    ).all()
    breakdown_map: dict[str, dict[str, int]] = {}
    for task_type, task_feature, total, success_count, failed_count in breakdown_rows:
        item = breakdown_map.setdefault(_breakdown_key(task_type, task_feature), {"total": 0, "success": 0, "failed": 0})
        item["total"] += int(total)
        item["success"] += int(success_count or 0)
        item["failed"] += int(failed_count or 0)
    ai_breakdown = [
        {
            "task_type": key,
            **counts,
            "success_rate": round(counts["success"] * 100 / counts["total"], 1) if counts["total"] else 0.0,
            "points_used": point_number(points_by_type.get(key, Decimal("0.00"))),
            "tokens": token_by_type.get(key, {}).get("total", 0),
            "input_tokens": token_by_type.get(key, {}).get("input", 0),
            "output_tokens": token_by_type.get(key, {}).get("output", 0),
        }
        for key, counts in sorted(breakdown_map.items(), key=lambda value: value[1]["total"], reverse=True)
    ]

    # 对话环节质量：回合任务行上的 turn_timing（回合耗时）+ tool_quality（工具
    # 步骤）统一聚合，全部来自 ai-service 计费上报的事实行，与消息表解耦——
    # 删简历后口径仍与功能分布一致。按 id 降序取近 500 行，行序即新→旧，
    # 供「近10次」趋势样本使用。
    stage_rows = db.execute(
        select(AiTask.input_data, AiTask.status)
        .where(AiTask.task_type == "ai_chat", AiTask.input_data.isnot(None))
        .order_by(AiTask.id.desc())
        .limit(500)
    ).all()
    stage_quality = _stage_quality_breakdown([(row.input_data, row.status) for row in stage_rows])

    # ---- 按日趋势（窗口内，SQL 分组，不再逐日发查询） ----
    buckets: dict[str, dict[str, Any]] = {}
    for offset in range(resolved["days"]):
        day = (start + timedelta(days=offset)).date()
        buckets[day.isoformat()] = {
            "date": day.strftime("%m-%d"),
            "users": 0,
            "resumes": 0,
            "ai_tasks": 0,
            "exports": 0,
            "shares": 0,
            "tokens": 0,
            "points_consumed": 0.0,
        }

    def _spread(rows, field: str, *, points: bool = False) -> None:
        for raw_day, value in rows:
            bucket = buckets.get(_day_key(raw_day))
            if bucket is None:
                continue
            bucket[field] = round(float(value or 0), 2) if points else int(value or 0)

    _spread(
        db.execute(
            select(func.date(User.create_time), func.count())
            .where(User.create_time >= start)
            .group_by(func.date(User.create_time))
        ).all(),
        "users",
    )
    _spread(
        db.execute(
            select(func.date(Resume.create_time), func.count())
            .where(Resume.create_time >= start)
            .group_by(func.date(Resume.create_time))
        ).all(),
        "resumes",
    )
    _spread(
        db.execute(
            select(func.date(AiTask.create_time), func.count())
            .where(AiTask.create_time >= start)
            .group_by(func.date(AiTask.create_time))
        ).all(),
        "ai_tasks",
    )
    _spread(
        db.execute(
            select(func.date(ExportRecord.create_time), func.count())
            .where(ExportRecord.create_time >= start)
            .group_by(func.date(ExportRecord.create_time))
        ).all(),
        "exports",
    )
    _spread(
        db.execute(
            select(func.date(Resume.share_created_time), func.count())
            .where(Resume.share_created_time >= start)
            .group_by(func.date(Resume.share_created_time))
        ).all(),
        "shares",
    )
    _spread(
        db.execute(
            select(func.date(FlowPointTransaction.create_time), func.sum(FlowPointTransaction.points_delta))
            .where(FlowPointTransaction.points_delta < 0, FlowPointTransaction.create_time >= start)
            .group_by(func.date(FlowPointTransaction.create_time))
        ).all(),
        "points_consumed",
        points=True,
    )
    for day_key, tokens in tokens_by_day.items():
        if day_key in buckets:
            buckets[day_key]["tokens"] = tokens
    daily = list(buckets.values())

    # ---- 活跃时段（固定近 7 天，与窗口解耦：时段分布需要足够样本才有意义） ----
    hourly_start = today - timedelta(days=6)
    hourly_user_ids: list[set[int]] = [set() for _ in range(24)]
    hourly_actions = [0 for _ in range(24)]

    def add_hourly_activity(user_id: int | None, create_time: datetime | None) -> None:
        if not user_id or not create_time or create_time < hourly_start:
            return
        hour = create_time.hour
        hourly_user_ids[hour].add(int(user_id))
        hourly_actions[hour] += 1

    for user_id, create_time, update_time in db.execute(
        select(Resume.user_id, Resume.create_time, Resume.update_time).where(
            or_(Resume.create_time >= hourly_start, Resume.update_time >= hourly_start)
        )
    ).all():
        add_hourly_activity(user_id, create_time)
        if update_time != create_time:
            add_hourly_activity(user_id, update_time)

    for user_id, create_time in db.execute(
        select(AiTask.user_id, AiTask.create_time).where(AiTask.create_time >= hourly_start)
    ).all():
        add_hourly_activity(user_id, create_time)

    for user_id, create_time in db.execute(
        select(ExportRecord.user_id, ExportRecord.create_time).where(ExportRecord.create_time >= hourly_start)
    ).all():
        add_hourly_activity(user_id, create_time)

    for user_id, share_created_time in db.execute(
        select(Resume.user_id, Resume.share_created_time).where(Resume.share_created_time >= hourly_start)
    ).all():
        add_hourly_activity(user_id, share_created_time)

    hourly_activity = [
        {
            "hour": hour,
            "label": f"{hour:02d}:00",
            "active_users": len(user_ids),
            "actions": hourly_actions[hour],
        }
        for hour, user_ids in enumerate(hourly_user_ids)
    ]

    return success(
        {
            "range": {
                "key": resolved["key"],
                "days": resolved["days"],
                "start": start.isoformat(),
                "end": now.isoformat(),
            },
            # 顶部常驻 KPI 条：全部是窗口值 + 环比，没有累计值
            "kpi": {
                "active_users": _kpi(active_now, active_prev),
                "ai_calls": _kpi(window_ai, prev_ai),
                "new_users": _kpi(window_users, prev_users),
                "resumes": _kpi(window_resumes, prev_resumes),
                "exports": _kpi(window_exports, prev_exports),
                "revenue_cny": _kpi(window_revenue, prev_window_revenue),
                "failed_calls": _kpi(window_ai_failed, prev_ai_failed),
            },
            # 累计值只作为脚注，不做主指标
            "totals": {
                "users": total_users,
                "resumes": total_resumes,
                "ai_tasks": total_ai,
                "exports": total_exports,
                "storage_bytes": int(storage_bytes),
                "operating_days": operating_days,
                "launch_date": first_user_time.date().isoformat() if first_user_time else None,
            },
            "today": {
                "users": _count(User, User.create_time >= today),
                "active_users": active_now,
                "resumes": _count(Resume, Resume.create_time >= today),
                "ai_tasks": _count(AiTask, AiTask.create_time >= today),
                "exports": _count(ExportRecord, ExportRecord.create_time >= today),
            },
            "rates": {
                "ai_success": round(window_ai_success * 100 / window_ai, 1) if window_ai else 0,
                "export_success": round(window_exports_success * 100 / window_exports, 1) if window_exports else 0,
            },
            "tokens": {
                **token_summary,
                "avg_per_task": round(token_summary["total"] / window_ai, 1) if window_ai else 0,
            },
            "points": {
                "consumed": _points_consumed(db, start),
                "today_consumed": _points_consumed(db, today),
                "recharged": point_number(point_amount(db.scalar(
                    select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0)).where(
                        FlowPointTransaction.points_delta > 0
                    )
                ) or 0)),
            },
            "revenue": {
                "redeemed": _redeem_revenue(db),
                "window_redeemed": window_revenue,
                "today_redeemed": today_revenue,
            },
            # 成本按上游美元价估算；收入是点数兑换的人民币。两者币种不同，
            # 只用于看趋势，不做毛利（毛利需要汇率与售价口径，见看板说明）。
            "economics": {
                # 两位小数会把小额估算抹成 0.00，保留四位由前端决定展示精度
                "cost_usd": round(cost_usd, 4),
                "revenue_cny": window_revenue,
                "avg_cost_per_call_usd": round(cost_usd / window_ai, 4) if window_ai else 0,
                "priced_calls": priced_calls,
                "unpriced_calls": max(window_ai - priced_calls, 0),
            },
            "funnel": _funnel(db, start),
            "failure_reasons": _failure_reasons(db, start, window_ai_failed),
            "ai_breakdown": ai_breakdown,
            "model_breakdown": [
                {
                    **{key: value for key, value in item.items() if key not in {"points_used", "config_names"}},
                    "config_names": sorted(item.get("config_names") or []),
                    "points_used": point_number(item["points_used"]),
                }
                for item in sorted(
                    model_map.values(),
                    key=lambda value: (
                        point_amount(value["points_used"]),
                        int(value["tokens"]),
                        int(value["total"]),
                    ),
                    reverse=True,
                )
            ],
            "stage_quality": stage_quality,
            "daily": daily,
            "hourly_activity": hourly_activity,
        }
    )
