from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_task import AiTask
from app.models.ai_config import AiModelConfig, FlowPointRedeemCode, FlowPointRedeemRecord, FlowPointTransaction
from app.models.export_record import ExportRecord
from app.models.resume import Resume, UploadedFile
from app.models.user import User
from app.services.ai.token_usage import normalize_token_usage
from app.services.flow_points_service import point_amount, point_number


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


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


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    total_resumes = db.scalar(select(func.count()).select_from(Resume)) or 0
    total_ai = db.scalar(select(func.count()).select_from(AiTask)) or 0
    total_exports = db.scalar(select(func.count()).select_from(ExportRecord)) or 0
    first_user_time = db.scalar(select(func.min(User.create_time)))
    operating_days = (today.date() - first_user_time.date()).days + 1 if first_user_time else 0
    ai_success = db.scalar(select(func.count()).select_from(AiTask).where(AiTask.status == "success")) or 0
    export_success = db.scalar(select(func.count()).select_from(ExportRecord).where(ExportRecord.status == "success")) or 0
    storage_bytes = db.scalar(select(func.coalesce(func.sum(UploadedFile.file_size), 0))) or 0
    redeem_revenue_expr = case(
        (FlowPointRedeemRecord.price > 0, FlowPointRedeemRecord.price),
        else_=func.coalesce(FlowPointRedeemCode.price, 0),
    )
    redeem_revenue_query = (
        select(func.coalesce(func.sum(redeem_revenue_expr), 0))
        .select_from(FlowPointRedeemRecord)
        .outerjoin(FlowPointRedeemCode, FlowPointRedeemCode.id == FlowPointRedeemRecord.code_id)
    )
    total_redeem_revenue = point_amount(db.scalar(redeem_revenue_query) or 0)
    today_redeem_revenue = point_amount(
        db.scalar(redeem_revenue_query.where(FlowPointRedeemRecord.create_time >= today)) or 0
    )
    total_points_consumed = point_amount(abs(
        db.scalar(
            select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0)).where(
                FlowPointTransaction.points_delta < 0
            )
        )
        or 0
    ))
    today_points_consumed = point_amount(abs(
        db.scalar(
            select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0)).where(
                FlowPointTransaction.points_delta < 0,
                FlowPointTransaction.create_time >= today,
            )
        )
        or 0
    ))
    total_points_recharged = point_amount(db.scalar(
        select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0)).where(
            FlowPointTransaction.points_delta > 0
        )
    ) or 0)
    active_user_ids: set[int] = set()
    active_user_ids.update(
        uid
        for uid in db.scalars(
            select(Resume.user_id).where(or_(Resume.create_time >= today, Resume.update_time >= today))
        ).all()
        if uid
    )
    active_user_ids.update(uid for uid in db.scalars(select(AiTask.user_id).where(AiTask.create_time >= today)).all() if uid)
    active_user_ids.update(uid for uid in db.scalars(select(ExportRecord.user_id).where(ExportRecord.create_time >= today)).all() if uid)
    breakdown_rows = db.execute(
        select(
            AiTask.task_type,
            AiTask.feature,
            func.count(AiTask.id),
            func.sum(case((AiTask.status == "success", 1), else_=0)),
            func.sum(case((AiTask.status == "failed", 1), else_=0)),
        ).group_by(AiTask.task_type, AiTask.feature)
    ).all()
    token_rows = db.execute(
        select(
            AiTask.task_type,
            AiTask.feature,
            AiTask.create_time,
            AiTask.tokens_used,
            AiTask.input_data,
            AiTask.model_name,
            AiTask.points_used,
        )
    ).all()
    token_summary = {"total": 0, "today": 0, "input": 0, "output": 0}
    token_by_type: dict[str, dict[str, int]] = {}
    model_map: dict[str, dict[str, object]] = {}
    configured_models = list(
        db.scalars(
            select(AiModelConfig).order_by(
                AiModelConfig.sort_order.asc(),
                AiModelConfig.id.asc(),
            )
        )
    )
    for config in configured_models:
        model_name = str(getattr(config, "model_ref", None) or "").strip()
        if not model_name:
            continue
        model_item = model_map.setdefault(
            model_name,
            {
                "model_name": model_name,
                "model_raw_name": model_name,
                "config_names": set(),
                "total": 0,
                "tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "points_used": Decimal("0.00"),
            },
        )
        if config.name and str(config.name) != model_name:
            model_item["config_names"].add(str(config.name))
    # 历史任务的 model_name 口径不一（裸 id / 策略名 / ref）；裸 id 若能唯一映射到已配置 ref 则归并
    refs_by_model_id: dict[str, list[str]] = {}
    for config in configured_models:
        ref = str(getattr(config, "model_ref", None) or "").strip()
        if ref and "/" in ref:
            refs_by_model_id.setdefault(ref.split("/", 1)[1], []).append(ref)

    for task_type, task_feature, create_time, fallback_tokens, input_data, raw_model_name, points_used in token_rows:
        usage = normalize_token_usage(input_data, fallback_tokens or 0)
        item = token_by_type.setdefault(_breakdown_key(task_type, task_feature), {"total": 0, "input": 0, "output": 0})
        item["total"] += int(usage["total_tokens"])
        item["input"] += int(usage["input_tokens"])
        item["output"] += int(usage["output_tokens"])
        token_summary["total"] += int(usage["total_tokens"])
        token_summary["input"] += int(usage["input_tokens"])
        token_summary["output"] += int(usage["output_tokens"])
        if create_time and create_time >= today:
            token_summary["today"] += int(usage["total_tokens"])
        input_data = input_data or {}
        config_name = input_data.get("model_name")
        # 聚合键以目录模型 ref（input_data.model，各链路统一写入）为准，与按模型计费同口径
        model_name = str(input_data.get("model") or "").strip()
        if model_name and "/" not in model_name:
            refs = refs_by_model_id.get(model_name, [])
            if len(refs) == 1:
                model_name = refs[0]
        if not model_name:
            model_name = str(raw_model_name or config_name or "")
        if not model_name:
            continue
        model_key = model_name
        model_item = model_map.setdefault(
            model_key,
            {
                "model_name": model_name,
                "model_raw_name": str(raw_model_name) if raw_model_name else "",
                "config_names": set(),
                "total": 0,
                "tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "points_used": Decimal("0.00"),
            },
        )
        if config_name and str(config_name) != model_name:
            model_item["config_names"].add(str(config_name))
        model_item["total"] = int(model_item["total"]) + 1
        model_item["tokens"] = int(model_item["tokens"]) + int(usage["total_tokens"])
        model_item["input_tokens"] = int(model_item["input_tokens"]) + int(usage["input_tokens"])
        model_item["output_tokens"] = int(model_item["output_tokens"]) + int(usage["output_tokens"])
        model_item["points_used"] = point_amount(model_item["points_used"]) + point_amount(points_used or 0)
    # 功能分布：对话回合（ai_chat）按 feature 拆分（润色/翻译/JD优化/自由对话…），
    # 其余任务类型沿用 task_type；两个 group-by 在此按同一 key 合并。
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
            "tokens": token_by_type.get(key, {}).get("total", 0),
            "input_tokens": token_by_type.get(key, {}).get("input", 0),
            "output_tokens": token_by_type.get(key, {}).get("output", 0),
        }
        for key, counts in sorted(breakdown_map.items(), key=lambda value: value[1]["total"], reverse=True)
    ]
    section_rows = db.execute(
        select(AiTask.input_data, AiTask.status).where(AiTask.task_type == "section_optimize")
    ).all()
    built_in_sections = {"basics", "summary", "education", "skills", "work", "projects", "awards"}
    section_map: dict[str, dict[str, int]] = {}
    for input_data, task_status in section_rows:
        section_type = str((input_data or {}).get("section_type") or "unknown")
        if section_type not in built_in_sections:
            section_type = "custom"
        item = section_map.setdefault(section_type, {"total": 0, "success": 0, "failed": 0})
        item["total"] += 1
        if task_status in {"success", "failed"}:
            item[task_status] += 1

    # 对话环节质量：回合任务行上的 turn_timing（回合耗时）+ tool_quality（工具
    # 步骤）统一聚合，全部来自 ai-service 计费上报的事实行，与消息表解耦——
    # 删简历后口径仍与功能分布一致。按 id 降序取近 500 行，行序即新→旧，
    # 供「近10次」趋势样本使用。
    task_rows = db.execute(
        select(AiTask.input_data, AiTask.status)
        .where(AiTask.task_type == "ai_chat", AiTask.input_data.isnot(None))
        .order_by(AiTask.id.desc())
        .limit(500)
    ).all()
    stage_quality = _stage_quality_breakdown([(row.input_data, row.status) for row in task_rows])

    daily = []
    start_day = today - timedelta(days=6)
    end_day = today + timedelta(days=1)
    hourly_user_ids: list[set[int]] = [set() for _ in range(24)]
    hourly_actions = [0 for _ in range(24)]

    def add_hourly_activity(user_id: int | None, create_time: datetime | None) -> None:
        if not user_id or not create_time or create_time < start_day or create_time >= end_day:
            return
        hour = create_time.hour
        hourly_user_ids[hour].add(int(user_id))
        hourly_actions[hour] += 1

    resume_activity_rows = db.execute(
        select(Resume.user_id, Resume.create_time, Resume.update_time).where(
            or_(
                Resume.create_time >= start_day,
                Resume.update_time >= start_day,
            )
        )
    ).all()
    for user_id, create_time, update_time in resume_activity_rows:
        add_hourly_activity(user_id, create_time)
        if update_time != create_time:
            add_hourly_activity(user_id, update_time)

    for user_id, create_time in db.execute(
        select(AiTask.user_id, AiTask.create_time).where(AiTask.create_time >= start_day)
    ).all():
        add_hourly_activity(user_id, create_time)

    for user_id, create_time in db.execute(
        select(ExportRecord.user_id, ExportRecord.create_time).where(ExportRecord.create_time >= start_day)
    ).all():
        add_hourly_activity(user_id, create_time)

    for user_id, share_created_time in db.execute(
        select(Resume.user_id, Resume.share_created_time).where(Resume.share_created_time >= start_day)
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

    for offset in range(7):
        day = start_day + timedelta(days=offset)
        next_day = day + timedelta(days=1)
        daily.append(
            {
                "date": day.strftime("%m-%d"),
                "users": db.scalar(select(func.count()).select_from(User).where(User.create_time >= day, User.create_time < next_day)) or 0,
                "resumes": db.scalar(select(func.count()).select_from(Resume).where(Resume.create_time >= day, Resume.create_time < next_day)) or 0,
                "ai_tasks": db.scalar(select(func.count()).select_from(AiTask).where(AiTask.create_time >= day, AiTask.create_time < next_day)) or 0,
                "exports": db.scalar(select(func.count()).select_from(ExportRecord).where(ExportRecord.create_time >= day, ExportRecord.create_time < next_day)) or 0,
                "shares": db.scalar(select(func.count()).select_from(Resume).where(Resume.share_created_time >= day, Resume.share_created_time < next_day)) or 0,
                "tokens": sum(
                    int(normalize_token_usage(input_data, fallback_tokens or 0)["total_tokens"])
                    for _, _, create_time, fallback_tokens, input_data, _, _ in token_rows
                    if create_time and day <= create_time < next_day
                ),
                "points_consumed": point_number(abs(
                    db.scalar(
                        select(func.coalesce(func.sum(FlowPointTransaction.points_delta), 0)).where(
                            FlowPointTransaction.points_delta < 0,
                            FlowPointTransaction.create_time >= day,
                            FlowPointTransaction.create_time < next_day,
                        )
                    )
                    or 0
                )),
            }
        )

    return success(
        {
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
                "users": db.scalar(select(func.count()).select_from(User).where(User.create_time >= today)) or 0,
                "active_users": len(active_user_ids),
                "resumes": db.scalar(select(func.count()).select_from(Resume).where(Resume.create_time >= today)) or 0,
                "ai_tasks": db.scalar(select(func.count()).select_from(AiTask).where(AiTask.create_time >= today)) or 0,
                "exports": db.scalar(select(func.count()).select_from(ExportRecord).where(ExportRecord.create_time >= today)) or 0,
            },
            "rates": {
                "ai_success": round(ai_success * 100 / total_ai, 1) if total_ai else 0,
                "export_success": round(export_success * 100 / total_exports, 1) if total_exports else 0,
            },
            "tokens": {
                **token_summary,
                "avg_per_task": round(token_summary["total"] / total_ai, 1) if total_ai else 0,
            },
            "points": {
                "consumed": point_number(total_points_consumed),
                "today_consumed": point_number(today_points_consumed),
                "recharged": point_number(total_points_recharged),
            },
            "revenue": {
                "redeemed": point_number(total_redeem_revenue),
                "today_redeemed": point_number(today_redeem_revenue),
            },
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
            "section_breakdown": [
                {"section_type": section_type, **counts} for section_type, counts in section_map.items()
            ],
            "stage_quality": stage_quality,
            "daily": daily,
            "hourly_activity": hourly_activity,
        }
    )
