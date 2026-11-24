from copy import deepcopy
from datetime import datetime
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.contracts import RESUME_TITLE_MAX, normalize_request_id
from app.core.exceptions import AppException
from app.core.resume_schema import normalize_resume_document
from app.core.template_config import normalize_template_config
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume, ResumeVersion
from app.schemas.resume import ResumeCreate, ResumeUpdate, default_template_config
from app.services.pdf_export_service import delete_cached_pdfs
from app.services.resume_locale import normalize_resume_language

logger = logging.getLogger(__name__)

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
VERSION_SOURCE_IMPORT = "import"
VERSION_KEEP_LIMIT = 50
# 自动版本（AI 采纳 / 恢复前备份）在 UI 上的默认说明
VERSION_REASON_AI = "AI 修改前"
VERSION_REASON_RESTORE = "恢复前自动备份"

#
# 简历的写入来源。四条路径（编辑器保存 / AI 采纳 / 信息库导入 / 版本恢复）最终
# 都落到 `persist_resume_data`，只有 source 不同：它决定快照往哪个抽屉放、
# 日志里这条改动该记在谁名下，不是装饰性的字符串。
#
RESUME_WRITE_SOURCES = frozenset(
    {
        VERSION_SOURCE_MANUAL,
        VERSION_SOURCE_AI,
        VERSION_SOURCE_IMPORT,
        VERSION_SOURCE_RESTORE,
    }
)


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


def _find_by_request_id(db: Session, user_id: int, request_id: str) -> Resume | None:
    """幂等键命中的行。重复提交（双击 / 网络重试）要回放首次结果，不能再建一份。"""
    return db.scalar(select(Resume).where(Resume.user_id == user_id, Resume.request_id == request_id))


def create_resume(
    db: Session,
    user_id: int,
    payload: ResumeCreate,
    *,
    request_id: str | None = None,
) -> Resume:
    key = normalize_request_id(request_id)
    if key:
        existing = _find_by_request_id(db, user_id, key)
        if existing is not None:
            return existing
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
        request_id=key,
    )
    db.add(resume)
    try:
        db.commit()
    except IntegrityError:
        # 并发双击时两个请求都过了上面的查重，唯一索引只放行一个；
        # 输的那个回滚后回读赢家的行，对调用方来说就是「一次成功的新建」。
        db.rollback()
        existing = _find_by_request_id(db, user_id, key) if key else None
        if existing is None:
            raise
        return existing
    db.refresh(resume)
    return resume


def _reload_resume_data(db: Session, resume: Resume) -> dict[str, Any]:
    """写完再读一遍，确认库里存的确实是刚提交的那份。

    JSON 列被静默截断时只有回读看得见（历史上出现过「保存成功但内容少了」）。
    单独抽出来是为了能被测试替换——SQLite 和 MySQL 正常情况下都不会截断，
    「读回来的不是写进去的」这个分支只能靠替换读回来覆盖。
    """
    # synchronize_session=False 之后 ORM 不知道自己被改过，必须显式失效再读。
    db.expire(resume)
    db.refresh(resume)
    return deepcopy(resume.resume_data or {})


