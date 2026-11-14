"""原子信息库：schema 规范化、条目增改查、检索、导入简历、Agent 提案与路由契约。

service 层跑真实 SQLite 内存库；路由层用 TestClient 验证用户隔离与
「确认前不落库」的行为。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.ai import router as ai_router
from app.api.info_fragments import router as info_router
from app.core.database import Base, get_db
from app.core.exceptions import register_exception_handlers
from app.core.resume_schema import (
    SCHEMA_VERSION,
    guess_fragment_kind,
    normalize_fragment_payload,
    normalize_resume_document,
    schema_json,
    schema_prompt,
)
from app.core.security import get_current_user
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.info_fragment import InfoFragment
from app.models.resume import Resume
from app.services import info_fragment_service as svc


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(
        engine,
        tables=[InfoFragment.__table__, Resume.__table__, AiChatSession.__table__, AiChatMessage.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _resume(db, user_id: int = 1, resume_id: int = 1) -> Resume:
    resume = Resume(
        id=resume_id,
        user_id=user_id,
        title="测试简历",
        language="zh-CN",
        resume_data=normalize_resume_document(
            {
                "basics": {"name": "张三", "email": "a@b.com"},
                "work": [{"company": "某公司", "position": "实习生", "highlights": ["完成 A 功能"]}],
                "projects": [{"name": "简历系统", "role": "全栈"}],
                "custom_sections": [
                    {
                        "id": "custom_1",
                        "title": "证书/认证",
                        "preset_type": "certifications",
                        "items": [{"name": "PMP", "issuer": "PMI"}],
                    }
                ],
            }
        ),
        template_id="tech",
        template_config={"template_id": "tech"},
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


class ResumeSchemaTest(unittest.TestCase):
    def test_normalize_fills_version_ids_and_types(self):
        data = normalize_resume_document(
            {
                "basics": {"name": " 张三 "},
                "skills": [{"name": " 后端 ", "keywords": "Python\nFastAPI"}],
                "projects": [{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}],
            }
        )
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["basics"]["name"], "张三")
        self.assertEqual(data["skills"][0]["keywords"], ["Python", "FastAPI"])
        ids = [item["id"] for item in data["projects"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "dup")

    def test_normalize_keeps_unknown_fields(self):
        data = normalize_resume_document({"work": [{"company": "A", "legacy_field": "保留"}]})
        self.assertEqual(data["work"][0]["legacy_field"], "保留")

    def test_custom_section_without_id_gets_one_and_stays_ordered(self):
        data = normalize_resume_document(
            {"custom_sections": [{"title": "竞赛", "preset_type": "competitions", "items": [{"name": "ACM"}]}]}
        )
        section = data["custom_sections"][0]
        self.assertTrue(section["id"])
        self.assertIn(section["id"], data["layout"]["section_order"])

    def test_schema_description_is_consistent(self):
        prompt = schema_prompt()
        self.assertIn(SCHEMA_VERSION, prompt)
        for key in ("basics", "education", "work", "projects", "awards", "custom_sections"):
            self.assertIn(key, prompt)
        self.assertIn("fragment_kinds", schema_json())

    def test_fragment_payload_validation(self):
        cleaned, issue = normalize_fragment_payload("project", {"name": " 简历系统 ", "highlights": "做了 A\n做了 B"})
        self.assertEqual(issue, "")
        self.assertEqual(cleaned["name"], "简历系统")
        self.assertEqual(cleaned["highlights"], ["做了 A", "做了 B"])

        _, issue = normalize_fragment_payload("project", {"role": "全栈"})
        self.assertIn("缺少必填字段", issue)

        # 标题本身就是内容：只有名字也算一条完整信息
        cleaned, issue = normalize_fragment_payload("project", {"name": "只有名字"})
        self.assertEqual(issue, "")
        self.assertEqual(cleaned, {"name": "只有名字"})

        _, issue = normalize_fragment_payload("unknown", {"name": "x"})
        self.assertIn("不支持", issue)


class FragmentServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_create_list_and_counts(self):
        row, created = svc.create_fragment(
            self.db, 1, "project", {"name": "VitaFlow", "role": "全栈"}, tags=["简历", "AI"]
        )
        self.assertTrue(created)
        self.assertEqual(row.kind_label if hasattr(row, "kind_label") else "项目经历", "项目经历")
        svc.create_fragment(self.db, 1, "award", {"name": "一等奖"})
        svc.create_fragment(self.db, 2, "project", {"name": "别人的项目"})

        rows, total = svc.list_fragments(self.db, 1)
        self.assertEqual(total, 2)
        self.assertTrue(all(item.user_id == 1 for item in rows))

        counts = svc.counts_by_kind(self.db, 1)
        self.assertEqual(counts["project"], 1)
        self.assertEqual(counts["award"], 1)

        hits, _ = svc.list_fragments(self.db, 1, keyword="vita")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "VitaFlow")

        # LIKE 通配符必须转义，否则 % 会命中全部
        hits, total = svc.list_fragments(self.db, 1, keyword="%")
        self.assertEqual(total, 0)

    def test_dedupe_merges_instead_of_duplicating(self):
        first, created = svc.create_fragment(self.db, 1, "award", {"name": "一等奖", "date": "2024.05"})
        self.assertTrue(created)
        second, created_again = svc.create_fragment(
            self.db, 1, "award", {"name": "一等奖", "date": "2024.05"}, tags=["竞赛"]
        )
        self.assertFalse(created_again)
        self.assertEqual(first.id, second.id)
        self.assertEqual(svc.list_fragments(self.db, 1)[1], 1)
        self.assertIn("竞赛", second.tags)

    def test_update_and_delete_are_user_scoped(self):
        row, _ = svc.create_fragment(self.db, 1, "project", {"name": "旧项目", "role": "开发"})
        updated = svc.update_fragment(self.db, 1, row.id, payload={"name": "新项目", "role": "负责人"})
        self.assertEqual(updated.title, "新项目")
        with self.assertRaises(Exception):
            svc.update_fragment(self.db, 2, row.id, payload={"name": "越权"})
        with self.assertRaises(Exception):
            svc.delete_fragment(self.db, 2, row.id)
        svc.delete_fragment(self.db, 1, row.id)
        self.assertEqual(svc.list_fragments(self.db, 1)[1], 0)

    def test_extract_from_resume(self):
        resume = _resume(self.db)
        result = svc.extract_from_resume(self.db, 1, resume)
        self.assertGreaterEqual(result["created"], 4)  # basic + work + project + certificate
        kinds = {row.kind for row in svc.list_fragments(self.db, 1, page_size=50)[0]}
        self.assertTrue({"basic", "work", "project", "certificate"} <= kinds)

        # 再提取一次：同内容判重，不新增
        again = svc.extract_from_resume(self.db, 1, resume)
        self.assertEqual(again["created"], 0)
        self.assertEqual(svc.list_fragments(self.db, 1)[1], result["created"])

    def test_merge_fragments_into_resume_appends_and_skips_duplicates(self):
        resume = _resume(self.db)
        project, _ = svc.create_fragment(self.db, 1, "project", {"name": "新项目", "role": "后端"})
        work, _ = svc.create_fragment(self.db, 1, "work", {"company": "另一家公司", "position": "工程师"})
        internship, _ = svc.create_fragment(
            self.db, 1, "internship", {"company": "实习公司", "position": "实习生", "highlights": ["上线功能"]}
        )
        merged, added = svc.merge_fragments_into_resume(resume.resume_data, [project, work, internship])
        self.assertEqual(len(added), 3)
        self.assertEqual([item["name"] for item in merged["projects"]][-1], "新项目")
        self.assertEqual(merged["work"][-1]["company"], "另一家公司")
        internship_section = next(
            section for section in merged["custom_sections"] if section["preset_type"] == "internships"
        )
        self.assertEqual(internship_section["items"][0]["company"], "实习公司")
        self.assertTrue(internship_section["items"][0]["id"])

        # 重复导入同一批：不再新增条目
        merged_again, added_again = svc.merge_fragments_into_resume(merged, [project, work])
        self.assertEqual(added_again, [])
        self.assertEqual(len(merged_again["projects"]), len(merged["projects"]))

    def test_import_fragments_normalizes_and_rejects_empty(self):
        resume = _resume(self.db)
        row, _ = svc.create_fragment(self.db, 1, "award", {"name": "优秀员工", "date": "2025.01"})
        merged, added, rows = svc.import_fragments(self.db, 1, resume, [row.id])
        self.assertEqual(len(added), 1)
        self.assertEqual(merged["awards"][-1]["name"], "优秀员工")
        self.assertEqual(merged["schema_version"], SCHEMA_VERSION)
        self.assertEqual(rows[0].id, row.id)

        with self.assertRaises(Exception):
            svc.import_fragments(self.db, 1, resume, [])

    def test_import_reveals_a_hidden_target_section(self):
        resume = _resume(self.db)
        data = dict(resume.resume_data)
        data["layout"] = {**(data.get("layout") or {}), "hidden_sections": ["work", "projects"]}
        resume.resume_data = data

        work, _ = svc.create_fragment(self.db, 1, "work", {"company": "新公司", "position": "工程师"})
        merged, added, _rows = svc.import_fragments(self.db, 1, resume, [work.id])

        self.assertEqual(len(added), 1)
        self.assertEqual(merged["work"][-1]["company"], "新公司")
        hidden = merged["layout"]["hidden_sections"]
        self.assertNotIn("work", hidden, "导入的目标模块必须重新可见")
        self.assertIn("projects", hidden, "没导入的模块应保持用户原来的隐藏设置")

    def test_import_of_existing_fragment_is_not_an_error_but_still_reveals(self):
        resume = _resume(self.db)
        data = dict(resume.resume_data)
        data["layout"] = {**(data.get("layout") or {}), "hidden_sections": ["work"]}
        resume.resume_data = data
        existing, _ = svc.create_fragment(
            self.db, 1, "work", {"company": "某公司", "position": "实习生", "highlights": ["完成 A 功能"]}
        )

        # 空库里「已在简历中」过去直接抛 400，前端按失败处理且不跳转，
        # 用户就看不到那条被隐藏的内容。现在要返回可写回的文档 + 空 added。
        merged, added, _rows = svc.import_fragments(self.db, 1, resume, [existing.id])
        self.assertEqual(added, [])
        self.assertNotIn("work", merged["layout"]["hidden_sections"])

    def test_search_for_agent_ranks_and_falls_back(self):
        svc.create_fragment(self.db, 1, "project", {"name": "VitaFlow 简历系统", "description": "AI 简历"})
        svc.create_fragment(self.db, 1, "project", {"name": "日志平台", "description": "监控"})
        hits = svc.search_for_agent(self.db, 1, "简历")
        self.assertEqual([item.title for item in hits], ["VitaFlow 简历系统"])
        self.assertTrue(svc.search_for_agent(self.db, 1, ""))

    def test_validate_and_apply_proposals(self):
        existing, _ = svc.create_fragment(self.db, 1, "project", {"name": "旧项目", "role": "开发"})
        pending, issues = svc.validate_proposals(
            self.db,
            1,
            [
                {"kind": "project", "payload": {"name": "新项目", "role": "负责人"}},
                {"id": existing.id, "kind": "project", "payload": {"name": "旧项目改名", "role": "负责人"}},
                {"kind": "foo", "payload": {"name": "x"}},
                {"kind": "award", "payload": {"role": "缺名字"}},
                {"kind": "award", "payload": {"name": "重复项"}},
                {"kind": "award", "payload": {"name": "重复项"}},
            ],
        )
        self.assertEqual(len(pending), 3)  # 新增 + 更新 + 一个去重后的奖项
        self.assertEqual(len(issues), 2)

        result = svc.apply_proposals(self.db, 1, pending)
        self.assertEqual(result, {"created": 2, "updated": 1})
        titles = {row.title for row in svc.list_fragments(self.db, 1, page_size=50)[0]}
        self.assertIn("旧项目改名", titles)
        self.assertIn("新项目", titles)

    def test_proposal_cannot_touch_another_users_fragment(self):
        foreign, _ = svc.create_fragment(self.db, 2, "project", {"name": "别人的"})
        pending, issues = svc.validate_proposals(
            self.db, 1, [{"id": foreign.id, "kind": "project", "payload": {"name": "改名", "role": "x"}}]
        )
        self.assertEqual(pending, [])
        self.assertIn("不存在", issues[0])


class FragmentApiTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        self.user = SimpleNamespace(id=1, email="me@example.com", role="user")
        app = FastAPI()
        app.include_router(info_router, prefix="/api")
        app.include_router(ai_router, prefix="/api")
        register_exception_handlers(app)

        def override_db():
            yield self.db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def test_crud_and_isolation(self):
        created = self.client.post(
            "/api/info-fragments",
            json={"kind": "project", "payload": {"name": "VitaFlow", "role": "全栈"}, "tags": ["简历"]},
        ).json()
        self.assertEqual(created["code"], 200)
        fragment_id = created["data"]["id"]

        listed = self.client.get("/api/info-fragments").json()["data"]
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["counts"]["project"], 1)

        updated = self.client.put(
            f"/api/info-fragments/{fragment_id}",
            json={"payload": {"name": "VitaFlow 2", "role": "负责人"}},
        ).json()
        self.assertEqual(updated["data"]["title"], "VitaFlow 2")

        self.user = SimpleNamespace(id=2, email="other@example.com", role="user")
        self.assertEqual(self.client.put(f"/api/info-fragments/{fragment_id}", json={"title": "越权"}).status_code, 404)
        self.user = SimpleNamespace(id=1, email="me@example.com", role="user")
        self.assertEqual(self.client.delete(f"/api/info-fragments/{fragment_id}").json()["code"], 200)

    def test_invalid_payload_is_rejected(self):
        resp = self.client.post("/api/info-fragments", json={"kind": "project", "payload": {"role": "开发"}})
        self.assertNotEqual(resp.json()["code"], 200)
        self.assertIn("缺少必填字段", resp.json()["message"])

    def test_meta_lists_kinds(self):
        meta = self.client.get("/api/info-fragments/meta").json()["data"]
        kinds = {item["kind"] for item in meta["kinds"]}
        self.assertTrue({"basic", "project", "award", "certificate"} <= kinds)

    def test_extract_and_apply(self):
        resume = _resume(self.db)
        extracted = self.client.post("/api/info-fragments/extract", json={"resume_id": resume.id}).json()
        self.assertGreaterEqual(extracted["data"]["created"], 3)

        rows = self.client.get("/api/info-fragments?kind=project").json()["data"]["items"]
        self.assertTrue(rows)
        # 简历里已有同一条项目：不再当错误（否则用户以为导入失败），
        # 但也不能静默造重复条目；返回空 added 由前端给出「已存在」提示。
        duplicate = self.client.post(
            "/api/info-fragments/apply",
            json={"resume_id": resume.id, "fragment_ids": [rows[0]["id"]]},
        ).json()
        self.assertEqual(duplicate["code"], 200)
        self.assertEqual(duplicate["data"]["added"], [])
        self.assertIn("已在简历中", duplicate["message"])
        self.assertEqual(len(duplicate["data"]["resume_data"]["projects"]), 1)

        # 新增一条信息库条目后再导入：成功写入并返回最新简历数据
        fresh = self.client.post(
            "/api/info-fragments",
            json={"kind": "project", "payload": {"name": "新项目", "role": "负责人"}},
        ).json()["data"]
        applied = self.client.post(
            "/api/info-fragments/apply",
            json={"resume_id": resume.id, "fragment_ids": [fresh["id"]]},
        ).json()
        self.assertEqual(applied["code"], 200)
        self.assertEqual(applied["data"]["resume_data"]["projects"][-1]["name"], "新项目")
        self.assertEqual(self.client.post(
            "/api/info-fragments/apply", json={"resume_id": resume.id, "fragment_ids": []}
        ).json()["code"], 400)

    def test_service_endpoints_require_token(self):
        with patch("app.api.ai.settings") as settings_mock:
            settings_mock.ai_service_token = "test-token"
            self.assertEqual(self.client.get("/api/ai/service/tool/info-fragments/search?user_id=1").status_code, 401)
            ok = self.client.get(
                "/api/ai/service/tool/info-fragments/search?user_id=1&query=项目",
                headers={"X-AI-Service-Token": "test-token"},
            ).json()
            self.assertEqual(ok["data"]["ok"], True)

            proposed = self.client.post(
                "/api/ai/service/tool/info-fragments/propose",
                headers={"X-AI-Service-Token": "test-token"},
                json={
                    "user_id": 1,
                    "fragments": [
                        {"kind": "project", "payload": {"name": "新项目", "role": "负责人"}},
                        {"kind": "award", "payload": {}},
                    ],
                },
            ).json()
            self.assertEqual(proposed["data"]["ok"], True)
            self.assertEqual(len(proposed["data"]["pending_info_fragments"]), 1)
            self.assertTrue(proposed["data"]["issues"])
            # 提案只返回待确认，不落库
            self.assertEqual(svc.list_fragments(self.db, 1)[1], 0)

    def test_service_import_returns_pending_change_without_persisting(self):
        resume = _resume(self.db)
        row, _ = svc.create_fragment(self.db, 1, "award", {"name": "优秀员工"})
        with patch("app.api.ai.settings") as settings_mock:
            settings_mock.ai_service_token = "test-token"
            payload = self.client.post(
                "/api/ai/service/tool/info-fragments/import",
                headers={"X-AI-Service-Token": "test-token"},
                json={"user_id": 1, "resume_id": resume.id, "fragment_ids": [row.id]},
            ).json()["data"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["optimized_resume_data"]["awards"][-1]["name"], "优秀员工")
        self.db.refresh(resume)
        self.assertFalse(resume.resume_data.get("awards"))  # 未确认前不写简历


class PendingFragmentDecisionTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        self.user = SimpleNamespace(id=1, email="me@example.com", role="user")
        self.resume = _resume(self.db)

    def tearDown(self):
        self.db.close()

    def test_apply_creates_fragments_and_marks_applied(self):
        from app.services.ai_chat_service import resolve_chat_change

        session = AiChatSession(id=1, user_id=1, resume_id=self.resume.id, title="t")
        self.db.add(session)
        self.db.commit()
        message = AiChatMessage(
            id=11,
            session_id=session.id,
            user_id=1,
            resume_id=self.resume.id,
            role="assistant",
            content="已整理",
            suggestions=["项目经历：新项目"],
            pending_info_fragments=[
                {"id": None, "kind": "project", "title": "新项目", "payload": {"name": "新项目", "role": "负责人"}}
            ],
            action_status="pending",
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)

        updated, resume_data = resolve_chat_change(self.db, self.user, self.resume, message.id, "apply")
        self.assertEqual(updated.action_status, "applied")
        self.assertIsNone(resume_data)
        self.assertEqual(svc.list_fragments(self.db, 1)[1], 1)

    def test_reject_discards_proposals(self):
        from app.services.ai_chat_service import resolve_chat_change

        session = AiChatSession(id=1, user_id=1, resume_id=self.resume.id, title="t")
        self.db.add(session)
        self.db.commit()
        message = AiChatMessage(
            id=12,
            session_id=session.id,
            user_id=1,
            resume_id=self.resume.id,
            role="assistant",
            content="已整理",
            suggestions=[],
            pending_info_fragments=[{"id": None, "kind": "award", "title": "奖", "payload": {"name": "奖"}}],
            action_status="pending",
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)

        updated, _ = resolve_chat_change(self.db, self.user, self.resume, message.id, "reject")
        self.assertEqual(updated.action_status, "rejected")
        self.assertEqual(svc.list_fragments(self.db, 1)[1], 0)


if __name__ == "__main__":
    unittest.main()


class FragmentKindGuessTest(unittest.TestCase):
    """分类由字段决定：模型只写内容，不再需要记住 15 个分类名与各自字段。"""

    def test_guesses_from_shape(self):
        cases = [
            ({"school": "某大学", "major": "计算机", "degree": "本科"}, "education"),
            ({"company": "A 公司", "position": "后端"}, "work"),
            ({"company": "A 公司", "position": "后端实习生"}, "internship"),
            ({"name": "VitaFlow", "tech_stack": "Vue"}, "project"),
            ({"name": "VitaFlow", "role": "负责人"}, "project"),
            ({"keywords": ["Python", "FastAPI"]}, "skill"),
            ({"name": "CET-6", "issuer": "教育部"}, "certificate"),
            ({"organization": "校学生会", "role": "部长"}, "campus"),
            ({"name": "一等奖", "date": "2024.05"}, "award"),
            ({"name": "PMP", "institution": "PMI"}, "training"),
            ({"name": "英语", "level": "CET-6"}, "language"),
            ({"phone": "13800138000", "email": "a@b.com"}, "basic"),
            ({"foo": "bar"}, "custom"),
            ({}, "custom"),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(guess_fragment_kind(payload), expected)

    def test_proposal_without_kind_is_accepted_and_labelled(self):
        db = _session()
        try:
            pending, issues = svc.validate_proposals(
                db,
                1,
                [{"payload": {"school": "某大学", "major": "计算机", "degree": "本科"}}],
            )
            self.assertEqual(issues, [])
            self.assertEqual(pending[0]["kind"], "education")
            self.assertEqual(pending[0]["kind_label"], "教育经历")
            self.assertTrue(pending[0]["title"])
        finally:
            db.close()
