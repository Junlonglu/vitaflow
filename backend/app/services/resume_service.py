from copy import deepcopy
import hashlib
import json

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.resume_schema import normalize_resume_document
from app.core.template_config import normalize_template_config
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume, ResumeVersion
from app.schemas.resume import ResumeCreate, ResumeUpdate, default_template_config
from app.services.pdf_export_service import delete_cached_pdfs
from app.services.resume_locale import normalize_resume_language

# 简历库排序：默认按最近编辑，其余给「新建」「标题」两个稳定选项。
RESUME_SORTS = ("updated", "created", "title")
DEFAULT_RESUME_SORT = "updated"

#
# 版本快照的来源与保留策略。
#
# 「每次自动保存存一版」会让一个写作下午产生上百条全量 JSON，所以自动写入
# 必须先过内容指纹：和上一版逐字节相同就复用旧行。手动存档是用户显式动作，
# 清理时永远不淘汰它，只回收 auto 产生的版本。
VERSION_SOURCE_MANUAL = "manual"
VERSION_SOURCE_AI = "ai"
VERSION_SOURCE_RESTORE = "restore"
VERSION_KEEP_LIMIT = 50
# 自动版本（AI 采纳 / 恢复前备份）在 UI 上的默认说明
VERSION_REASON_AI = "AI 修改前"
VERSION_REASON_RESTORE = "恢复前自动备份"


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
    # 白名单收敛后再落库：模板配置会被插进 <style>，非法值一旦入库就会
    # 在预览/分享/PDF 三条链路上同时生效。
    config = normalize_template_config(payload.template_config or default_template_config(payload.template_id))
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
        # 复制一份再改：直接 setdefault(resume.template_config) 会原地改写 ORM
        # 上那份共享 dict，等于「只是换个模板」也顺手改了简历对象。
        base_config = data.get("template_config") or deepcopy(resume.template_config) or {}
        base_config["template_id"] = data["template_id"]
        data["template_config"] = base_config
    if isinstance(data.get("template_config"), dict):
        # 新建一份 dict：既做白名单，也避免原地改写 ORM 上那份共享对象。
        config = normalize_template_config(data["template_config"])
        if data.get("template_id"):
            config["template_id"] = data["template_id"]
        data["template_config"] = config
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
        # JSON 列不做深拷贝的话，两份简历会挂同一个 dict：SQLAlchemy 不追踪
        # 原地修改，改副本会静默改到原件（反之亦然）。
        resume_data=deepcopy(source.resume_data),
        template_id=source.template_id,
        template_config=deepcopy(source.template_config),
        create_by=user_id,
        update_by=user_id,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy


def resume_version_digest(resume_data: dict | None, template_config: dict | None) -> tuple[str, int]:
    """返回 (内容指纹, 序列化字节数)。

    指纹用于「与上一版是否完全相同」的判断：编辑器是防抖整包 PUT，同一次
    保存可能被触发多次，没有指纹就会写出一串一模一样的快照。
    """
    raw = json.dumps(
        {"resume_data": resume_data or {}, "template_config": template_config or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _latest_version(db: Session, resume_id: int) -> ResumeVersion | None:
    return db.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.resume_id == resume_id)
        .order_by(ResumeVersion.id.desc())
        .limit(1)
    ).first()


def create_version(
    db: Session,
    user_id: int,
    resume_id: int,
    reason: str,
    source: str = VERSION_SOURCE_MANUAL,
) -> tuple[ResumeVersion, bool]:
    """写一份快照，返回 (版本, 是否新建)。

    内容与上一版逐字节一致时直接复用旧行（`created=False`），避免重复快照。
    """
    resume = get_resume(db, user_id, resume_id)
    digest, size = resume_version_digest(resume.resume_data, resume.template_config)
    latest = _latest_version(db, resume.id)
    if latest is not None and latest.content_hash == digest:
        return latest, False

    version = ResumeVersion(
        resume_id=resume.id,
        # 版本是快照：必须和当前简历脱离引用，否则后续编辑会改到历史版本。
        resume_data=deepcopy(resume.resume_data),
        template_config=deepcopy(resume.template_config),
        reason=(reason or "").strip()[:255] or "版本快照",
        source=source,
        content_hash=digest,
        size_bytes=size,
    )
    db.add(version)
    db.flush()
    prune_versions(db, resume.id)
    db.commit()
    db.refresh(version)
    return version, True


