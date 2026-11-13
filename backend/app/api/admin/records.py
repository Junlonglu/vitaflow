from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_task import AiTask
from app.models.export_record import ExportRecord
from app.models.user import User
from app.api.admin.common import _ai_model_config_lookups, _ai_task_model_filter, page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/ai-tasks")
def ai_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = select(AiTask, User.username).join(User, User.id == AiTask.user_id)
    count_query = select(func.count()).select_from(AiTask)
    if status:
        query = query.where(AiTask.status == status)
        count_query = count_query.where(AiTask.status == status)
    if task_type:
        query = query.where(AiTask.task_type == task_type)
        count_query = count_query.where(AiTask.task_type == task_type)
    model_filter = _ai_task_model_filter(db, model)
    if model_filter is not None:
        query = query.where(model_filter)
        count_query = count_query.where(model_filter)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(AiTask.create_time.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    configs_by_id, configs_by_model = _ai_model_config_lookups(db)
    items = [
        {
            "id": task.id,
            "user_id": task.user_id,
            "username": username,
            "resume_id": task.resume_id,
            "task_type": task.task_type,
            "feature": task.feature,
            "status": task.status,
            **admin_task_model_info(task, configs_by_id, configs_by_model),
            "points_used": point_number(task.points_used),
            "tokens_used": task.tokens_used,
            "error_message": task.error_message,
            "input_data": {
                **(task.input_data or {}),
                "token_usage": normalize_token_usage(task.input_data or {}, task.tokens_used or 0),
            },
            "output_data": task.output_data or {},
            "create_time": task.create_time,
            "update_time": task.update_time,
        }
        for task, username in rows
    ]
    return success(page_data(items, total, page, page_size))


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
