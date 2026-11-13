"""起手模板内容的规范化规则（纯函数，不碰数据库）。

岗位内容来自管理端手填或内置数据，缺字段、层级变体不全、技能深度不够都是常态；
这里把它们补齐成渲染可直接消费的形状。

注：`resume_starter_service` / `resume_starter_render` 会直接复用本模块下划线
前缀的 helper，这是包内约定，不是漏改的私有调用。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.resume_starter_data import INDUSTRY_MODULE_DEFAULTS, ROLE_MODULE_OVERRIDES, STANDARD_MODULES, STARTER_LEVELS


def _modules_for_role(industry_id: str, starter_id: str, content: dict[str, Any]) -> list[dict[str, str]]:
    modules = content.get("modules")
    if isinstance(modules, list) and modules:
        return [item for item in modules if isinstance(item, dict) and item.get("key") and item.get("title")]
    return ROLE_MODULE_OVERRIDES.get(starter_id) or INDUSTRY_MODULE_DEFAULTS.get(industry_id) or STANDARD_MODULES


def _ensure_skill_depth(role: dict[str, Any], content: dict[str, Any]) -> None:
    skills = content.get("skills") if isinstance(content.get("skills"), list) else []
    keywords = role.get("keywords") or []
    fallback = [
        {"name": "岗位核心能力", "keywords": keywords[:4], "description": "能够围绕岗位目标拆解任务、推进执行，并通过复盘持续优化结果。"},
        {"name": "业务理解与判断", "keywords": keywords[4:8] or keywords[:4], "description": "理解行业规则、用户或客户需求，能够在效率、质量和风险之间做出稳妥判断。"},
        {"name": "数据与复盘", "keywords": ["数据记录", "指标跟踪", "问题定位", "复盘优化"], "description": "能够记录关键过程和结果数据，定位影响目标的主要因素并提出改进动作。"},
        {"name": "协作与交付", "keywords": ["跨团队沟通", "进度管理", "流程沉淀", "风险反馈"], "description": "能够与上下游保持清晰沟通，推动事项闭环并沉淀可复用模板或 SOP。"},
    ]
    merged = [item for item in skills if isinstance(item, dict)]
    for item in fallback:
        if len(merged) >= 4:
            break
        if not any(existing.get("name") == item["name"] for existing in merged):
            merged.append(item)
    content["skills"] = merged


def _ensure_content_defaults(industry: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(role)
    content = normalized.setdefault("content", {})
    content["modules"] = _modules_for_role(industry["industry_id"], normalized["starter_id"], content)
    _ensure_skill_depth(normalized, content)
    _ensure_level_variants({**normalized, "industry_name": industry["industry_name"]}, content)
    return normalized


def _level(level_id: str) -> dict[str, str]:
    return next((item for item in STARTER_LEVELS if item["id"] == level_id), STARTER_LEVELS[1])


def _starter_value(source: Any, key: str, default: Any = "") -> Any:
    if isinstance(source, dict):
        return source.get(key) or default
    return getattr(source, key, default) or default


def _starter_keywords(source: Any) -> list[str]:
    keywords = _starter_value(source, "keywords", [])
    return [str(item).strip() for item in keywords if str(item).strip()] if isinstance(keywords, list) else []


def _default_level_variant(source: Any, level: dict[str, str]) -> dict[str, Any]:
    role_title = str(_starter_value(source, "role_title", "目标岗位"))
    industry_name = str(_starter_value(source, "industry_name", "目标行业"))
    keywords = "、".join(_starter_keywords(source)[:3]) or role_title
    if level["id"] == "fresh":
        return {
            "summary": f"具备{role_title}岗位所需的基础认知和实践训练，熟悉{keywords}等方向，可从资料整理、执行跟进、项目协作和复盘优化切入。",
            "work_title": "实习/实践经历",
            "projects_title": "校园/实践项目",
            "education_description": "建议补充与目标岗位相关的主修课程、课程项目、竞赛经历、社团职责、实训作品或证书。",
            "work_description": f"围绕{industry_name}基础场景参与实习、校园实践或助理型工作，重点积累岗位流程、协作方式和可复用方法。",
            "project_description": f"围绕{role_title}岗位能力进行课程、实训或个人实践项目，重点展示学习能力、执行过程和成果表达。",
            "work_highlights": [
                f"参与{industry_name}相关基础工作，完成资料整理、流程记录、执行跟进或用户/业务反馈收集。",
                "在指导下理解岗位流程和协作方式，沉淀表格、清单或复盘记录，提升后续执行效率。",
            ],
            "project_highlights": [
                f"围绕{keywords}完成资料查阅、需求理解、方案执行和成果整理，形成可展示的实践产出。",
                "在老师、导师或团队成员指导下承担明确模块，按计划推进任务并及时同步进展。",
            ],
        }
    if level["id"] == "junior":
        return {
            "summary_suffix": "能够独立完成明确任务，关注执行质量、沟通反馈和阶段性结果。",
            "work_title": "工作经历",
            "projects_title": "项目经历",
            "work_description": f"围绕{industry_name}实际业务场景承担{role_title}相关执行工作，关注任务交付、沟通反馈和质量稳定。",
            "project_description": f"围绕真实业务问题完成{role_title}相关项目任务，覆盖需求理解、方案执行、结果验证和复盘优化。",
            "work_highlights": [
                f"独立承担{role_title}相关明确任务，按节点完成交付并及时反馈风险和进展。",
                "结合业务反馈或过程数据优化执行细节，提升效率、质量或协作体验。",
            ],
        }
    if level["id"] == "mid":
        return {
            "summary_suffix": "能够负责完整模块或项目事项，兼顾方案拆解、跨团队协作、过程把控和结果复盘。",
            "work_title": "工作经历",
            "projects_title": "项目经历",
            "work_description": f"负责{role_title}相关模块或专项事项，覆盖目标拆解、过程推进、跨团队协作和结果复盘。",
            "project_description": f"负责{role_title}相关专项项目，连接业务目标、协作资源和落地动作，推动项目按阶段达成结果。",
            "work_highlights": [
                f"负责{role_title}相关模块或专项事项，拆解目标、协调上下游并推进结果落地。",
                "围绕关键指标复盘执行过程，沉淀方法、流程或模板，提升团队复用效率。",
            ],
        }
    return {
        "summary_suffix": "擅长拆解复杂目标、搭建流程机制、协调关键资源并带动团队持续优化。",
        "work_title": "核心工作经历",
        "projects_title": "重点项目",
        "work_description": f"负责{role_title}相关核心方向或复杂项目，推动流程机制建设、关键问题解决和团队协同提效。",
        "project_description": f"主导{role_title}相关核心项目或体系建设，统筹目标拆解、资源协调、风险控制和机制沉淀。",
        "work_highlights": [
            f"主导{role_title}相关复杂项目或核心模块，明确目标、资源、节奏和验收标准。",
            "搭建协作机制、质量标准或复盘体系，推动团队在效率、稳定性或业务结果上持续改善。",
        ],
    }


def _ensure_level_variants(source: Any, content: dict[str, Any]) -> bool:
    variants = content.get("level_variants")
    if not isinstance(variants, dict):
        variants = {}
        content["level_variants"] = variants
    changed = False
    for level in STARTER_LEVELS:
        current = variants.get(level["id"])
        if not isinstance(current, dict) or not current:
            variants[level["id"]] = _default_level_variant(source, level)
            changed = True
    return changed
