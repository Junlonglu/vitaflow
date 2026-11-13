"""起手模板的管理端读写：行业模板配置、岗位增删改、内置数据同步。

对外接口（`app.api.resume_starters` / `app.api.admin.templates`）从这里取数据；
纯内容规则在 `resume_starter_content`，内置数据在 `resume_starter_data`。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.models.resume import ResumeStarter, ResumeStarterIndustryTemplate

from app.services.resume_starter_content import _ensure_content_defaults, _ensure_level_variants, _ensure_skill_depth, _modules_for_role
from app.services.resume_starter_data import BUILTIN_STARTERS, INDUSTRY_DEFAULT_TEMPLATES, STARTER_LEVELS


def builtin_industry_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for index, industry in enumerate(BUILTIN_STARTERS, start=1):
        options.append(
            {
                "industry_id": industry["industry_id"],
                "industry_name": industry["industry_name"],
                "industry_description": industry.get("industry_description") or "",
                "role_count": len(industry.get("roles") or []),
                "sort_order": index,
                "recommended_template_id": INDUSTRY_DEFAULT_TEMPLATES.get(industry["industry_id"], "tech"),
            }
        )
    return options


def _sync_builtin_industry_template_configs(db: Session) -> list[ResumeStarterIndustryTemplate]:
    existing = db.scalars(select(ResumeStarterIndustryTemplate)).all()
    by_industry = {row.industry_id: row for row in existing}
    changed = False
    for item in builtin_industry_options():
        row = by_industry.get(item["industry_id"])
        if row:
            updates = {
                "industry_name": item["industry_name"],
                "industry_description": item["industry_description"],
                "sort_order": item["sort_order"],
            }
            for key, value in updates.items():
                if getattr(row, key) != value:
                    setattr(row, key, value)
                    changed = True
            continue
        row = ResumeStarterIndustryTemplate(
            industry_id=item["industry_id"],
            industry_name=item["industry_name"],
            industry_description=item["industry_description"],
            default_template_id=item["recommended_template_id"],
            note="系统推荐默认模板",
            sort_order=item["sort_order"],
            is_active=1,
        )
        db.add(row)
        by_industry[item["industry_id"]] = row
        changed = True
    if changed:
        db.commit()
    return db.scalars(select(ResumeStarterIndustryTemplate).order_by(ResumeStarterIndustryTemplate.sort_order.asc(), ResumeStarterIndustryTemplate.id.asc())).all()


def get_industry_template_config(db: Session, industry_id: str) -> ResumeStarterIndustryTemplate | None:
    _sync_builtin_industry_template_configs(db)
    return db.scalar(
        select(ResumeStarterIndustryTemplate).where(
            ResumeStarterIndustryTemplate.industry_id == industry_id,
            ResumeStarterIndustryTemplate.is_active == 1,
        )
    )


def industry_default_template_id(db: Session, industry_id: str) -> str:
    row = get_industry_template_config(db, industry_id)
    return (row.default_template_id if row and row.default_template_id else "tech") or "tech"


def list_industry_template_configs(db: Session, templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _sync_builtin_industry_template_configs(db)
    by_industry = {row.industry_id: row for row in rows}
    templates_by_id = {item["template_id"]: item for item in templates}
    result: list[dict[str, Any]] = []
    for item in builtin_industry_options():
        row = by_industry.get(item["industry_id"])
        active = bool(row and row.is_active)
        template_id = row.default_template_id if active and row else ""
        template = templates_by_id.get(template_id)
        result.append(
            {
                "id": row.id if row else None,
                "industry_id": item["industry_id"],
                "industry_name": item["industry_name"],
                "industry_description": item["industry_description"],
                "role_count": item["role_count"],
                "default_template_id": template_id,
                "template_name": template["name"] if template else "",
                "template_category": template["category"] if template else "",
                "recommended_template_id": item["recommended_template_id"],
                "is_active": active,
                "note": row.note if row else "",
                "sort_order": item["sort_order"],
                "update_time": row.update_time if row else None,
            }
        )
    return result


def save_industry_template_config(db: Session, industry_id: str, template_id: str, note: str = "") -> dict[str, Any]:
    option = next((item for item in builtin_industry_options() if item["industry_id"] == industry_id), None)
    if not option:
        raise AppException("行业不存在", 404)
    row = db.scalar(select(ResumeStarterIndustryTemplate).where(ResumeStarterIndustryTemplate.industry_id == industry_id))
    if not row:
        row = ResumeStarterIndustryTemplate(
            industry_id=industry_id,
            industry_name=option["industry_name"],
            industry_description=option["industry_description"],
            sort_order=option["sort_order"],
        )
        db.add(row)
    row.industry_name = option["industry_name"]
    row.industry_description = option["industry_description"]
    row.default_template_id = template_id
    row.note = (note or "").strip()[:255]
    row.sort_order = option["sort_order"]
    row.is_active = 1
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "industry_id": row.industry_id,
        "industry_name": row.industry_name,
        "industry_description": row.industry_description,
        "role_count": option["role_count"],
        "default_template_id": row.default_template_id,
        "recommended_template_id": option["recommended_template_id"],
        "is_active": bool(row.is_active),
        "note": row.note,
        "sort_order": row.sort_order,
        "update_time": row.update_time,
    }


def delete_industry_template_config(db: Session, industry_id: str) -> None:
    row = db.scalar(select(ResumeStarterIndustryTemplate).where(ResumeStarterIndustryTemplate.industry_id == industry_id))
    if not row:
        return
    row.is_active = 0
    db.commit()


def _admin_starter_payload(row: ResumeStarter, templates_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    template_id = row.default_template_id or "tech"
    template = templates_by_id.get(template_id)
    return {
        "id": row.id,
        "starter_id": row.starter_id,
        "industry_id": row.industry_id,
        "industry_name": row.industry_name,
        "industry_description": row.industry_description or "",
        "role_title": row.role_title,
        "role_subtitle": row.role_subtitle or "",
        "default_template_id": template_id,
        "template_name": template["name"] if template else template_id,
        "template_category": template["category"] if template else "",
        "keywords": row.keywords or [],
        "focus": row.focus or [],
        "content": row.content or {},
        "sort_order": row.sort_order,
        "is_visible": bool(row.is_visible),
        "create_time": row.create_time,
        "update_time": row.update_time,
    }


def admin_resume_starter_industries(db: Session) -> list[dict[str, Any]]:
    _sync_builtin_resume_starters(db)
    rows = db.scalars(select(ResumeStarter).order_by(ResumeStarter.sort_order.asc(), ResumeStarter.id.asc())).all()
    by_id: dict[str, dict[str, Any]] = {}
    for item in builtin_industry_options():
        by_id[item["industry_id"]] = {
            "id": item["industry_id"],
            "name": item["industry_name"],
            "description": item["industry_description"],
        }
    for row in rows:
        if row.industry_id not in by_id:
            by_id[row.industry_id] = {
                "id": row.industry_id,
                "name": row.industry_name,
                "description": row.industry_description or "",
            }
    return list(by_id.values())


def list_admin_resume_starters(db: Session, templates: list[dict[str, Any]], keyword: str = "", industry_id: str = "") -> list[dict[str, Any]]:
    rows = [row for row in _sync_builtin_resume_starters(db) if row.is_visible]
    industry = (industry_id or "").strip()
    if industry:
        rows = [row for row in rows if row.industry_id == industry]
    text = (keyword or "").strip().lower()
    if text:
        rows = [
            row
            for row in rows
            if text in row.starter_id.lower()
            or text in row.industry_name.lower()
            or text in row.role_title.lower()
            or text in (row.role_subtitle or "").lower()
        ]
    templates_by_id = {item["template_id"]: item for item in templates}
    return [_admin_starter_payload(row, templates_by_id) for row in rows]


def _new_starter_id(db: Session) -> str:
    while True:
        starter_id = f"custom-{uuid4().hex[:12]}"
        if not db.scalar(select(ResumeStarter.id).where(ResumeStarter.starter_id == starter_id)):
            return starter_id


def _normalize_admin_starter_content(
    starter_id: str,
    industry_id: str,
    industry_name: str,
    role_title: str,
    keywords: list[str],
    content: Any,
) -> dict[str, Any]:
    value = deepcopy(content) if isinstance(content, dict) else {}
    if not isinstance(value.get("modules"), list) or not value["modules"]:
        value["modules"] = _modules_for_role(industry_id, starter_id, value)
    _ensure_skill_depth({"keywords": keywords}, value)
    _ensure_level_variants(
        {"role_title": role_title, "industry_name": industry_name, "keywords": keywords},
        value,
    )
    return value


def create_admin_resume_starter(db: Session, data: dict[str, Any], templates: list[dict[str, Any]]) -> dict[str, Any]:
    template_ids = {item["template_id"] for item in templates}
    template_id = data.get("default_template_id") or "tech"
    if template_id not in template_ids:
        raise AppException("模板不存在", 404)
    starter_id = (data.get("starter_id") or "").strip() or _new_starter_id(db)
    if db.scalar(select(ResumeStarter.id).where(ResumeStarter.starter_id == starter_id)):
        raise AppException("岗位预设 ID 已存在", 409)
    keywords = [str(item).strip() for item in (data.get("keywords") or []) if str(item).strip()]
    row = ResumeStarter(
        starter_id=starter_id,
        industry_id=(data.get("industry_id") or "custom").strip(),
        industry_name=(data.get("industry_name") or "自定义行业").strip(),
        industry_description=(data.get("industry_description") or "").strip(),
        role_title=(data.get("role_title") or "").strip(),
        role_subtitle=(data.get("role_subtitle") or "").strip(),
        default_template_id=template_id,
        keywords=keywords,
        focus=[str(item).strip() for item in (data.get("focus") or []) if str(item).strip()],
        content=_normalize_admin_starter_content(
            starter_id,
            (data.get("industry_id") or "custom").strip(),
            (data.get("industry_name") or "自定义行业").strip(),
            (data.get("role_title") or "").strip(),
            keywords,
            data.get("content") or {},
        ),
        sort_order=int(data.get("sort_order") or 1000),
        is_visible=1 if data.get("is_visible", True) else 0,
    )
    if not row.role_title:
        raise AppException("岗位名称不能为空", 400)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _admin_starter_payload(row, {item["template_id"]: item for item in templates})


def update_admin_resume_starter(db: Session, starter_id: str, data: dict[str, Any], templates: list[dict[str, Any]]) -> dict[str, Any]:
    template_ids = {item["template_id"] for item in templates}
    template_id = data.get("default_template_id") or "tech"
    if template_id not in template_ids:
        raise AppException("模板不存在", 404)
    row = db.scalar(select(ResumeStarter).where(ResumeStarter.starter_id == starter_id))
    if not row:
        raise AppException("岗位预设不存在", 404)
    keywords = [str(item).strip() for item in (data.get("keywords") or []) if str(item).strip()]
    row.industry_id = (data.get("industry_id") or "custom").strip()
    row.industry_name = (data.get("industry_name") or "自定义行业").strip()
    row.industry_description = (data.get("industry_description") or "").strip()
    row.role_title = (data.get("role_title") or "").strip()
    row.role_subtitle = (data.get("role_subtitle") or "").strip()
    row.default_template_id = template_id
    row.keywords = keywords
    row.focus = [str(item).strip() for item in (data.get("focus") or []) if str(item).strip()]
    row.content = _normalize_admin_starter_content(
        row.starter_id,
        row.industry_id,
        row.industry_name,
        row.role_title,
        row.keywords,
        data.get("content") or {},
    )
    row.sort_order = int(data.get("sort_order") or row.sort_order or 1000)
    row.is_visible = 1 if data.get("is_visible", True) else 0
    if not row.role_title:
        raise AppException("岗位名称不能为空", 400)
    db.commit()
    db.refresh(row)
    return _admin_starter_payload(row, {item["template_id"]: item for item in templates})


def delete_admin_resume_starter(db: Session, starter_id: str) -> None:
    row = db.scalar(select(ResumeStarter).where(ResumeStarter.starter_id == starter_id))
    if not row:
        return
    row.is_visible = 0
    db.commit()


def _sync_builtin_resume_starters(db: Session) -> list[ResumeStarter]:
    existing = db.scalars(select(ResumeStarter)).all()
    by_starter_id = {row.starter_id: row for row in existing}
    changed = False
    sort_order = 1
    for industry in BUILTIN_STARTERS:
        for role in industry["roles"]:
            role = _ensure_content_defaults(industry, role)
            row = by_starter_id.get(role["starter_id"])
            if row:
                if not getattr(row, "default_template_id", ""):
                    row.default_template_id = role.get("default_template_id") or INDUSTRY_DEFAULT_TEMPLATES.get(industry["industry_id"], "tech")
                    changed = True
                content = deepcopy(row.content or {})
                if _ensure_level_variants(row, content):
                    row.content = content
                    changed = True
                sort_order += 1
                continue
            row = ResumeStarter(
                starter_id=role["starter_id"],
                industry_id=industry["industry_id"],
                industry_name=industry["industry_name"],
                industry_description=industry["industry_description"],
                role_title=role["role_title"],
                role_subtitle=role.get("role_subtitle") or "",
                default_template_id=role.get("default_template_id") or INDUSTRY_DEFAULT_TEMPLATES.get(industry["industry_id"], "tech"),
                keywords=role.get("keywords") or [],
                focus=role.get("focus") or [],
                content=role.get("content") or {},
                sort_order=sort_order,
                is_visible=1,
            )
            db.add(row)
            changed = True
            sort_order += 1
    if changed:
        db.commit()
    return db.scalars(select(ResumeStarter).order_by(ResumeStarter.sort_order.asc(), ResumeStarter.id.asc())).all()


def _starter_to_role(row: ResumeStarter) -> dict[str, Any]:
    return {
        "starter_id": row.starter_id,
        "title": row.role_title,
        "subtitle": row.role_subtitle or "",
        "default_template_id": row.default_template_id or "tech",
        "keywords": row.keywords or [],
        "focus": row.focus or [],
        "content": row.content or {},
    }


def list_resume_starters(db: Session, industry_id: str = "") -> dict[str, Any]:
    rows = [row for row in _sync_builtin_resume_starters(db) if row.is_visible]
    industries: list[dict[str, Any]] = []
    by_industry: dict[str, dict[str, Any]] = {}
    for row in rows:
        industry = by_industry.get(row.industry_id)
        if not industry:
            industry = {
                "id": row.industry_id,
                "name": row.industry_name,
                "description": row.industry_description or "",
                "roles": [],
            }
            by_industry[row.industry_id] = industry
            industries.append(industry)
        if industry_id and row.industry_id == industry_id:
            industry["roles"].append(_starter_to_role(row))
    return {"levels": STARTER_LEVELS, "industries": industries}


def _get_starter(db: Session, starter_id: str) -> ResumeStarter:
    _sync_builtin_resume_starters(db)
    row = db.scalar(select(ResumeStarter).where(ResumeStarter.starter_id == starter_id, ResumeStarter.is_visible == 1))
    if not row:
        raise AppException("岗位起稿内容不存在", 404)
    return row
