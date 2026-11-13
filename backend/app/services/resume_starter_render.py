"""把起手模板渲染成可编辑的简历数据，并负责创建简历。

一条简历的组装链路：取模板行 → 按层级选变体 → 组织模块顺序 → 生成工作/项目
条目 → 补自定义模块。纯拼装逻辑，落库只有最后一步 `create_resume_from_starter`。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.models.resume import Resume, ResumeStarter
from app.schemas.resume import ResumeCreate, default_template_config
from app.services.resume_service import create_resume

from app.services.resume_starter_content import _default_level_variant, _level, _modules_for_role, _starter_keywords
from app.services.resume_starter_data import CUSTOM_SECTION_LIBRARY, _starter_field_config
from app.services.resume_starter_service import _get_starter, industry_default_template_id


BUILT_IN_SECTION_KEYS = ["summary", "education", "skills", "work", "projects", "awards"]
BUILT_IN_SECTION_TITLES = {
    "basics": "基本信息",
    "summary": "个人简介",
    "education": "教育经历",
    "skills": "专业技能",
    "work": "工作经历",
    "projects": "项目经历",
    "awards": "荣誉奖项",
}


def _module_list(row: ResumeStarter, content: dict[str, Any]) -> list[dict[str, str]]:
    return _modules_for_role(row.industry_id, row.starter_id, content)


def _unique_section_order(modules: list[dict[str, str]]) -> list[str]:
    order = ["basics"]
    for item in modules:
        key = str(item.get("key") or "").strip()
        if key and key not in order:
            order.append(key)
    return order


def _with_ids(items: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        next_item = deepcopy(item)
        next_item["id"] = next_item.get("id") or f"{prefix}_{index}"
        result.append(next_item)
    return result


def _keyword_text(row: ResumeStarter, limit: int = 3) -> str:
    keywords = _starter_keywords(row)
    return "、".join(keywords[:limit]) if keywords else row.role_title


def _level_variant(content: dict[str, Any], row: ResumeStarter, level: dict[str, str]) -> dict[str, Any]:
    variants = content.get("level_variants") if isinstance(content.get("level_variants"), dict) else {}
    variant = variants.get(level["id"]) if isinstance(variants.get(level["id"]), dict) else {}
    return {**_default_level_variant(row, level), **variant}


def _level_summary(content: dict[str, Any], row: ResumeStarter, level: dict[str, str], variant: dict[str, Any]) -> str:
    base = str(content.get("summary") or "").strip()
    summary = str(variant.get("summary") or "").strip()
    if summary:
        return summary
    suffix = str(variant.get("summary_suffix") or "").strip()
    return f"{base} {suffix}".strip()


def _level_highlight_seeds(row: ResumeStarter, level: dict[str, str], section: str, variant: dict[str, Any]) -> list[str]:
    key = "project_highlights" if section == "project" else "work_highlights"
    values = variant.get(key)
    return [str(item).strip() for item in values if str(item).strip()] if isinstance(values, list) else []


def _ensure_highlights(items: list[str], row: ResumeStarter, level: dict[str, str], variant: dict[str, Any], section: str = "work") -> list[str]:
    highlights = _level_highlight_seeds(row, level, section, variant)
    highlights.extend(str(item) for item in items if item)
    fallbacks = [
        f"梳理{row.role_title}相关流程和高频问题，沉淀执行清单、话术模板或检查标准。",
        "与上下游团队保持清晰协作，推动需求确认、资源协调、异常反馈和结果闭环。",
        "通过数据记录、案例复盘或用户反馈定位改进点，持续提升效率、质量或转化表现。",
        "可将 XX 替换为真实数据，例如效率提升、成本下降、转化改善、满意度提升或错误率下降。",
    ]
    if level["id"] == "senior":
        fallbacks.append("搭建团队协作机制或新人带教方法，提升团队交付质量和问题响应速度。")
    for item in fallbacks:
        if len(highlights) >= 4:
            break
        if item not in highlights:
            highlights.append(item)
    result: list[str] = []
    for item in highlights:
        if item not in result:
            result.append(item)
        if len(result) >= 5:
            break
    return result


def _level_work_description(row: ResumeStarter, level: dict[str, str], variant: dict[str, Any]) -> str:
    return str(variant.get("work_description") or _default_level_variant(row, level).get("work_description") or "")


def _level_project_description(row: ResumeStarter, level: dict[str, str], variant: dict[str, Any]) -> str:
    return str(variant.get("project_description") or _default_level_variant(row, level).get("project_description") or "")


def _compose_level_description(raw: Any, level_description: str, level: dict[str, str]) -> str:
    text = str(raw or "").strip()
    if level["id"] in ("mid", "senior"):
        return f"{level_description} {text}".strip()
    return text or level_description


def _work_entries(content: dict[str, Any], row: ResumeStarter, level: dict[str, str], variant: dict[str, Any]) -> list[dict[str, Any]]:
    is_fresh = level["id"] == "fresh"
    if is_fresh:
        entries: list[dict[str, Any]] = [
            {
                "company": "请填写实习/校园实践组织",
                "position": f"{row.role_title}实习生",
                "description": _level_work_description(row, level, variant),
                "highlights": [],
            },
            {
                "company": "请填写校园项目/社团/实验室",
                "position": f"{row.role_title}相关实践",
                "description": f"结合课程、竞赛、社团或个人项目完成{row.role_title}相关实践，重点展示学习能力、执行意识和复盘能力。",
                "highlights": [
                    "拆解任务要求并按阶段推进，主动记录问题、反馈和改进动作。",
                    "整理项目资料、过程数据或展示文档，形成可用于面试沟通的作品或案例。",
                ],
            },
        ]
    else:
        samples = content.get("work_samples") if isinstance(content.get("work_samples"), list) else []
        entries = [item for item in samples if isinstance(item, dict)]
        work = content.get("work") if isinstance(content.get("work"), dict) else {}
        if work:
            entries.insert(0, work)
    if len(entries) < 2:
        entries.append({
            "company": "请填写公司名称",
            "position": row.role_title,
            "description": _level_work_description(row, level, variant),
            "highlights": [],
        })
    result: list[dict[str, Any]] = []
    for index, item in enumerate(entries[:2], start=1):
        result.append({
            "id": f"work_{index}",
            "company": item.get("company") or ("请填写实习/校园实践组织" if is_fresh else "请填写公司名称"),
            "position": f"{row.role_title}实习生" if is_fresh and index == 1 else (item.get("position") or row.role_title),
            "start_date": item.get("start_date") or "",
            "end_date": item.get("end_date") or "",
            "description": _compose_level_description(item.get("description"), _level_work_description(row, level, variant), level),
            "highlights": _ensure_highlights(list(item.get("highlights") or []), row, level, variant, "work"),
        })
    return result


def _project_entries(content: dict[str, Any], row: ResumeStarter, level: dict[str, str], variant: dict[str, Any]) -> list[dict[str, Any]]:
    is_fresh = level["id"] == "fresh"
    if is_fresh:
        entries: list[dict[str, Any]] = [
            {
                "name": f"{row.role_title}课程/实践项目",
                "role": "项目成员",
                "tech_stack": _keyword_text(row, 4),
                "description": _level_project_description(row, level, variant),
                "highlights": [],
            },
            {
                "name": f"{row.industry_name}场景分析与改进练习",
                "role": "项目成员",
                "tech_stack": "资料调研 / 流程梳理 / 复盘总结",
                "description": f"选择{row.industry_name}真实或模拟场景，完成问题分析、方案执行和成果总结。",
                "highlights": [
                    "通过资料调研、用户/业务观察或案例拆解理解场景问题，形成清晰的分析结论。",
                    "输出项目文档、展示材料或复盘记录，说明个人承担内容和改进思路。",
                ],
            },
        ]
    else:
        samples = content.get("project_samples") if isinstance(content.get("project_samples"), list) else []
        entries = [item for item in samples if isinstance(item, dict)]
        project = content.get("project") if isinstance(content.get("project"), dict) else {}
        if project:
            entries.insert(0, project)
    if len(entries) < 2:
        entries.append({
            "name": f"{row.role_title}能力提升项目",
            "role": "项目负责人" if level["id"] in ("mid", "senior") else "项目成员",
            "tech_stack": "流程梳理 / 数据复盘 / 协同推进",
            "description": _level_project_description(row, level, variant),
            "highlights": [],
        })
    result: list[dict[str, Any]] = []
    for index, item in enumerate(entries[:2], start=1):
        result.append({
            "id": f"project_{index}",
            "name": item.get("name") or "请填写项目名称",
            "role": "项目成员" if is_fresh else (item.get("role") or ("项目负责人" if level["id"] in ("mid", "senior") else "项目成员")),
            "start_date": item.get("start_date") or "",
            "end_date": item.get("end_date") or "",
            "tech_stack": item.get("tech_stack") or "",
            "description": _compose_level_description(item.get("description"), _level_project_description(row, level, variant), level),
            "highlights": _ensure_highlights(list(item.get("highlights") or []), row, level, variant, "project"),
        })
    return result


def _custom_sections(modules: list[dict[str, str]], content: dict[str, Any]) -> list[dict[str, Any]]:
    provided = content.get("custom_sections") if isinstance(content.get("custom_sections"), list) else []
    provided_by_id = {item.get("id"): item for item in provided if isinstance(item, dict) and item.get("id")}
    sections: list[dict[str, Any]] = []
    for module in modules:
        key = str(module.get("key") or "")
        if not key.startswith("custom_"):
            continue
        template = provided_by_id.get(key) or CUSTOM_SECTION_LIBRARY.get(key)
        if not template:
            continue
        section = deepcopy(template)
        section["id"] = key
        section["title"] = module.get("title") or section.get("title") or "自定义模块"
        section["items"] = _with_ids([item for item in section.get("items", []) if isinstance(item, dict)], f"{key}_item")
        sections.append(section)
    return sections


def build_resume_data_from_starter(row: ResumeStarter, level_id: str) -> dict[str, Any]:
    level = _level(level_id)
    content = deepcopy(row.content or {})
    modules = _module_list(row, content)
    section_order = _unique_section_order(modules)
    custom_sections = _custom_sections(modules, content)
    is_fresh = level["id"] == "fresh"
    variant = _level_variant(content, row, level)
    summary = _level_summary(content, row, level, variant)
    skills = content.get("skills") if isinstance(content.get("skills"), list) else []
    section_titles = {
        **BUILT_IN_SECTION_TITLES,
        "work": str(variant.get("work_title") or ("实习/实践经历" if is_fresh else "工作经历")),
        "projects": str(variant.get("projects_title") or ("校园/实践项目" if is_fresh else ("重点项目" if level["id"] == "senior" else "项目经历"))),
    }
    for module in modules:
        key = str(module.get("key") or "")
        if key:
            section_titles[key] = str(module.get("title") or section_titles.get(key) or key)
    if is_fresh:
        section_titles["work"] = str(variant.get("work_title") or "实习/实践经历")
        section_titles["projects"] = str(variant.get("projects_title") or "校园/实践项目")
    elif level["id"] == "senior":
        section_titles["work"] = str(variant.get("work_title") or "核心工作经历")
        section_titles["projects"] = str(variant.get("projects_title") or "重点项目")

    return {
        "basics": {
            "name": "",
            "title": row.role_title,
            "status": level["status"],
            "phone": "",
            "email": "",
            "location": "",
            "expected_salary": "面议",
            "highest_degree": "",
            "website": "",
            "github": "",
            "avatar": "",
            "custom_fields": [
                {"id": "field_industry", "label": "目标行业", "value": row.industry_name, "icon": "Briefcase", "row": 1, "order": 9}
            ],
            "field_config": _starter_field_config(),
        },
        "summary": {"content": summary},
        "education": [
            {
                "id": "edu_1",
                "school": "请填写学校名称",
                "major": "请填写专业",
                "degree": "请填写学历",
                "start_date": "",
                "end_date": "",
                "description": str(variant.get("education_description") or ("建议补充与目标岗位相关的主修课程、课程项目、竞赛经历、社团职责、实训作品或证书。" if is_fresh else "可补充与目标岗位相关的学历背景、培训经历、专业认证或在职学习成果。")),
            }
        ],
        "skills": [
            {
                "id": f"skill_{index + 1}",
                "name": item.get("name") or "专业技能",
                "keywords": item.get("keywords") or [],
                "description": item.get("description") or "",
            }
            for index, item in enumerate(skills)
            if isinstance(item, dict)
        ],
        "work": [
            *(_work_entries(content, row, level, variant) if "work" in section_order else [])
        ],
        "projects": [
            *(_project_entries(content, row, level, variant) if "projects" in section_order else [])
        ],
        "awards": [
            {
                "id": "award_1",
                "name": "请填写与目标岗位相关的奖项/荣誉",
                "date": "",
                "description": "可补充竞赛、评优、项目表彰、客户认可或团队荣誉。",
            }
        ] if "awards" in section_order else [],
        "custom_sections": custom_sections,
        "layout": {
            "section_order": section_order,
            "hidden_sections": [key for key in BUILT_IN_SECTION_KEYS if key not in section_order],
            "skills_options": {"show_keywords": True, "description_inline": False},
            "section_titles": section_titles,
        },
    }


def starter_resume_title(row: ResumeStarter, level_id: str) -> str:
    level = _level(level_id)
    return f"{row.role_title}简历 · {level['short_label']}"


def create_resume_from_starter(db: Session, user_id: int, starter_id: str, level_id: str, template_id: str) -> Resume:
    row = _get_starter(db, starter_id)
    starter_template = row.default_template_id or industry_default_template_id(db, row.industry_id)
    template = starter_template if template_id in ("", "__industry_default", "industry_default", None) else (template_id or starter_template or "tech")
    return create_resume(
        db,
        user_id,
        ResumeCreate(
            title=starter_resume_title(row, level_id),
            template_id=template,
            resume_data=build_resume_data_from_starter(row, level_id),
            template_config=default_template_config(template),
        ),
    )
