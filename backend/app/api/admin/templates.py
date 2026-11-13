from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.resume import Resume
from app.services.resume_starter_service import admin_resume_starter_industries, create_admin_resume_starter, delete_admin_resume_starter, delete_industry_template_config, list_admin_resume_starters, list_industry_template_configs, save_industry_template_config, update_admin_resume_starter
from app.services.template_service import ordered_template_records, update_template_display
from app.api.admin.common import page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


class TemplateDisplayUpdate(BaseModel):
    sort_order: Optional[int] = Field(default=None, ge=1)
    is_visible: Optional[bool] = None


class IndustryTemplateWrite(BaseModel):
    default_template_id: str = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=255)


class ResumeStarterWrite(BaseModel):
    starter_id: Optional[str] = Field(default=None, max_length=80)
    industry_id: str = Field(min_length=1, max_length=50)
    industry_name: str = Field(min_length=1, max_length=100)
    industry_description: str = Field(default="", max_length=255)
    role_title: str = Field(min_length=1, max_length=100)
    role_subtitle: str = Field(default="", max_length=120)
    default_template_id: str = Field(default="tech", min_length=1, max_length=50)
    keywords: list[str] = Field(default_factory=list)
    focus: list[str] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = Field(default=1000, ge=0)
    is_visible: bool = True


@router.get("/templates")
def templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    usage_rows = db.execute(select(Resume.template_id, func.count(Resume.id)).group_by(Resume.template_id)).all()
    usage = {template_id: int(count) for template_id, count in usage_rows}
    all_items = [
        {
            "template_id": item["template_id"],
            "name": item["name"],
            "category": item["category"],
            "is_pro": bool(item["is_pro"]),
            "is_visible": bool(item.get("is_visible", True)),
            "sort_order": int(item.get("sort_order") or 0),
            "usage_count": usage.get(item["template_id"], 0),
            "preview_html": _with_preview(item)["preview_html"],
        }
        for item in ordered_template_records(db, include_hidden=True)
    ]
    start = (page - 1) * page_size
    return success(page_data(all_items[start : start + page_size], len(all_items), page, page_size))


@router.patch("/templates/{template_id}")
def update_template(template_id: str, payload: TemplateDisplayUpdate, db: Session = Depends(get_db)):
    try:
        item = update_template_display(
            db,
            template_id,
            sort_order=payload.sort_order,
            is_visible=payload.is_visible,
        )
    except ValueError:
        raise AppException("模板不存在", 404)
    return success(item)


@router.get("/resume-starter-industries")
def resume_starter_industries(db: Session = Depends(get_db)):
    return success(admin_resume_starter_industries(db))


@router.get("/resume-starters")
def admin_resume_starters(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    industry_id: str = "",
    db: Session = Depends(get_db),
):
    template_records = ordered_template_records(db, include_hidden=True)
    all_items = list_admin_resume_starters(db, template_records, keyword, industry_id)
    start = (page - 1) * page_size
    return success(page_data(all_items[start : start + page_size], len(all_items), page, page_size))


@router.post("/resume-starters")
def create_resume_starter(payload: ResumeStarterWrite, db: Session = Depends(get_db)):
    template_records = ordered_template_records(db, include_hidden=True)
    item = create_admin_resume_starter(db, payload.model_dump(), template_records)
    return success(item, "岗位预设已创建")


@router.put("/resume-starters/{starter_id}")
def update_resume_starter(starter_id: str, payload: ResumeStarterWrite, db: Session = Depends(get_db)):
    template_records = ordered_template_records(db, include_hidden=True)
    item = update_admin_resume_starter(db, starter_id, payload.model_dump(), template_records)
    return success(item, "岗位预设已保存")


@router.delete("/resume-starters/{starter_id}")
def delete_resume_starter(starter_id: str, db: Session = Depends(get_db)):
    delete_admin_resume_starter(db, starter_id)
    return success(True, "岗位预设已删除")


@router.get("/resume-starter-industry-templates")
def resume_starter_industry_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    template_records = ordered_template_records(db, include_hidden=True)
    all_items = list_industry_template_configs(db, template_records)
    start = (page - 1) * page_size
    return success(page_data(all_items[start : start + page_size], len(all_items), page, page_size))


@router.put("/resume-starter-industry-templates/{industry_id}")
def update_resume_starter_industry_template(industry_id: str, payload: IndustryTemplateWrite, db: Session = Depends(get_db)):
    template_ids = {item["template_id"] for item in ordered_template_records(db, include_hidden=True)}
    if payload.default_template_id not in template_ids:
        raise AppException("模板不存在", 404)
    item = save_industry_template_config(db, industry_id, payload.default_template_id, payload.note)
    template = next((row for row in ordered_template_records(db, include_hidden=True) if row["template_id"] == payload.default_template_id), None)
    if template:
        item["template_name"] = template["name"]
        item["template_category"] = template["category"]
    return success(item, "行业默认模板已保存")


@router.delete("/resume-starter-industry-templates/{industry_id}")
def delete_resume_starter_industry_template(industry_id: str, db: Session = Depends(get_db)):
    delete_industry_template_config(db, industry_id)
    return success(True, "行业默认模板配置已删除")
