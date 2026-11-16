"""PDF 导出端点与导出服务。

路由层很薄：所有权校验（get_resume）+ 生成（export_pdf）+ 返回文件。这里把
service patch 掉，固定文件响应头与越权 404；再单测 export_pdf 会写一条
export_records，以及 get_pdf_path 命中缓存时不再渲染。
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exports import router as exports_router
from app.core.database import get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.core.security import get_current_user
from app.services import pdf_export_service


def _make_client(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(exports_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


def _resume():
    return SimpleNamespace(
        id=7,
        user_id=1,
        resume_data={"basics": {"name": "张三"}},
        template_id="tech",
        template_config={"theme_color": "#2563eb"},
        language="zh-CN",
    )


class PdfExportRouteTest(unittest.TestCase):
    def test_pdf_returns_pdf_file_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "张三的简历.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake")
            with (
                patch("app.api.exports.get_resume", return_value=_resume()),
                patch("app.api.exports.export_pdf", return_value=pdf) as mock_export,
            ):
                resp = _make_client(MagicMock()).post("/api/resumes/7/export/pdf")
            # FileResponse 是异步读盘的，TestClient 已把内容读进 resp.content。
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.headers["content-type"].startswith("application/pdf"))
            self.assertIn("attachment;", resp.headers["content-disposition"])
            self.assertIn(".pdf", resp.headers["content-disposition"])
            self.assertEqual(resp.content, b"%PDF-1.4 fake")
            mock_export.assert_called_once()

    def test_other_users_resume_is_404(self):
        with patch("app.api.exports.get_resume", side_effect=AppException("简历不存在", 404)):
            resp = _make_client(MagicMock()).post("/api/resumes/99/export/pdf")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], 404)

    def test_export_failure_does_not_return_200(self):
        from fastapi import HTTPException

        with (
            patch("app.api.exports.get_resume", return_value=_resume()),
            patch("app.api.exports.export_pdf", side_effect=HTTPException(500, "PDF 生成失败：x")),
        ):
            resp = _make_client(MagicMock()).post("/api/resumes/7/export/pdf")
        self.assertEqual(resp.status_code, 500)


class ExportPdfServiceTest(unittest.TestCase):
    def test_export_pdf_records_an_export_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "resume_7_chromium_x.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            db = MagicMock()
            with patch("app.services.pdf_export_service.get_pdf_path", return_value=pdf):
                result = pdf_export_service.export_pdf(db, 1, _resume())

        self.assertEqual(result, pdf)
        record = db.add.call_args.args[0]
        self.assertEqual(record.user_id, 1)
        self.assertEqual(record.resume_id, 7)
        self.assertEqual(record.file_type, "pdf")
        self.assertEqual(record.status, "success")
        self.assertEqual(record.file_path, str(pdf))
        db.commit.assert_called_once()

    def test_cached_pdf_skips_render(self):
        resume = _resume()
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            # 造一个非空缓存文件；_cache_path 依赖 _cache_dir，patch 掉它。
            with (
                patch("app.services.pdf_export_service._cache_dir", return_value=cache_dir),
                patch("app.services.pdf_export_service.render_resume_html") as mock_render,
            ):
                from app.services.pdf_export_service import _cache_path

                path = _cache_path(resume, "chromium")
                path.write_bytes(b"%PDF cached content")
                result = pdf_export_service.get_pdf_path(resume)

            self.assertEqual(result, path)
            mock_render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