def persist_resume_data(
    db: Session,
    resume: Resume,
    data: dict[str, Any] | None,
    *,
    source: str,
    expect_revision: int | None = None,
    user_id: int | None = None,
    values: dict[str, Any] | None = None,
) -> Resume:
    """唯一的简历写入函数：条件 UPDATE + 回读校验。

    **为什么不读出来比较、再 setattr 回写**：两个并发请求都能读到同一个
    revision，比较完到写库之间还有窗口，两边都通过、后到的赢，输家不知情。
    把版本判断放进 UPDATE 的 WHERE 里，rowcount 就是唯一的裁决点——数据库来
    保证「只有一个赢」，应用层不需要额外锁。

    `data=None` 表示本次只改元数据（标题 / 模板），JSON 列不动；此时不做
    回读比对，因为 MySQL 的 JSON 列只在真正写入时才可能静默截断。

    调用方负责 normalize：四条路径的规范化细节不同（如恢复要保留快照里人手
    改过的模块标题），那个差异不该藏进写函数里。

    `expect_revision=None` 表示调用方明确放弃冲突保护（例如关页时的 keepalive
    兜底，已经没有界面可以提示冲突了）。
    """
    if source not in RESUME_WRITE_SOURCES:
        raise AppException(f"未知的简历写入来源：{source}", 500)

    next_values: dict[str, Any] = dict(values or {})
    if data is not None:
        next_values["resume_data"] = data
    if not next_values:
        return resume
    # 版本号与时间戳由写函数统一维护，调用方传什么都不算数。
    next_values["revision"] = Resume.revision + 1
    next_values["update_time"] = datetime.now()
    if user_id is not None:
        next_values["update_by"] = user_id

    statement = update(Resume).where(Resume.id == resume.id)
    if expect_revision is not None:
        statement = statement.where(Resume.revision == expect_revision)
    # synchronize_session=False：SET 里含 `revision = revision + 1`，就地求值拿不到
    # 新值；下面用 expire + refresh 让 ORM 对象重新读一遍。
    result = db.execute(
        statement.values(**next_values).execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        # 冲突必须回滚：调用方在同一事务里可能已经暂存了快照（恢复 / AI 采纳）。
        db.rollback()
        latest = db.get(Resume, resume.id)
        if latest is None:
            raise AppException("简历不存在", 404)
        latest_revision = int(latest.revision or 0)
        if expect_revision is None:
            raise AppException("简历写入未命中，请重试", 409, data={"latest_revision": latest_revision})
        raise AppException(
            f"简历已在其它位置被修改（当前第 {latest_revision} 版），本次保存未生效",
            409,
            data={"latest_revision": latest_revision},
        )

    if data is None:
        db.expire(resume)
        db.refresh(resume)
    else:
        persisted = _reload_resume_data(db, resume)
        if persisted != data:
            # 「保存成功」是假的：库里存的不是刚写进去的东西。
            db.rollback()
            raise AppException("简历写入校验失败，内容未保存，请重试", 409)
    # 每次成功写入都留一行「resume_id + revision」：事后复盘一条故障回合时，
    # 只需要 grep 这两个键就能还原「谁在第几版写了什么来源的内容」（M5）。
    logger.info(
        "简历写入 resume_id=%s revision=%s source=%s user_id=%s",
        resume.id,
        int(resume.revision or 0),
        source,
        user_id if user_id is not None else "-",
    )
    return resume


def update_resume(
    db: Session,
    user_id: int,
    resume_id: int,
    payload: ResumeUpdate,
    *,
    source: str = VERSION_SOURCE_MANUAL,
) -> Resume:
    resume = get_resume(db, user_id, resume_id)
    data = payload.model_dump(exclude_unset=True)
    # expect_revision 是协议字段，不是列；留在 data 里会被当成字段写库。
    expect_revision = data.pop("expect_revision", None)

    values: dict[str, Any] = {}
    if data.get("title") is not None:
        values["title"] = data["title"]
    if data.get("language"):
        values["language"] = normalize_resume_language(data["language"])

    template_id = data.get("template_id") or None
    config_input = data.get("template_config")
    if template_id and config_input is None:
        # 复制一份再改：直接改 resume.template_config 会原地动到 ORM 上那份共享
        # dict，等于「只是换个模板」也顺手改了简历对象。
        config_input = deepcopy(resume.template_config) or {}
    if isinstance(config_input, dict):
        # 新建一份 dict：既做白名单，也避免原地改写 ORM 上那份共享对象。
        config = normalize_template_config(config_input)
        if template_id:
            config["template_id"] = template_id
            values["template_id"] = template_id
        values["template_config"] = config
    elif template_id:
        values["template_id"] = template_id

    next_data = None
    if isinstance(data.get("resume_data"), dict):
        # 前端保存与 AI 写回走同一套规范化，避免出现「同一份简历两种形状」。
        next_data = normalize_resume_document(
            data["resume_data"],
            {"language": values.get("language") or resume.language},
        )

    if not values and next_data is None:
        # 只有 expect_revision、没有内容要改：不涨版本。
        return resume
    resume = persist_resume_data(
        db,
        resume,
        next_data,
        source=source,
        expect_revision=expect_revision,
        user_id=user_id,
        values=values,
    )
    # persist_resume_data 只负责「写进事务」；提交时机留给调用方（恢复路径要在
    # 同一事务里先落恢复前备份），所以这一步不能省。
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


def duplicate_resume(
    db: Session,
    user_id: int,
    resume_id: int,
    title: str | None = None,
    *,
    request_id: str | None = None,
) -> Resume:
    key = normalize_request_id(request_id)
    if key:
        existing = _find_by_request_id(db, user_id, key)
        if existing is not None:
            return existing
    source = get_resume(db, user_id, resume_id)
    # 兜底标题也要按上限裁：源标题本身已顶到上限时，「XXX 副本」会超长，直接
    # 写库会撞 MySQL 的 Data too long（历史上就是这个组合让「复制简历」必然 500）。
    # 裁的是源标题、保留后缀，否则副本标题会变得和原件一模一样。
    suffix = " 副本"
    copy_title = (title or "").strip() or (
        f"{source.title[: RESUME_TITLE_MAX - len(suffix)].rstrip()}{suffix}"
    )
    copy = Resume(
        user_id=user_id,
        title=copy_title[:RESUME_TITLE_MAX],
        language=source.language,
        # JSON 列不做深拷贝的话，两份简历会挂同一个 dict：SQLAlchemy 不追踪
        # 原地修改，改副本会静默改到原件（反之亦然）。
        resume_data=deepcopy(source.resume_data),
        template_id=source.template_id,
        template_config=deepcopy(source.template_config),
        create_by=user_id,
        update_by=user_id,
        request_id=key,
    )
    db.add(copy)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _find_by_request_id(db, user_id, key) if key else None
        if existing is None:
            raise
        return existing
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


def stage_version(
    db: Session,
    resume: Resume,
    reason: str,
    source: str = VERSION_SOURCE_MANUAL,
) -> tuple[ResumeVersion, bool]:
    """把「当前内容」暂存成一份快照，返回 (版本, 是否新建)，**不提交**。

    恢复与 AI 采纳都要求快照和简历写入是同一事务：写入撞上 409 时，那份
    「改动前备份」必须一起回滚，否则版本列表里会多出一条没有对应改动的孤儿。
    """
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
        revision=int(resume.revision or 0),
    )
    db.add(version)
    db.flush()
    return version, True


