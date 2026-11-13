from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.resume_schema import normalize_resume_document
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume, ResumeVersion
from app.schemas.resume import ResumeCreate, ResumeUpdate, default_template_config
from app.services.pdf_export_service import delete_cached_pdfs
from app.services.resume_locale import normalize_resume_language

# 简历库排序：默认按最近编辑，其余给「新建」「标题」两个稳定选项。
RESUME_SORTS = ("updated", "created", "title")
DEFAULT_RESUME_SORT = "updated"


def _resume_sort_clause(sort: str | None):
    if sort == "created":
        return (Resume.create_time.desc(), Resume.id.desc())
    if sort == "title":
        return (Resume.title.asc(), Resume.id.asc())
    return (Resume.update_time.desc(), Resume.id.desc())


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，避免用户输入 % 或 _ 时把全表都匹配出来。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_resumes(
    db: Session,
    user_id: int,
    page: int = 1,
    page_size: int = 8,
    keyword: str | None = None,
    sort: str | None = DEFAULT_RESUME_SORT,
) -> tuple[list[Resume], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 48)
    filters = [Resume.user_id == user_id]
    text = (keyword or "").strip()
    if text:
        filters.append(Resume.title.like(f"%{_escape_like(text)}%", escape="\\"))
    condition = and_(*filters)
    total = db.scalar(select(func.count()).select_from(Resume).where(condition)) or 0
    items = list(
        db.scalars(
            select(Resume)
            .where(condition)
            .order_by(*_resume_sort_clause(sort))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def get_resume(db: Session, user_id: int, resume_id: int) -> Resume:
    resume = db.get(Resume, resume_id)
    # 越权和不存在返回同一个 404，不向调用方泄露「这个 id 存在但不属于你」
    if not resume or resume.user_id != user_id:
        raise AppException("简历不存在", 404)
    return resume


def create_resume(db: Session, user_id: int, payload: ResumeCreate) -> Resume:
    config = payload.template_config or default_template_config(payload.template_id)
    config["template_id"] = payload.template_id
    language = normalize_resume_language(payload.language)
    # 统一结构：补 schema 版本、条目 id、字段类型清洗（前端 / 导入 / AI 共用同一入口）
    resume_data = normalize_resume_document(payload.resume_data, {"language": language})
    resume = Resume(
        user_id=user_id,
        title=payload.title,
        language=language,
        resume_data=resume_data,
        template_id=payload.template_id,
        template_config=config,
        create_by=user_id,
        update_by=user_id,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def update_resume(db: Session, user_id: int, resume_id: int, payload: ResumeUpdate) -> Resume:
    resume = get_resume(db, user_id, resume_id)
    data = payload.model_dump(exclude_unset=True)
    if "language" in data:
        data["language"] = normalize_resume_language(data["language"])
    if "template_id" in data and data["template_id"]:
        data.setdefault("template_config", resume.template_config or {})
        data["template_config"]["template_id"] = data["template_id"]
    if isinstance(data.get("resume_data"), dict):
        # 前端保存与 AI 写回走同一套规范化，避免出现「同一份简历两种形状」。
        data["resume_data"] = normalize_resume_document(
            data["resume_data"], {"language": data.get("language") or resume.language}
        )
    for key, value in data.items():
        setattr(resume, key, value)
    resume.update_by = user_id
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def delete_resume(db: Session, user_id: int, resume_id: int) -> None:
    resume = get_resume(db, user_id, resume_id)
    # 无外键级联：对话记录、版本快照需随简历显式删除（删除确认文案已告知用户）
    db.query(AiChatMessage).filter(AiChatMessage.resume_id == resume.id).delete(synchronize_session=False)
    db.query(AiChatSession).filter(AiChatSession.resume_id == resume.id).delete(synchronize_session=False)
    db.query(ResumeVersion).filter(ResumeVersion.resume_id == resume.id).delete(synchronize_session=False)
    delete_cached_pdfs(resume.id)
    db.delete(resume)
    db.commit()


def duplicate_resume(db: Session, user_id: int, resume_id: int, title: str | None = None) -> Resume:
    source = get_resume(db, user_id, resume_id)
    copy = Resume(
        user_id=user_id,
        title=(title or "").strip()[:120] or f"{source.title} 副本",
        language=source.language,
        resume_data=source.resume_data,
        template_id=source.template_id,
        template_config=source.template_config,
        create_by=user_id,
        update_by=user_id,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy


def create_version(db: Session, user_id: int, resume_id: int, reason: str) -> ResumeVersion:
    resume = get_resume(db, user_id, resume_id)
    version = ResumeVersion(
        resume_id=resume.id,
        resume_data=resume.resume_data,
        template_config=resume.template_config,
        reason=reason,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version
