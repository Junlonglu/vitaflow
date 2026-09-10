"""Pure text / resume-structure normalization helpers.

Rescued from the retired LangChain ``chains.py`` (ticket 09): these are plain
data-shaping utilities with no LLM dependency, still used by the pi-agent chat
path, the AI-service generate/import proxy and the file-import endpoint.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.services.resume_locale import (
    detect_resume_language,
    localized_section_title,
    normalize_resume_language,
    resume_locale,
)

BUILT_IN_SECTIONS = ["basics", "summary", "education", "skills", "work", "projects", "awards"]
BUILT_IN_TITLES = {
    "basics": "基本信息",
    "summary": "个人简介",
    "education": "教育经历",
    "skills": "专业技能",
    "work": "工作经历",
    "projects": "项目经历",
    "awards": "荣誉奖项",
}
DEFAULT_FIELD_CONFIG = {
    "phone": {"label": "电话", "icon": "Phone", "row": 1, "order": 1},
    "email": {"label": "邮箱", "icon": "Mail", "row": 1, "order": 2},
    "status": {"label": "当前状态", "icon": "Info", "row": 1, "order": 3},
    "location": {"label": "地点", "icon": "MapPin", "row": 1, "order": 4},
    "highest_degree": {"label": "最高学历", "icon": "GraduationCap", "row": 2, "order": 1},
    "website": {"label": "个人网站", "icon": "Globe", "row": 2, "order": 2},
    "github": {"label": "代码仓库", "icon": "Github", "row": 2, "order": 3},
    "expected_salary": {"label": "期望薪资", "icon": "Briefcase", "row": 2, "order": 4},
}


def strip_thinking_text(text: str) -> str:
    result = re.sub(r"<think\b[^>]*>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL)
    if "</think>" in result.lower():
        result = re.split(r"</think>", result, maxsplit=1, flags=re.IGNORECASE)[-1]
    result = re.sub(r"<think\b[^>]*>.*$", "", result, flags=re.IGNORECASE | re.DOTALL)
    return result.lstrip()


def localize_ai_text(value: str) -> str:
    result = value
    user_hidden_fields = {
        "optimized_resume_data": "优化后的简历内容",
        "resume_data": "简历内容",
        "field_config": "基本信息展示配置",
        "highest_degree": "最高学历",
        "start_date": "开始时间",
        "end_date": "结束时间",
        "tech_stack": "技术栈",
        "description": "描述",
        "highlights": "亮点",
        "highlight": "亮点",
        "keywords": "关键词",
        "basics": "基本信息",
        "summary": "个人简介",
        "skills": "专业技能",
        "projects": "项目经历",
    }
    for key, label in sorted(user_hidden_fields.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, result, flags=re.IGNORECASE)
    return result


def normalize_resume_data(data: dict[str, Any], payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    requested_language = (payload or {}).get("language")
    language = normalize_resume_language(
        requested_language or detect_resume_language(json.dumps(data, ensure_ascii=False))
    )
    locale = resume_locale(language)
    basics = data.setdefault("basics", {})
    if not isinstance(basics, dict):
        basics = {}
        data["basics"] = basics
    basics.setdefault("avatar", "")
    if not isinstance(basics.get("custom_fields"), list):
        basics["custom_fields"] = []
    field_config = basics.get("field_config")
    if not isinstance(field_config, dict):
        field_config = {}
        basics["field_config"] = field_config
    for key, cfg in DEFAULT_FIELD_CONFIG.items():
        current = field_config.get(key)
        if not isinstance(current, dict):
            current = {}
        default_label = locale["field_config"].get(key) or cfg["label"]
        field_config[key] = {**cfg, "label": default_label, **current}

    summary = data.setdefault("summary", {"content": ""})
    if isinstance(summary, str):
        data["summary"] = {"content": summary}
    elif not isinstance(summary, dict):
        data["summary"] = {"content": ""}
    else:
        summary.setdefault("content", "")

    for key in ["education", "skills", "work", "projects", "awards", "custom_sections"]:
        if not isinstance(data.get(key), list):
            data[key] = []
    # 自定义字段补齐排版位（row/order）：渲染模板按行内 order 统一排序，缺值/脏值会让排序崩溃或字段被静默丢弃
    for index, field in enumerate(basics.get("custom_fields") or []):
        if isinstance(field, dict):
            if not isinstance(field.get("row"), int) or not 1 <= field["row"] <= 4:
                field["row"] = 1
            if not isinstance(field.get("order"), int):
                field["order"] = 1000 + index
    custom_ids = [
        item.get("id")
        for item in data.get("custom_sections", [])
        if isinstance(item, dict) and item.get("id")
    ]

    layout = data.setdefault("layout", {})
    if not isinstance(layout.get("field_labels"), dict):
        layout["field_labels"] = {}
    layout["hidden_sections"] = [key for key in layout.get("hidden_sections", []) if key != "basics"] if isinstance(layout.get("hidden_sections"), list) else []
    section_order = layout.get("section_order")
    allowed_sections = [*BUILT_IN_SECTIONS, *custom_ids]
    layout["section_order"] = [
        key for key in section_order if key in allowed_sections
    ] if isinstance(section_order, list) and section_order else allowed_sections.copy()
    for key in allowed_sections:
        if key not in layout["section_order"]:
            layout["section_order"].append(key)
    layout["section_order"] = ["basics"] + [key for key in layout["section_order"] if key != "basics"]
    section_titles = layout.setdefault("section_titles", {})
    for key in BUILT_IN_TITLES:
        section_titles[key] = localized_section_title(key, section_titles.get(key), language)
    for item in data.get("custom_sections", []):
        if isinstance(item, dict) and item.get("id"):
            section_titles.setdefault(item["id"], item.get("title") or "自定义模块")
    return data