def create_version(
    db: Session,
    user_id: int,
    resume_id: int,
    reason: str,
    source: str = VERSION_SOURCE_MANUAL,
) -> tuple[ResumeVersion, bool]:
    """手动存档：暂存快照后立刻提交，并回收超限的自动版本。

    内容与上一版逐字节一致时直接复用旧行（`created=False`），避免重复快照。
    """
    resume = get_resume(db, user_id, resume_id)
    version, created = stage_version(db, resume, reason, source)
    if created:
        prune_versions(db, resume.id)
    db.commit()
    db.refresh(version)
    return version, created


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
            ResumeVersion.revision,
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
    """把时间截到秒并去掉时区，用于兼容老客户端的秒级乐观锁。

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
    expected_revision: int | None = None,
) -> Resume:
    """把某个版本写回简历。

    三步都不能省：
    1. 先把「当前内容」存成一份备份快照——恢复本身也要能反悔；
    2. 写回后回读比对，不一致回滚（JSON 列的静默截断只有回读才看得见，
       和 AI 应用走同一套 409 口径）；
    3. 前端带上打开版本列表时的 revision，防止把另一标签页刚落库的修改冲掉。
       `expected_update_time` 只为老客户端保留：秒级时间戳在同一秒内的两次
       写入上分辨不出来，新代码一律传 revision。
    """
    resume = get_resume(db, user_id, resume_id)
    version = get_version(db, user_id, resume_id, version_id)
    expect = expected_revision
    if expect is None and expected_update_time is not None:
        if _seconds(resume.update_time) != _seconds(expected_update_time):
            raise AppException(
                "简历已被其他操作更新，请刷新版本列表后重试",
                409,
                data={"latest_revision": int(resume.revision or 0)},
            )
        # 时间戳已经比过了，写的时候不再卡版本：这一次 UPDATE 会自己把
        # revision 推进一格，再拿旧值当条件没有意义。
        expect = None

    # 快照与写回同一事务（stage_version 不提交）：写回撞 409 时，这份
    # 「恢复前备份」必须一起回滚。
    stage_version(db, resume, VERSION_REASON_RESTORE, VERSION_SOURCE_RESTORE)

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

    resume = persist_resume_data(
        db,
        resume,
        next_data,
        source=VERSION_SOURCE_RESTORE,
        expect_revision=expect,
        user_id=user_id,
        values={"template_id": template_id, "template_config": config},
    )
    prune_versions(db, resume.id)
    db.commit()
    db.refresh(resume)
    return resume
