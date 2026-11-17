from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_task import AiTask
from app.models.export_record import ExportRecord
from app.models.resume import Resume
from app.models.user import User
from app.services.ai.token_usage import normalize_token_usage
from app.services.flow_points_service import point_number
from app.api.admin.common import (
    _ai_model_config_lookups,
    _ai_task_model_filter,
    admin_task_model_info,
    page_data,
    window_start,
)


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])

# 单次导出上限：排障只会看最近的几百条，全量导出既慢又没人看得完。
_AI_TASK_EXPORT_LIMIT = 5000


def _task_duration_ms(task: AiTask) -> int | None:
    """任务耗时：对话回合取 ai-service 上报的 turn_timing.total_ms，

    其余任务退回 create→update 的时间差（update_time 在收尾时写入）。
    """
    timing = (task.input_data or {}).get("turn_timing")
    if isinstance(timing, dict):
        total = timing.get("total_ms")
        if isinstance(total, (int, float)) and total >= 0:
            return int(total)
    if task.create_time and task.update_time:
        delta = (task.update_time - task.create_time).total_seconds() * 1000
        if delta >= 0:
            return int(delta)
    return None


def _ai_task_conditions(db: Session, status: str | None, task_type: str | None, model: str | None, window: str | None) -> list[Any]:
    conditions: list[Any] = []
    if status:
        conditions.append(AiTask.status == status)
    if task_type:
        conditions.append(AiTask.task_type == task_type)
    model_filter = _ai_task_model_filter(db, model)
    if model_filter is not None:
        conditions.append(model_filter)
    start = window_start(window)
    if start is not None:
        conditions.append(AiTask.create_time >= start)
    return conditions


def _ai_task_select():
    return (
        select(AiTask, User.username, Resume.title)
        .join(User, User.id == AiTask.user_id)
        .outerjoin(Resume, Resume.id == AiTask.resume_id)
    )


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if any(char in text for char in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


@router.get("/ai-tasks")
def ai_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
    window: Optional[str] = Query(None, alias="range"),
    db: Session = Depends(get_db),
):
    conditions = _ai_task_conditions(db, status, task_type, model, window)
    query = _ai_task_select()
    count_query = select(func.count()).select_from(AiTask)
    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(AiTask.create_time.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    configs_by_id, configs_by_model = _ai_model_config_lookups(db)
    items = [
        {
            "id": task.id,
            "user_id": task.user_id,
            "username": username,
            "resume_id": task.resume_id,
            "resume_title": resume_title,
            "task_type": task.task_type,
            "feature": task.feature,
            "status": task.status,
            **admin_task_model_info(task, configs_by_id, configs_by_model),
            "points_used": point_number(task.points_used),
            "tokens_used": task.tokens_used,
            "duration_ms": _task_duration_ms(task),
            "error_message": task.error_message,
            "input_data": {
                **(task.input_data or {}),
                "token_usage": normalize_token_usage(task.input_data or {}, task.tokens_used or 0),
            },
            "output_data": task.output_data or {},
            "create_time": task.create_time,
            "update_time": task.update_time,
        }
        for task, username, resume_title in rows
    ]
    return success(page_data(items, total, page, page_size))


@router.get("/ai-tasks/export")
def export_ai_tasks(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
    window: Optional[str] = Query(None, alias="range"),
    db: Session = Depends(get_db),
):
    """导出当前筛选条件下的 AI 任务明细（最多 5000 条）。

    供客诉对账与计费复核使用；列与列表保持一致，不含 output_data。
    """
    conditions = _ai_task_conditions(db, status, task_type, model, window)
    query = _ai_task_select()
    if conditions:
        query = query.where(*conditions)
    rows = db.execute(query.order_by(AiTask.create_time.desc()).limit(_AI_TASK_EXPORT_LIMIT)).all()
    configs_by_id, configs_by_model = _ai_model_config_lookups(db)

    lines = ["时间,用户,任务类型,指令,模型,状态,扣点,输入 Token,输出 Token,总 Token,耗时(ms),简历 ID,简历标题,错误"]
    for task, username, resume_title in rows:
        usage = normalize_token_usage(task.input_data or {}, task.tokens_used or 0)
        model_info = admin_task_model_info(task, configs_by_id, configs_by_model)
        lines.append(",".join(_csv_cell(value) for value in (
            task.create_time.strftime("%Y-%m-%d %H:%M:%S") if task.create_time else "",
            username,
            task.task_type,
            task.feature or "",
            model_info.get("model_name") or "",
            task.status,
            point_number(task.points_used),
            usage["input_tokens"],
            usage["output_tokens"],
            usage["total_tokens"],
            _task_duration_ms(task) or "",
            task.resume_id or "",
            resume_title or "",
            task.error_message or "",
        )))
    # BOM 让 Excel 直接用 UTF-8 打开中文列名
    text = "\ufeff" + "\n".join(lines)
    headers = {"Content-Disposition": 'attachment; filename="ai-tasks.csv"'}
    return PlainTextResponse(text, media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/exports")
def exports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = select(ExportRecord, User.username).join(User, User.id == ExportRecord.user_id)
    count_query = select(func.count()).select_from(ExportRecord)
    if status:
        query = query.where(ExportRecord.status == status)
        count_query = count_query.where(ExportRecord.status == status)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(ExportRecord.create_time.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        {
            "id": record.id,
            "user_id": record.user_id,
            "username": username,
            "resume_id": record.resume_id,
            "file_type": record.file_type,
            "file_name": record.file_name,
            "status": record.status,
            "error_message": record.error_message,
            "create_time": record.create_time,
        }
        for record, username in rows
    ]
    return success(page_data(items, total, page, page_size))
