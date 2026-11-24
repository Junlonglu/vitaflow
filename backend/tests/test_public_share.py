"""公开分享链路：谁能读到、读到什么、以及匿名化是否真的生效。

分享是唯一不需要登录就能拿到简历内容的入口，两个风险点都没有测试覆盖：

1. 失效判断（关闭分享、过期、token 不存在）必须收敛成同一个 404，否则可以
   拿来枚举「这个 token 曾经存在过」；
2. 「隐藏敏感信息」是隐私边界——除了清空字段，还要把姓名换成「候选人」、
   关掉头像、清掉自定义字段，并且不能就地改到 ORM 对象上（同进程的其它请求
   会读到被脱敏后的数据）。
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.shares import router as shares_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.services import resume_share_service as svc


def _resume(**overrides):
    data = {
        "title": "我的简历",
        "share_token": "abc123",
        "share_enabled": True,
        "share_expire_time": None,
        "share_mask_sensitive": False,
        "language": "zh",
        "template_id": "blue_timeline",
        "template_config": {"show_avatar": True, "accent": "#123456"},
        "resume_data": {
            "basics": {
                "name": "张三",
                "phone": "13800000000",
                "email": "zhangsan@example.com",
                "custom_fields": [{"label": "个人网站", "value": "https://x.test"}],
                "job_intention": "后端工程师",
            },
            "education": [{"school": "某大学"}],
        },
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class _Db:
    def __init__(self, resume=None):
        self.resume = resume
        self.queries = 0

    def scalar(self, _stmt):
        self.queries += 1
        return self.resume


class SharedResumeLookupTest(unittest.TestCase):
    def test_valid_token_returns_resume(self):
        resume = _resume()
        self.assertIs(svc.get_shared_resume(_Db(resume), "abc123"), resume)

    def test_unknown_token_is_404(self):
        with self.assertRaises(Exception) as ctx:
            svc.get_shared_resume(_Db(None), "missing")
        self.assertEqual(ctx.exception.code, 404)

    def test_disabled_share_is_404(self):
        with self.assertRaises(Exception) as ctx:
            svc.get_shared_resume(_Db(_resume(share_enabled=False)), "abc123")
        self.assertEqual(ctx.exception.code, 404)

    def test_expired_share_is_404(self):
        expired = datetime.now() - timedelta(minutes=1)
        with self.assertRaises(Exception) as ctx:
            svc.get_shared_resume(_Db(_resume(share_expire_time=expired)), "abc123")
        self.assertEqual(ctx.exception.code, 404)

    def test_future_expiry_still_readable(self):
        later = datetime.now() + timedelta(days=1)
        resume = _resume(share_expire_time=later)
        self.assertIs(svc.get_shared_resume(_Db(resume), "abc123"), resume)

    def test_oversized_token_short_circuits_without_query(self):
        db = _Db(_resume())
        with self.assertRaises(Exception) as ctx:
            svc.get_shared_resume(db, "x" * 81)
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(db.queries, 0, "超长 token 不该打到数据库")

    def test_blank_token_is_404(self):
        with self.assertRaises(Exception) as ctx:
            svc.get_shared_resume(_Db(_resume()), "   ")
        self.assertEqual(ctx.exception.code, 404)

    def test_all_failures_share_one_message(self):
        # 文案一致，避免用不同提示反推出「token 存在但被关了」。
        messages = set()
        for db, token in (
            (_Db(None), "missing"),
            (_Db(_resume(share_enabled=False)), "abc123"),
            (_Db(_resume(share_expire_time=datetime.now() - timedelta(seconds=1))), "abc123"),
        ):
            with self.assertRaises(Exception) as ctx:
                svc.get_shared_resume(db, token)
            messages.add(ctx.exception.message)
        self.assertEqual(len(messages), 1, messages)


class MaskingTest(unittest.TestCase):
    def test_masking_clears_sensitive_basics(self):
        resume = _resume(share_mask_sensitive=True)
        data, config, title = svc.prepare_public_share_content(resume)

        basics = data["basics"]
        self.assertNotEqual(basics["name"], "张三")
        for field in ("phone", "email"):
            self.assertEqual(basics[field], "", field)
        self.assertEqual(basics["custom_fields"], [])
        self.assertEqual(config["show_avatar"], False)
        self.assertNotEqual(title, "我的简历")
        # 非敏感字段要保留，否则「匿名简历」就没有信息量了。
        self.assertEqual(basics["job_intention"], "后端工程师")
        self.assertEqual(data["education"], [{"school": "某大学"}])

    def test_masking_does_not_touch_the_orm_object(self):
        resume = _resume(share_mask_sensitive=True)
        svc.prepare_public_share_content(resume)
        self.assertEqual(resume.resume_data["basics"]["name"], "张三")
        self.assertEqual(resume.resume_data["basics"]["phone"], "13800000000")
        self.assertIs(resume.template_config["show_avatar"], True)

    def test_masking_keeps_other_template_config(self):
        resume = _resume(share_mask_sensitive=True)
        _, config, _ = svc.prepare_public_share_content(resume)
        self.assertEqual(config["accent"], "#123456")

    def test_unmasked_share_returns_original_payload(self):
        resume = _resume(share_mask_sensitive=False)
        data, config, title = svc.prepare_public_share_content(resume)
        self.assertEqual(data["basics"]["name"], "张三")
        self.assertEqual(data["basics"]["phone"], "13800000000")
        self.assertEqual(config["show_avatar"], True)
        self.assertEqual(title, "我的简历")

    def test_masking_handles_missing_basics(self):
        resume = _resume(share_mask_sensitive=True, resume_data={})
        data, _, _ = svc.prepare_public_share_content(resume)
        self.assertIsInstance(data.get("basics"), dict)
        self.assertEqual(data["basics"]["phone"], "")

    def test_masking_masks_contacts_written_into_free_text(self):
        """自我介绍里写「联系方式：138…」是常见写法，只清 basics 等于没脱敏。"""
        resume = _resume(
            share_mask_sensitive=True,
            resume_data={
                "basics": {"name": "张三", "phone": "13800000000", "email": "zhangsan@example.com"},
                # summary 是对象而不是字符串，这是简历数据的真实结构。
                "summary": {"content": "五年后端经验，联系方式 13800001111 或 lisi@example.com，也可打 010-88886666。"},
                "work": [
                    {
                        "company": "某公司",
                        "position": "后端工程师",
                        "description": "支撑 1200 万日活的网关，2020-2023 年在职，内推联系 13900002222。",
                        "highlights": ["把 P99 从 800ms 降到 120ms"],
                    }
                ],
                "custom_sections": [
                    {
                        "title": "实习经历",
                        "preset_type": "internships",
                        "items": [{"company": "某研究所", "description": "微信同手机 13700003333"}],
                    }
                ],
            },
        )
        data, _, _ = svc.prepare_public_share_content(resume)

        raw = json.dumps(data, ensure_ascii=False)
        for leaked in ("13800001111", "lisi@example.com", "010-88886666", "13900002222", "13700003333"):
            self.assertNotIn(leaked, raw, leaked)
        # 非联系方式的数字要留住，否则整份简历就没信息量了。
        self.assertIn("1200 万", data["work"][0]["description"])
        self.assertIn("2020-2023", data["work"][0]["description"])
        self.assertEqual(data["work"][0]["highlights"], ["把 P99 从 800ms 降到 120ms"])

    def test_masked_html_renders_without_contacts(self):
        """blue_timeline 是少数会渲染 summary / 经历描述的模板，按最终 HTML 断言一次。"""
        from app.services.preview_service import render_resume_html

        resume = _resume(
            share_mask_sensitive=True,
            template_id="blue_timeline",
            template_config={"show_avatar": True},
            resume_data={
                "basics": {"name": "张三"},
                "summary": {"content": "联系方式 13800001111，邮箱 lisi@example.com"},
                "work": [{"company": "某公司", "description": "内推 13900002222"}],
            },
        )
        data, config, _ = svc.prepare_public_share_content(resume)
        html = render_resume_html(data, resume.template_id, config, resume.language)

        for leaked in ("13800001111", "lisi@example.com", "13900002222"):
            self.assertNotIn(leaked, html, leaked)

    def test_unmasked_share_keeps_free_text_untouched(self):
        resume = _resume(
            share_mask_sensitive=False,
            resume_data={"basics": {}, "summary": {"content": "联系方式 13800001111"}},
        )
        data, _, _ = svc.prepare_public_share_content(resume)
        self.assertIn("13800001111", data["summary"]["content"])


class CustomTokenTest(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(svc.normalize_custom_share_token(_Db(), "  "))

    def test_rejects_illegal_patterns(self):
        # 3–7 位的短词可被字典枚举命中，所以下限是 8 位。
        for value in ("ab", "abcd", "a-b-c-d", "x" * 65, "-abc", "_abcdefg", "a b", "中文链接"):
            with self.subTest(value=value):
                with self.assertRaises(Exception) as ctx:
                    svc.normalize_custom_share_token(_Db(), value)
                self.assertEqual(ctx.exception.code, 400)

    def test_accepts_eight_characters(self):
        self.assertEqual(svc.normalize_custom_share_token(_Db(None), "cv-2026x"), "cv-2026x")

    def test_conflict_with_other_resume_is_409(self):
        with self.assertRaises(Exception) as ctx:
            svc.normalize_custom_share_token(_Db(_resume()), "mine-2026")
        self.assertEqual(ctx.exception.code, 409)

    def test_same_resume_may_keep_its_token(self):
        db = _Db(None)  # 排除自身后查不到冲突
        self.assertEqual(svc.normalize_custom_share_token(db, "mine-2026", resume_id=8), "mine-2026")


def _make_client(resume):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(shares_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: _Db(resume)
    return app


class ShareEndpointTest(unittest.TestCase):
    def test_returns_rendered_html_without_caching(self):
        resume = _resume(share_mask_sensitive=True)
        app = _make_client(resume)
        with patch("app.api.shares.render_resume_html", return_value="<html>ok</html>"):
            resp = TestClient(app).get("/api/shares/abc123")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("cache-control"), "no-store")
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["html"], "<html>ok</html>")
        self.assertNotEqual(body["data"]["title"], "我的简历")

    def test_missing_share_returns_404_envelope(self):
        app = _make_client(None)
        resp = TestClient(app).get("/api/shares/nope")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], 404)

    def test_rate_limited_lookups_return_429(self):
        # 公开端点是可枚举的：命中每 IP 上限后必须拒绝，且不再拿 token 去查库。
        db = _Db(_resume())
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(shares_router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db
        with patch("app.api.shares.hit", return_value=True):
            resp = TestClient(app).get("/api/shares/abc123")

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["code"], 429)
        self.assertEqual(db.queries, 0)


if __name__ == "__main__":
    unittest.main()
