from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.security import get_current_user
from app.models.user import User
from app.services.pdf_export_service import export_pdf
from app.services.resume_service import get_resume

router = APIRouter(prefix="/resumes", tags=["exports"])

WORD_EXPORT_DISABLED_MESSAGE = "Word 导出已暂停使用，请改用 PDF 导出"


@router.post("/{resume_id}/export/pdf")
def export_resume_pdf(resume_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    resume = get_resume(db, current_user.id, resume_id)
    path = export_pdf(db, current_user.id, resume)
    return FileResponse(path, filename=path.name, media_type="application/pdf")


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
