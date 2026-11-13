"""Canonical resume JSON schema (single source of truth).

The resume document has always been JSON, but historically the shape was only
implied by the renderers and the starter fixtures: section keys were duplicated
across ``resume_normalization``, ``ai_chat_service`` and the Node tools, item
fields had no declared type, and list entries sometimes arrived without an id.

This module makes the shape explicit and machine-readable:

* :data:`SECTIONS` —每模块的 key / 类型 / 字段表（类型、是否必填、长度上限）；
* :data:`FRAGMENT_KINDS` — 原子信息条目的分类，以及它落到简历哪个模块；
* :func:`normalize_resume_document` — 写入前的统一规范化（补 schema 版本、
  补条目 id、按字段表清洗字符串和数组、去重 id），所有落库路径共用；
* :func:`schema_prompt` / :func:`schema_json` — 给 Agent 的写入契约。

保持向后兼容：规范化只做「补齐 + 清洗」，不删除未知字段（旧简历里的历史
字段不会因为升级被吞掉），也不重排已有条目。
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Optional

from app.services.resume_normalization import normalize_resume_data

# 版本号随字段表变化递增；写入时落到 resume_data["schema_version"]。
SCHEMA_VERSION = "1.1"

LIST_SECTIONS: tuple[str, ...] = (
    "education",
    "skills",
    "work",
    "projects",
    "awards",
    "custom_sections",
)
OBJECT_SECTIONS: tuple[str, ...] = ("basics", "summary", "layout")
SECTION_ORDER: tuple[str, ...] = (
    "basics",
    "summary",
    "education",
    "skills",
    "work",
    "projects",
    "awards",
    "custom_sections",
)

MAX_TEXT = 400
MAX_LONG_TEXT = 4000


@dataclass(frozen=True)
class FieldSpec:
    """一个字段的写入契约。"""

    key: str
    label: str
    type: str = "text"  # text | long_text | date | list
    required: bool = False
    max_length: int = MAX_TEXT


@dataclass(frozen=True)
class SectionSpec:
    key: str
    label: str
    kind: str  # object | list
    description: str
    fields: tuple[FieldSpec, ...] = ()
    id_prefix: str = ""
    title_field: str = ""

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self.fields)


BASICS_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("name", "姓名", required=True, max_length=60),
    FieldSpec("title", "求职方向", max_length=60),
    FieldSpec("status", "当前状态", max_length=40),
    FieldSpec("phone", "电话", max_length=40),
    FieldSpec("email", "邮箱", max_length=120),
    FieldSpec("location", "所在城市", max_length=60),
    FieldSpec("expected_salary", "期望薪资", max_length=40),
    FieldSpec("highest_degree", "最高学历", max_length=40),
    FieldSpec("website", "个人网站", max_length=200),
    FieldSpec("github", "代码仓库", max_length=200),
    FieldSpec("avatar", "头像", max_length=500),
)

CUSTOM_ITEM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("title", "标题", max_length=120),
    FieldSpec("subtitle", "副标题", max_length=160),
    FieldSpec("start_date", "开始时间", "date", max_length=20),
    FieldSpec("end_date", "结束时间", "date", max_length=20),
    FieldSpec("description", "描述", "long_text", max_length=MAX_LONG_TEXT),
    FieldSpec("highlights", "要点", "list"),
    FieldSpec("tech_stack", "技术栈", max_length=200),
)

SECTIONS: dict[str, SectionSpec] = {
    "basics": SectionSpec("basics", "基本信息", "object", "姓名、联系方式等单人字段", BASICS_FIELDS),
    "summary": SectionSpec(
        "summary", "个人简介", "object", "一段自我描述", (FieldSpec("content", "简介", "long_text", max_length=MAX_LONG_TEXT),)
    ),
    "education": SectionSpec(
        "education",
        "教育经历",
        "list",
        "学校、专业、学历与在校经历",
        (
            FieldSpec("school", "学校", required=True, max_length=120),
            FieldSpec("major", "专业", max_length=80),
            FieldSpec("degree", "学历", max_length=40),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "在校经历", "long_text", max_length=MAX_LONG_TEXT),
        ),
        id_prefix="edu",
        title_field="school",
    ),
    "skills": SectionSpec(
        "skills",
        "专业技能",
        "list",
        "技能分类 + 关键词",
        (
            FieldSpec("name", "分类", required=True, max_length=60),
            FieldSpec("keywords", "关键词", "list"),
            FieldSpec("description", "说明", "long_text", max_length=MAX_LONG_TEXT),
        ),
        id_prefix="skill",
        title_field="name",
    ),
    "work": SectionSpec(
        "work",
        "工作/实习经历",
        "list",
        "公司、岗位、职责与成果",
        (
            FieldSpec("company", "公司", required=True, max_length=120),
            FieldSpec("position", "岗位", max_length=80),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "工作内容", "long_text", max_length=MAX_LONG_TEXT),
            FieldSpec("highlights", "成果", "list"),
        ),
        id_prefix="work",
        title_field="company",
    ),
    "projects": SectionSpec(
        "projects",
        "项目经历",
        "list",
        "项目名、角色、技术栈与成果",
        (
            FieldSpec("name", "项目名", required=True, max_length=120),
            FieldSpec("role", "角色", max_length=80),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("tech_stack", "技术栈", max_length=200),
            FieldSpec("description", "项目描述", "long_text", max_length=MAX_LONG_TEXT),
            FieldSpec("highlights", "成果", "list"),
        ),
        id_prefix="project",
        title_field="name",
    ),
    "awards": SectionSpec(
        "awards",
        "荣誉奖项",
        "list",
        "奖项名称、时间与说明",
        (
            FieldSpec("name", "奖项", required=True, max_length=120),
            FieldSpec("date", "时间", "date", max_length=20),
            FieldSpec("description", "说明", "long_text", max_length=MAX_LONG_TEXT),
        ),
        id_prefix="award",
        title_field="name",
    ),
    "custom_sections": SectionSpec(
        "custom_sections",
        "自定义模块",
        "list",
        "证书、语言、竞赛等补充模块容器",
        (
            FieldSpec("title", "模块名", required=True, max_length=60),
            FieldSpec("preset_type", "预设类型", max_length=40),
            FieldSpec("items", "条目", "list"),
        ),
        id_prefix="custom",
        title_field="title",
    ),
}


@dataclass(frozen=True)
class FragmentKindSpec:
    """原子信息分类：决定它落到简历的哪个模块、用哪套字段。"""

    kind: str
    label: str
    section: str
    description: str
    fields: tuple[FieldSpec, ...]
    title_field: str
    # 落到 custom_sections 时使用的预设类型（certifications / languages / …）
    preset_type: str = ""

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self.fields)


FRAGMENT_KINDS: dict[str, FragmentKindSpec] = {
    "basic": FragmentKindSpec(
        "basic", "个人信息", "basics", "姓名、联系方式、求职意向等单人字段", BASICS_FIELDS, "name"
    ),
    "education": FragmentKindSpec(
        "education", "教育经历", "education", "一段学历经历", SECTIONS["education"].fields, "school"
    ),
    "work": FragmentKindSpec("work", "工作经历", "work", "一段全职/兼职工作", SECTIONS["work"].fields, "company"),
    "internship": FragmentKindSpec(
        "internship",
        "实习经历",
        "custom_sections",
        "一段实习经历（独立模块）",
        (
            FieldSpec("company", "公司", required=True, max_length=120),
            FieldSpec("position", "岗位", max_length=80),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "工作内容", "long_text", max_length=MAX_LONG_TEXT),
            FieldSpec("highlights", "成果", "list"),
        ),
        "company",
        preset_type="internships",
    ),
    "project": FragmentKindSpec(
        "project", "项目经历", "projects", "一个项目或作品", SECTIONS["projects"].fields, "name"
    ),
    "skill": FragmentKindSpec("skill", "专业技能", "skills", "一类技能与关键词", SECTIONS["skills"].fields, "name"),
    "award": FragmentKindSpec("award", "荣誉奖项", "awards", "一个奖项或荣誉", SECTIONS["awards"].fields, "name"),
    "certificate": FragmentKindSpec(
        "certificate",
        "证书认证",
        "custom_sections",
        "职业资格、专业认证",
        (
            FieldSpec("name", "证书名称", required=True, max_length=120),
            FieldSpec("issuer", "颁发机构", max_length=120),
            FieldSpec("date", "获得时间", "date", max_length=20),
            FieldSpec("credential_id", "证书编号", max_length=80),
            FieldSpec("url", "证书链接", max_length=300),
            FieldSpec("description", "说明", "long_text", max_length=MAX_LONG_TEXT),
        ),
        "name",
        preset_type="certifications",
    ),
    "language": FragmentKindSpec(
        "language",
        "语言能力",
        "custom_sections",
        "外语水平与考试成绩",
        (
            FieldSpec("name", "语言", required=True, max_length=40),
            FieldSpec("level", "水平", max_length=60),
            FieldSpec("score", "成绩", max_length=40),
            FieldSpec("description", "说明", "long_text", max_length=MAX_LONG_TEXT),
        ),
        "name",
        preset_type="languages",
    ),
    "training": FragmentKindSpec(
        "training",
        "培训经历",
        "custom_sections",
        "课程、训练营、专项培训",
        (
            FieldSpec("name", "课程/培训", required=True, max_length=120),
            FieldSpec("institution", "机构", max_length=120),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "学习内容", "long_text", max_length=MAX_LONG_TEXT),
        ),
        "name",
        preset_type="training",
    ),
    "competition": FragmentKindSpec(
        "competition",
        "竞赛经历",
        "custom_sections",
        "学科/商业/设计竞赛",
        (
            FieldSpec("name", "比赛名称", required=True, max_length=120),
            FieldSpec("award", "奖项/等级", max_length=80),
            FieldSpec("date", "时间", "date", max_length=20),
            FieldSpec("role", "角色", max_length=80),
            FieldSpec("description", "说明", "long_text", max_length=MAX_LONG_TEXT),
        ),
        "name",
        preset_type="competitions",
    ),
    "campus": FragmentKindSpec(
        "campus",
        "校园经历",
        "custom_sections",
        "学生组织、社团、班委",
        (
            FieldSpec("organization", "组织", required=True, max_length=120),
            FieldSpec("role", "角色", max_length=80),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "经历说明", "long_text", max_length=MAX_LONG_TEXT),
            FieldSpec("highlights", "要点", "list"),
        ),
        "organization",
        preset_type="campus",
    ),
    "publication": FragmentKindSpec(
        "publication",
        "论文专利",
        "custom_sections",
        "论文、专利、著作",
        (
            FieldSpec("title", "标题", required=True, max_length=200),
            FieldSpec("publisher", "期刊/专利号/会议", max_length=160),
            FieldSpec("date", "时间", "date", max_length=20),
            FieldSpec("role", "作者/角色", max_length=80),
            FieldSpec("research_area", "研究领域", max_length=120),
            FieldSpec("url", "链接", max_length=300),
            FieldSpec("description", "说明", "long_text", max_length=MAX_LONG_TEXT),
        ),
        "title",
        preset_type="publications",
    ),
    "volunteer": FragmentKindSpec(
        "volunteer",
        "志愿服务",
        "custom_sections",
        "公益、志愿、社会实践",
        (
            FieldSpec("organization", "组织/项目", required=True, max_length=120),
            FieldSpec("role", "角色", max_length=80),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "内容", "long_text", max_length=MAX_LONG_TEXT),
        ),
        "organization",
        preset_type="social_practice",
    ),
    "custom": FragmentKindSpec(
        "custom",
        "自定义",
        "custom_sections",
        "不属于以上分类的单条信息",
        (
            FieldSpec("title", "标题", required=True, max_length=120),
            FieldSpec("subtitle", "副标题", max_length=160),
            FieldSpec("start_date", "开始时间", "date", max_length=20),
            FieldSpec("end_date", "结束时间", "date", max_length=20),
            FieldSpec("description", "描述", "long_text", max_length=MAX_LONG_TEXT),
            FieldSpec("highlights", "要点", "list"),
        ),
        "title",
        preset_type="custom",
    ),
}

FRAGMENT_KIND_ORDER: tuple[str, ...] = tuple(FRAGMENT_KINDS.keys())


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _clean_text(value: Any, max_length: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    # 简历正文里不该出现控制字符（会破坏导出模板排版）
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return text[:max_length]


def _clean_list(value: Any, max_items: int = 40) -> list[str]:
    """把字符串 / 换行文本 / 数组统一成去空、去重、保序的字符串数组。"""
    if value is None:
        return []
    if isinstance(value, str):
        raw: Iterable[Any] = re.split(r"[\n；;]+", value)
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = [value]
    result: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("text") or item.get("content") or ""
        text = _clean_text(item, 400).lstrip("-•· ").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def _clean_scalar(key: str, value: Any, spec: FieldSpec) -> Any:
    if spec.type == "list":
        return _clean_list(value)
    return _clean_text(value, spec.max_length)


def _normalize_item(section: str, item: Any, spec: SectionSpec) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for field in spec.fields:
        if field.key in ("id",):
            continue
        if field.key not in item:
            continue
        cleaned[field.key] = _clean_scalar(field.key, item[field.key], field)
    # 保留历史/扩展字段（不吞数据），只做轻量清洗
    for key, value in item.items():
        if key in cleaned or key in ("id",):
            continue
        if key in spec.field_keys:
            continue
        cleaned[key] = value
    if "id" in item and item.get("id") is not None:
        cleaned["id"] = str(item["id"])
    return cleaned


def _normalize_custom_section(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    result: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "title": _clean_text(item.get("title"), 60),
        "preset_type": _clean_text(item.get("preset_type"), 40),
    }
    raw_items = item.get("items")
    result["items"] = [
        _normalize_custom_item(entry) for entry in (raw_items if isinstance(raw_items, list) else []) if isinstance(entry, dict)
    ]
    for key, value in item.items():
        if key not in result:
            result[key] = value
    return result


def _normalize_custom_item(item: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for field in CUSTOM_ITEM_FIELDS:
        if field.key in item:
            cleaned[field.key] = _clean_scalar(field.key, item[field.key], field)
    for key, value in item.items():
        if key not in cleaned:
            cleaned[key] = value
    return cleaned


def _ensure_unique_id(section: str, item: dict[str, Any], used: set[str], index: int) -> None:
    prefix = SECTIONS[section].id_prefix or section.rstrip("s")
    current = str(item.get("id") or "").strip()
    if not current or current in used:
        n = index + 1
        current = f"{prefix}_{n}"
        while current in used:
            n += 1
            current = f"{prefix}_{n}"
    used.add(current)
    item["id"] = current


def normalize_resume_document(
    data: dict[str, Any] | None,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """把任意来源（前端保存 / AI 提案 / 导入）的简历数据规范成统一结构。

    在 :func:`normalize_resume_data` 的基础上补齐 schema 层：版本号、每个列表
    条目的稳定 id、按字段表清洗文本与数组。未知字段原样保留，避免升级吞数据。
    """
    source = deepcopy(data or {})
    # 先补自定义模块的 id：normalize_resume_data 依赖 custom id 计算 section_order，
    # 缺 id 的模块会被排到顺序之外（等于凭空隐藏）。
    raw_custom = source.get("custom_sections")
    if isinstance(raw_custom, list):
        seen_custom: set[str] = set()
        for index, item in enumerate(raw_custom):
            if not isinstance(item, dict):
                continue
            current = str(item.get("id") or "").strip()
            if not current or current in seen_custom:
                n = index + 1
                current = f"custom_{n}"
                while current in seen_custom:
                    n += 1
                    current = f"custom_{n}"
                item["id"] = current
            seen_custom.add(current)
    document = normalize_resume_data(source, payload)
    document["schema_version"] = SCHEMA_VERSION

    basics = document.get("basics")
    if isinstance(basics, dict):
        for field in BASICS_FIELDS:
            if field.key in basics:
                basics[field.key] = _clean_scalar(field.key, basics[field.key], field)

    summary = document.get("summary")
    if isinstance(summary, dict) and "content" in summary:
        summary["content"] = _clean_text(summary["content"], MAX_LONG_TEXT)

    for section in ("education", "skills", "work", "projects", "awards"):
        spec = SECTIONS[section]
        items = document.get(section)
        if not isinstance(items, list):
            document[section] = []
            continue
        cleaned_items: list[dict[str, Any]] = []
        used: set[str] = set()
        for index, item in enumerate(items):
            cleaned = _normalize_item(section, item, spec)
            if not cleaned:
                continue
            _ensure_unique_id(section, cleaned, used, index)
            cleaned_items.append(cleaned)
        document[section] = cleaned_items

    custom_sections = document.get("custom_sections")
    if isinstance(custom_sections, list):
        cleaned_custom: list[dict[str, Any]] = []
        used_custom: set[str] = set()
        for index, item in enumerate(custom_sections):
            cleaned = _normalize_custom_section(item)
            if not cleaned:
                continue
            if not cleaned.get("id") or cleaned["id"] in used_custom:
                n = index + 1
                while f"custom_{n}" in used_custom:
                    n += 1
                cleaned["id"] = f"custom_{n}"
            used_custom.add(cleaned["id"])
            cleaned_custom.append(cleaned)
        document["custom_sections"] = cleaned_custom

    return document


# ---------------------------------------------------------------------------
# Schema description for the agent
# ---------------------------------------------------------------------------


def _field_line(field: FieldSpec) -> str:
    kind = {"text": "字符串", "date": "字符串 YYYY.MM", "long_text": "字符串", "list": "字符串数组"}[field.type]
    required = "，必填" if field.required else ""
    return f"{field.key}（{field.label}，{kind}{required}）"


@lru_cache(maxsize=1)
def schema_prompt() -> str:
    """给 Agent 的写入契约（纯文本，注入系统提示词）。"""
    lines: list[str] = [
        f"# 简历 JSON 结构（schema {SCHEMA_VERSION}）",
        "简历数据是一个 JSON 对象，每个顶层 key 是一个模块；写入时只改目标模块，不要重排或重编号既有条目。",
    ]
    for key in SECTION_ORDER:
        spec = SECTIONS[key]
        if key == "custom_sections":
            items = "，".join(_field_line(field) for field in CUSTOM_ITEM_FIELDS)
            lines.append(
                f"- {key}（数组，模块容器）: id, title（模块名）, preset_type（预设类型）, "
                f"items（数组，每条含 {items}）"
            )
            continue
        if key == "basics":
            lines.append(f"- basics（对象）: " + "，".join(_field_line(field) for field in BASICS_FIELDS))
            continue
        if key == "summary":
            lines.append("- summary（对象）: content（简介正文，字符串）")
            continue
        prefix = f"- {key}（数组）: id（条目 id，系统生成）"
        lines.append(prefix + ", " + "，".join(_field_line(field) for field in spec.fields))
    lines.extend(
        [
            "- layout（对象）: section_order / hidden_sections / section_titles，只由界面维护，不要手改。",
            "规则：",
            "1. 日期统一写成 \"YYYY.MM\"（未知留空），不要写自然语言时间。",
            "2. highlights / keywords 是字符串数组，一条一个要点，不要塞进 description。",
            "3. 修改已有条目必须带原 id；新增条目不要自带 id，由系统生成。",
            "4. 只使用上面列出的字段，自造字段会被校验拦截。",
            "5. 个人信息（basics）用 update_basics_field；其余模块用 update_section / propose_change。",
        ]
    )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def schema_json() -> dict[str, Any]:
    """机器可读版本，供设置页 / 调试 / 未来的编辑器校验使用。"""
    return {
        "version": SCHEMA_VERSION,
        "sections": [
            {
                "key": key,
                "label": spec.label,
                "kind": spec.kind,
                "description": spec.description,
                "id_prefix": spec.id_prefix,
                "title_field": spec.title_field,
                "fields": [
                    {
                        "key": field.key,
                        "label": field.label,
                        "type": field.type,
                        "required": field.required,
                        "max_length": field.max_length,
                    }
                    for field in spec.fields
                ],
            }
            for key, spec in SECTIONS.items()
        ],
        "fragment_kinds": [
            {
                "kind": kind.kind,
                "label": kind.label,
                "section": kind.section,
                "preset_type": kind.preset_type,
                "title_field": kind.title_field,
                "description": kind.description,
                "fields": [
                    {"key": field.key, "label": field.label, "type": field.type, "required": field.required}
                    for field in kind.fields
                ],
            }
            for kind in FRAGMENT_KINDS.values()
        ],
    }


# ---------------------------------------------------------------------------
# Fragment helpers
# ---------------------------------------------------------------------------


def fragment_kind(kind: str) -> FragmentKindSpec | None:
    return FRAGMENT_KINDS.get(str(kind or "").strip().lower())


def normalize_fragment_payload(kind: str, payload: Any) -> tuple[dict[str, Any], str]:
    """按分类字段表清洗一条原子信息；返回 ``(cleaned, issue)``。"""
    spec = fragment_kind(kind)
    if spec is None:
        return {}, f"不支持的信息分类：{kind}"
    if not isinstance(payload, dict):
        return {}, "信息内容必须是对象"
    cleaned: dict[str, Any] = {}
    for field in spec.fields:
        if field.key not in payload:
            continue
        cleaned[field.key] = _clean_scalar(field.key, payload[field.key], field)
    if spec.kind == "basic":
        # 个人信息是一组字段，不做必填校验（用户可以只存一条联系方式）
        anonymous = {key: value for key, value in cleaned.items() if str(value or "").strip()}
        if not anonymous:
            return {}, "信息内容为空"
        return anonymous, ""
    title_field = spec.title_field
    if not str(cleaned.get(title_field) or "").strip():
        label = next((field.label for field in spec.fields if field.key == title_field), title_field)
        return {}, f"缺少必填字段：{label}"
    # 标题本身就是内容（「一等奖」也是完整的一条荣誉），只要有非空字段就允许保存；
    # 空字符串字段在入库前会被过滤。
    if not any(str(value or "").strip() for value in cleaned.values()):
        return {}, "信息内容为空"
    return cleaned, ""


def fragment_title(kind: str, payload: dict[str, Any]) -> str:
    spec = fragment_kind(kind)
    if spec is None:
        return "未命名信息"
    value = str(payload.get(spec.title_field) or "").strip()
    if value:
        return value[:120]
    parts = [str(payload.get("name") or ""), str(payload.get("company") or ""), str(payload.get("school") or "")]
    return next((part for part in parts if part), "未命名信息")[:120]


def fragment_summary(kind: str, payload: dict[str, Any]) -> str:
    """一句话摘要：优先取 highlights，其次 description，供列表与检索展示。"""
    highlights = payload.get("highlights")
    if isinstance(highlights, list) and highlights:
        return "；".join(str(item) for item in highlights[:3])[:240]
    text = str(payload.get("description") or payload.get("content") or "").strip()
    if text:
        return re.sub(r"\s+", " ", text)[:240]
    for key in ("position", "role", "issuer", "level", "award", "subtitle"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:120]
    return ""
