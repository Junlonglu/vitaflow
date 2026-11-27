"""原子信息库接口。

用户级（不是简历级）：同一条项目经历可以被多份简历复用，所以在 `resume_id`
之外单独建库。所有读写都带 ``user_id``，跨用户访问一律 404。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.info_fragment import (
    InfoFragmentApplyIn,
    InfoFragmentBulkIn,
    InfoFragmentExtractIn,
    InfoFragmentIn,
    InfoFragmentUpdateIn,
)
from app.schemas.resume import ResumeUpdate
from app.services import info_fragment_service as svc
from app.services import resume_service

router = APIRouter(prefix="/info-fragments", tags=["info-fragments"])


@router.get("")
def list_fragments(
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=svc.DEFAULT_PAGE_SIZE, ge=1, le=svc.MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total = svc.list_fragments(
        db, current_user.id, kind=kind, keyword=q, tag=tag, page=page, page_size=page_size
    )
    return success(
        {
            "items": [svc.fragment_out(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "counts": svc.counts_by_kind(db, current_user.id),
        }
    )


@router.get("/meta")
def fragment_meta(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return success(
        {
            "kinds": svc.kind_options(),
            "counts": svc.counts_by_kind(db, current_user.id),
            "limit": svc.MAX_FRAGMENTS_PER_USER,
        }
    )


@router.post("")
def create_fragment(
    payload: InfoFragmentIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row, created = svc.create_fragment(
        db,
        current_user.id,
        payload.kind,
        payload.payload,
        title=payload.title,
        tags=payload.tags,
        source=payload.source,
    )
    return success(svc.fragment_out(row), "已保存到信息库" if created else "信息库里已有这条信息，已合并")


@router.post("/bulk")
def bulk_create(
    payload: InfoFragmentBulkIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = svc.bulk_create(
        db,
        current_user.id,
        [item.model_dump() for item in payload.items],
    )
    message = f"已保存 {result['created']} 条" + (f"，合并 {result['updated']} 条" if result["updated"] else "")
    return success(result, message)


@router.put("/{fragment_id}")
def update_fragment(
    fragment_id: int,
    payload: InfoFragmentUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = svc.update_fragment(
        db,
        current_user.id,
        fragment_id,
        kind=payload.kind,
        payload=payload.payload,
        title=payload.title,
        tags=payload.tags,
    )
    return success(svc.fragment_out(row), "信息已更新")


@router.post("/{fragment_id}/default")
def set_default_fragment(
    fragment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把一条个人信息设为默认（自动补基本信息时取这一条）。"""
    row = svc.set_default_fragment(db, current_user.id, fragment_id)
    return success(svc.fragment_out(row), "已设为默认个人信息")


@router.delete("/{fragment_id}")
def delete_fragment(
    fragment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc.delete_fragment(db, current_user.id, fragment_id)
    return success(None, "信息已删除")


@router.post("/extract")
def extract_from_resume(
    payload: InfoFragmentExtractIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = resume_service.get_resume(db, current_user.id, payload.resume_id)
    result = svc.extract_from_resume(db, current_user.id, resume)
    message = f"从简历提取 {result['created']} 条信息" + (f"，已存在 {result['updated']} 条" if result["updated"] else "")
    return success(result, message)


@router.post("/apply")
def apply_to_resume(
    payload: InfoFragmentApplyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把信息导入简历。

    用户点了「导入」就是明确授权，这里直接写入并返回最新 resume_data；需要预览
    或逐条确认的改写走对话里的待确认修改（import_info_fragments 工具）。
    """
    resume = resume_service.get_resume(db, current_user.id, payload.resume_id)
    merged, added, rows = svc.import_fragments(
        db,
        current_user.id,
        resume,
        payload.fragment_ids,
        overwrite_placeholders=payload.overwrite_placeholders,
    )
    updated = resume_service.update_resume(
        db,
        current_user.id,
        resume.id,
        ResumeUpdate(resume_data=merged, expect_revision=resume.revision),
        # 来源只影响快照与审计归属；写入协议（版本号 + 条件 UPDATE）四条路径共用。
        # merged 是基于上面读到的 resume_data 算的，所以版本号也带读到的那个：
        # 期间若有人保存过，这次导入应该 409 让用户重来，而不是把对方的改动冲掉。
        source=resume_service.VERSION_SOURCE_IMPORT,
    )
    # 没有新增条目时不虚增使用次数；写回仍要做，因为可能刚取消了模块隐藏。
    if added:
        svc.touch_usage(db, rows)
    return success(
        {"resume_id": updated.id, "resume_data": updated.resume_data, "added": added},
        f"已导入 {len(added)} 条信息到简历" if added else "这些信息已在简历中",
    )
