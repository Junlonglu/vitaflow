"""JSON 导出与回填。

导出是纯序列化（不渲染、不落盘），回填是「原样导入」（不走 AI、不扣积分）。
这里的断言固定三件事：快照包含还原所需的全部字段、不含会冲突或泄露的列、
导入侧对垃圾 JSON 一律报错而不是静默建空简历。
"""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exports import router as exports_router
from app.api.resumes import router as resumes_router
from app.core.database import get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.core.security import get_current_user
from app.services.resume_json_export import (
    EXPORT_FORMAT,
    build_export_payload,
    export_file_name,
    export_json_bytes,
    import_resume_from_json,
    parse_resume_export,
)


def _make_resume(**overrides):
    base = {
        "id": 7,
        "user_id": 1,
        "title": "张三的简历",
        "language": "zh-CN",
        "resume_data": {
            "schema_version": "1.1",
            "basics": {"name": "张三", "title": "后端工程师"},
            "summary": {"content": "熟悉 FastAPI"},
            "education": [],
            "skills": [],
            "work": [],
            "projects": [],
            "awards": [],
            "custom_sections": [],
            "layout": {"section_order": ["basics", "summary"]},
        },
        "template_id": "tech",
        "template_config": {"template_id": "tech", "theme_color": "#2563eb"},
        "share_token": "secret-token",
        "create_time": datetime.now(),
        "update_time": datetime.now(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_client(router):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


class ExportPayloadTest(unittest.TestCase):
    def test_payload_keeps_restore_fields_and_drops_identity(self):
        payload = build_export_payload(_make_resume())
        self.assertEqual(payload["format"], EXPORT_FORMAT)
        self.assertEqual(payload["schema_version"], "1.1")
        self.assertIn("exported_at", payload)
        resume = payload["resume"]
        self.assertEqual(resume["title"], "张三的简历")
        self.assertEqual(resume["template_id"], "tech")
        self.assertEqual(resume["template_config"]["theme_color"], "#2563eb")
        self.assertEqual(resume["resume_data"]["basics"]["name"], "张三")
        # id / user_id / share_token 导入时会冲突或泄露分享凭证，一个都不能出现。
        flattened = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret-token", flattened)
        self.assertNotIn('"user_id"', flattened)
        self.assertNotIn('"id": 7', flattened)

    def test_bytes_keep_chinese_readable(self):
        data = export_json_bytes(_make_resume())
        self.assertIn("张三".encode("utf-8"), data)
        self.assertIn(b"\n", data)

    def test_file_name_strips_illegal_chars(self):
        self.assertEqual(export_file_name(_make_resume(title='张/三:的"简历')), "张三的简历.json")
        self.assertEqual(export_file_name(_make_resume(title="   ")), "resume_7.json")


class ParseExportTest(unittest.TestCase):
    def test_roundtrip_wrapped_payload(self):
        wrapped = json.dumps(build_export_payload(_make_resume()), ensure_ascii=False).encode("utf-8")
        parsed = parse_resume_export(wrapped)
        self.assertEqual(parsed["title"], "张三的简历")
        self.assertEqual(parsed["template_id"], "tech")
        self.assertEqual(parsed["language"], "zh-CN")
        self.assertEqual(parsed["resume_data"]["basics"]["name"], "张三")

    def test_accepts_bare_resume_data(self):
        bare = json.dumps({"basics": {"name": "李四"}, "education": []}).encode("utf-8")
        parsed = parse_resume_export(bare, fallback_title="李四简历.json")
        self.assertEqual(parsed["fallback_title"], "李四简历")
        self.assertEqual(parsed["resume_data"]["basics"]["name"], "李四")

    def test_rejects_non_resume_json(self):
        for body in (b"[]", b'{"foo": 1}', b"\x00\x01binary"):
            with self.subTest(body=body):
                with self.assertRaises(AppException):
                    parse_resume_export(body)

    def test_rejects_empty_file(self):
        with self.assertRaises(AppException):
            parse_resume_export(b"")

    def test_reports_json_syntax_error_line(self):
        with self.assertRaises(AppException) as ctx:
            parse_resume_export(b'{\n  "basics": {,\n}')
        self.assertIn("第 2 行", ctx.exception.message)


class ExportRouteTest(unittest.TestCase):
    def test_export_json_returns_attachment(self):
        resume = _make_resume()
        with patch("app.api.exports.get_resume", return_value=resume):
            resp = _make_client(exports_router).post("/api/resumes/7/export/json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("application/json"))
        disposition = resp.headers["content-disposition"]
        self.assertIn("attachment;", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        body = json.loads(resp.content.decode("utf-8"))
        self.assertEqual(body["resume"]["template_id"], "tech")

    def test_export_json_records_export_row(self):
        resume = _make_resume()
        fake_db = MagicMock()
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(exports_router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        with patch("app.api.exports.get_resume", return_value=resume):
            resp = TestClient(app).post("/api/resumes/7/export/json")
        self.assertEqual(resp.status_code, 200)
        record = fake_db.add.call_args.args[0]
        self.assertEqual(record.file_type, "json")
        self.assertEqual(record.file_path, "inline:json")
        self.assertEqual(record.file_name, "张三的简历.json")

    def test_export_json_rejects_other_users_resume(self):
        with patch("app.api.exports.get_resume", side_effect=AppException("简历不存在", 404)):
            resp = _make_client(exports_router).post("/api/resumes/99/export/json")
        self.assertEqual(resp.status_code, 404)


class ImportRouteTest(unittest.TestCase):
    def test_json_upload_skips_ai_pipeline(self):
        created = _make_resume(id=21, title="张三的简历")
        with patch("app.api.resumes.import_resume_from_json", return_value=created) as mock_import, patch(
            "app.api.resumes.ai_service_task_result_sync"
        ) as mock_ai:
            resp = _make_client(resumes_router).post(
                "/api/resumes/import-file",
                files={"files": ("张三的简历.json", b'{"basics": {"name": "\\u5f20\\u4e09"}}', "application/json")},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["id"], 21)
        mock_import.assert_called_once()
        # 结构化回填不该触发大模型，也不该产生积分预检。
        mock_ai.assert_not_called()


class ImportServiceTest(unittest.TestCase):
    def _run(self, body, template_id=""):
        created = _make_resume(id=31)
        with patch("app.services.resume_json_export.create_resume", return_value=created) as mock_create, patch(
            "app.services.resume_json_export.get_template", return_value=None
        ):
            result = import_resume_from_json(MagicMock(), 1, body, "导入.json", template_id)
        return result, mock_create

    def test_unknown_template_falls_back_to_tech(self):
        body = json.dumps({"resume": {"title": "旧简历", "template_id": "removed_template", "resume_data": {"basics": {}}}}).encode()
        result, mock_create = self._run(body)
        self.assertIs(result.id, 31)
        payload = mock_create.call_args.args[2]
        self.assertEqual(payload.template_id, "tech")
        self.assertEqual(payload.template_config["template_id"], "tech")

    def test_title_falls_back_to_basics_name(self):
        body = json.dumps({"basics": {"name": "王五"}}, ensure_ascii=False).encode("utf-8")
        _, mock_create = self._run(body)
        self.assertEqual(mock_create.call_args.args[2].title, "王五的简历")

    def test_title_is_truncated_to_column_limit(self):
        body = json.dumps({"resume": {"title": "长" * 300, "resume_data": {"basics": {}}}}).encode()
        _, mock_create = self._run(body)
        self.assertEqual(len(mock_create.call_args.args[2].title), 100)


if __name__ == "__main__":
    unittest.main()
