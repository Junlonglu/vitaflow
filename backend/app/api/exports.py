from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.security import get_current_user
from app.models.export_record import ExportRecord
from app.models.user import User
from app.services.pdf_export_service import export_pdf
from app.services.resume_json_export import INLINE_FILE_PATH, export_file_name, export_json_bytes
from app.services.resume_service import get_resume

router = APIRouter(prefix="/resumes", tags=["exports"])

WORD_EXPORT_DISABLED_MESSAGE = "Word 导出已暂停使用，请改用 PDF 导出"


@router.post("/{resume_id}/export/pdf")
def export_resume_pdf(resume_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    resume = get_resume(db, current_user.id, resume_id)
    path = export_pdf(db, current_user.id, resume)
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@router.post("/{resume_id}/export/json")
def export_resume_json(resume_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """导出结构化简历快照，可直接回填成一份新简历（``POST /resumes/import-file``）。

    不落盘：JSON 是纯序列化，没有渲染成本，也没有复用价值，缓存只会留下垃圾文件。
    因此 ``export_records.file_path`` 写 ``inline:json`` 占位，表示这条记录没有可下载的文件。
    """
    resume = get_resume(db, current_user.id, resume_id)
    file_name = export_file_name(resume)
    payload = export_json_bytes(resume)
    db.add(
        ExportRecord(
            user_id=current_user.id,
            resume_id=resume.id,
            file_type="json",
            file_name=file_name,
            file_path=INLINE_FILE_PATH,
            status="success",
        )
    )
    db.commit()
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _attachment_disposition(file_name)},
    )


def _attachment_disposition(file_name: str) -> str:
    # HTTP 头只能是 latin-1，中文标题必须走 RFC 5987 的 filename*，同时留一个 ASCII
    # 兜底名。中文被剔掉后往往只剩「1-3 .json」这类残渣，不含字母就退回通用名。
    ascii_name = " ".join(file_name.encode("ascii", "ignore").decode().split())
    stem = ascii_name[:-5] if ascii_name.lower().endswith(".json") else ascii_name
    if not stem.strip() or not any(char.isalpha() for char in stem):
        ascii_name = "resume.json"
    elif not ascii_name.lower().endswith(".json"):
        ascii_name = f"{ascii_name}.json"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"


@router.post("/{resume_id}/export/word")
def export_resume_word(resume_id: int, current_user: User = Depends(get_current_user)):
    """Word 导出暂时下线，接口保留但不再产出文件。

    ``word_export_service`` 尚未映射 ``template_config``（字体 / 主题色 / 页边距），
    导出的 docx 与预览、PDF 的观感不一致。在补齐映射前先关掉入口，避免用户拿到
    一份「看着不像自己简历」的文件。

    恢复方式：函数体换回 ``get_resume(...)`` + ``export_word(...)`` 并返回
    ``FileResponse``，服务实现未删除。
    """
    raise AppException(WORD_EXPORT_DISABLED_MESSAGE, 410)