def prune_versions(db: Session, resume_id: int) -> int:
    """把超出保留上限的自动快照删掉；手动存档不参与淘汰。"""
    total = db.scalar(
        select(func.count()).select_from(ResumeVersion).where(ResumeVersion.resume_id == resume_id)
    ) or 0
    excess = total - VERSION_KEEP_LIMIT
    if excess <= 0:
        return 0
    stale_ids = list(
        db.scalars(
            select(ResumeVersion.id)
            .where(
                ResumeVersion.resume_id == resume_id,
                ResumeVersion.source != VERSION_SOURCE_MANUAL,
            )
            .order_by(ResumeVersion.id.asc())
            .limit(excess)
        )
    )
    if not stale_ids:
        return 0
    db.query(ResumeVersion).filter(ResumeVersion.id.in_(stale_ids)).delete(synchronize_session=False)
    return len(stale_ids)


def list_versions(
    db: Session,
    user_id: int,
    resume_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list, int]:
    """版本列表。

    只取列表需要的列：整份 resume_data 有十几 KB，50 条一起返回就是一次
    无谓的 JSON 传输，前端列表也用不到。
    """
    resume = get_resume(db, user_id, resume_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 50)
    condition = ResumeVersion.resume_id == resume.id
    total = db.scalar(select(func.count()).select_from(ResumeVersion).where(condition)) or 0
    rows = db.execute(
        select(
            ResumeVersion.id,
            ResumeVersion.reason,
            ResumeVersion.source,
            ResumeVersion.template_config,
            ResumeVersion.size_bytes,
            ResumeVersion.create_time,
        )
        .where(condition)
        .order_by(ResumeVersion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total


def get_version(db: Session, user_id: int, resume_id: int, version_id: int) -> ResumeVersion:
    # 必须先按 user_id 拿到简历，再按 (resume_id, id) 查版本：
    # 只 db.get(ResumeVersion, version_id) 等于允许任何人拿别人的版本 id 读内容。
    resume = get_resume(db, user_id, resume_id)
    version = db.get(ResumeVersion, version_id)
    if not version or version.resume_id != resume.id:
        raise AppException("版本不存在", 404)
    return version


def _seconds(value):
    """把时间截到秒并去掉时区，用于乐观锁比较。

    前端回传的是服务端原样的 naive 时间；外部若塞了带时区的值，naive 与 aware
    直接比较会抛 TypeError（变 500），这里统一降级成 naive。
    """
    if not value:
        return None
    return value.replace(tzinfo=None, microsecond=0)


def restore_version(
    db: Session,
    user_id: int,
    resume_id: int,
    version_id: int,
    expected_update_time=None,
) -> Resume:
    """把某个版本写回简历。

    三步都不能省：
    1. 先把「当前内容」存成一份备份快照——恢复本身也要能反悔；
    2. 写回后 flush + expire 再读一遍比对，不一致回滚（JSON 列的静默截断
       只有回读才看得见，和 AI 应用走同一套 409 口径）；
    3. 前端带上打开版本列表时的 update_time，防止把另一标签页刚落库的修改冲掉。
    """
    resume = get_resume(db, user_id, resume_id)
    version = get_version(db, user_id, resume_id, version_id)
    if expected_update_time is not None and _seconds(resume.update_time) != _seconds(expected_update_time):
        raise AppException("简历已被其他操作更新，请刷新版本列表后重试", 409)

    create_version(db, user_id, resume_id, VERSION_REASON_RESTORE, VERSION_SOURCE_RESTORE)

    # 标题属于内容：规范化会补默认标题，快照里已人工改过的标题必须原样保留。
    restored_data = deepcopy(version.resume_data or {})
    titles_in = deepcopy((restored_data.get("layout") or {}).get("section_titles"))
    next_data = normalize_resume_document(restored_data, {"language": resume.language})
    if isinstance(titles_in, dict) and titles_in:
        next_data.setdefault("layout", {})["section_titles"] = {
            **(next_data.get("layout", {}).get("section_titles") or {}),
            **titles_in,
        }

    config = normalize_template_config(deepcopy(version.template_config or {}))
    template_id = str(config.get("template_id") or resume.template_id)
    config["template_id"] = template_id

    resume.resume_data = next_data
    resume.template_id = template_id
    resume.template_config = config
    resume.update_by = user_id
    db.add(resume)
    db.flush()
    db.expire(resume, ["resume_data", "template_config"])
    persisted = deepcopy(resume.resume_data or {})
    if persisted != next_data:
        db.rollback()
        raise AppException("版本恢复写入校验失败，简历未发生变化，请重试", 409)

    db.commit()
    db.refresh(resume)
    return resume
