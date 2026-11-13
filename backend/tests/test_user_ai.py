"""BYOK 凭证 / 用户技能：加密、SSRF 防护、service 规则与路由契约。

service 层跑真实 SQLite 内存库；路由层用 TestClient 断言「密钥绝不回传」、
跨用户隔离、文件导入与探活接线。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.user_ai import router as user_ai_router
from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.core.database import Base, get_db
from app.core.exceptions import register_exception_handlers
from app.core.security import get_current_user
from app.core.url_guard import literal_public_url
from app.models.user_ai import UserAiCredential, UserSkill
from app.schemas.user_ai import UserAiCredentialIn, UserSkillIn, UserSkillUpdateIn
from app.services import user_ai_service as svc

BASE_URL = "https://api.example.com/v1"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[UserAiCredential.__table__, UserSkill.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _credential(**kwargs):
    data = dict(
        provider_label="OpenAI 兼容",
        base_url=BASE_URL,
        model="gpt-4o-mini",
        api_key="sk-test-abcdefgh-1234",
        enabled=True,
    )
    data.update(kwargs)
    return UserAiCredentialIn(**data)


class CryptoTest(unittest.TestCase):
    def test_round_trip(self):
        token = encrypt_secret("sk-abcdefgh-1234")
        self.assertTrue(token.startswith("fernet:"))
        self.assertEqual(decrypt_secret(token), "sk-abcdefgh-1234")

    def test_empty_and_corrupt_return_blank(self):
        self.assertEqual(decrypt_secret(""), "")
        self.assertEqual(decrypt_secret("fernet:garbage"), "")

    def test_mask_keeps_only_head_and_tail(self):
        self.assertEqual(mask_secret("sk-abcdefgh-wxyz"), "sk-a…wxyz")
        self.assertEqual(mask_secret("short"), "s****")

    def test_ciphertext_never_contains_plaintext(self):
        self.assertNotIn("sk-test-abcdefgh-1234", encrypt_secret("sk-test-abcdefgh-1234"))


class UrlGuardTest(unittest.TestCase):
    def test_public_domain_allowed(self):
        self.assertEqual(literal_public_url(BASE_URL), (True, ""))

    def test_loopback_and_private_ips_rejected(self):
        for url in ("http://127.0.0.1:11434/v1", "http://localhost/v1", "http://10.0.0.5/v1", "http://169.254.169.254/latest"):
            allowed, _ = literal_public_url(url)
            self.assertFalse(allowed, url)

    def test_deployment_switch_allows_private(self):
        with patch("app.core.url_guard.settings") as fake:
            fake.byok_allow_private_network = True
            self.assertEqual(literal_public_url("http://127.0.0.1:11434/v1"), (True, ""))

    def test_bad_scheme_rejected(self):
        allowed, _ = literal_public_url("file:///etc/passwd")
        self.assertFalse(allowed)


class CredentialServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_first_save_requires_api_key(self):
        with self.assertRaises(Exception) as ctx:
            svc.upsert_credential(self.db, 1, _credential(api_key=None))
        self.assertIn("首次配置", str(ctx.exception))

    def test_upsert_keeps_one_row_and_encrypts(self):
        first = svc.upsert_credential(self.db, 1, _credential())
        self.assertEqual(first.api_key_masked, "sk-t…1234")
        self.assertNotIn("sk-test-abcdefgh-1234", first.api_key_encrypted)
        second = svc.upsert_credential(self.db, 1, _credential(model="gpt-4o", api_key=None))
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.api_key_masked, "sk-t…1234")
        self.assertEqual(decrypt_secret(second.api_key_encrypted), "sk-test-abcdefgh-1234")
        self.assertEqual(self.db.query(UserAiCredential).count(), 1)

    def test_override_resolves_only_when_enabled_and_decryptable(self):
        svc.upsert_credential(self.db, 1, _credential())
        override = svc.resolve_model_override(self.db, 1)
        self.assertEqual(override["api_key"], "sk-test-abcdefgh-1234")
        self.assertEqual(override["model"], "gpt-4o-mini")
        self.assertEqual(override["provider"], "byok-1")

        svc.upsert_credential(self.db, 1, _credential(enabled=False, api_key=None))
        self.assertIsNone(svc.resolve_model_override(self.db, 1))

        svc.upsert_credential(self.db, 1, _credential(enabled=True, api_key=None))
        cred = svc.get_credential(self.db, 1)
        cred.api_key_encrypted = "fernet:broken"
        self.db.commit()
        self.assertIsNone(svc.resolve_model_override(self.db, 1))

    def test_private_url_is_never_resolved(self):
        with patch("app.core.url_guard.settings") as fake:
            fake.byok_allow_private_network = True
            svc.upsert_credential(self.db, 1, _credential(base_url="http://127.0.0.1:11434/v1"))
        self.assertIsNone(svc.resolve_model_override(self.db, 1))

    def test_delete_and_user_isolation(self):
        svc.upsert_credential(self.db, 1, _credential())
        self.assertIsNone(svc.get_credential(self.db, 2))
        self.assertTrue(svc.delete_credential(self.db, 1))
        self.assertFalse(svc.delete_credential(self.db, 1))


class SkillServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_slug_is_unique_within_user_and_readable(self):
        a = svc.create_skill(self.db, 1, UserSkillIn(name="简历润色", instructions="用更正式的措辞"))
        b = svc.create_skill(self.db, 1, UserSkillIn(name="简历润色", instructions="另一版"))
        c = svc.create_skill(self.db, 2, UserSkillIn(name="简历润色", instructions="别人的"))
        self.assertNotEqual(a.slug, b.slug)
        self.assertEqual(b.slug, f"{a.slug}-2")
        self.assertEqual(a.slug, c.slug)  # 唯一约束按 (user_id, slug)，跨用户可同名同 slug
        self.assertTrue(a.slug.startswith("skill-"))  # 纯中文名走稳定哈希后缀

    def test_ascii_slug_and_toggle(self):
        skill = svc.create_skill(self.db, 1, UserSkillIn(name="Star Format", instructions="Use STAR"))
        self.assertEqual(skill.slug, "star-format")
        disabled = svc.update_skill(self.db, 1, skill.id, UserSkillUpdateIn(enabled=False))
        self.assertFalse(disabled.enabled)
        self.assertEqual(svc.build_skills_payload(self.db, 1), [])

    def test_cross_user_update_is_404(self):
        skill = svc.create_skill(self.db, 1, UserSkillIn(name="A", instructions="x"))
        with self.assertRaises(Exception) as ctx:
            svc.update_skill(self.db, 2, skill.id, UserSkillUpdateIn(enabled=False))
        self.assertIn("技能不存在", str(ctx.exception))

    def test_only_enabled_skills_reach_prompt_with_length_cap(self):
        svc.create_skill(self.db, 1, UserSkillIn(name="A", instructions="甲" * 6000, enabled=True))
        svc.create_skill(self.db, 1, UserSkillIn(name="B", instructions="乙" * 6000, enabled=True))
        svc.create_skill(self.db, 1, UserSkillIn(name="C", instructions="丙", enabled=False))
        payload = svc.build_skills_payload(self.db, 1)
        self.assertEqual(len(payload), 2)
        self.assertEqual(sum(len(item["instructions"]) for item in payload), svc.MAX_SKILL_PROMPT_CHARS)
        self.assertLessEqual(len(payload[1]["instructions"]), 2000)

    def test_parse_skill_file_frontmatter(self):
        content = "---\nname: 量化成果\ndescription: 把描述改成数字\nunknown: ignored\n---\n\n## 规则\n每条经历补一个数字。\n"
        payload = svc.parse_skill_file(content, "resume.md")
        self.assertEqual(payload.name, "量化成果")
        self.assertEqual(payload.description, "把描述改成数字")
        self.assertIn("每条经历补一个数字", payload.instructions)
        self.assertNotIn("---", payload.instructions)

    def test_parse_skill_file_uses_filename_and_rejects_empty(self):
        self.assertEqual(svc.parse_skill_file("直接写指令", "my-skill.md").name, "my-skill")
        with self.assertRaises(Exception):
            svc.parse_skill_file("", "x.md")
        with self.assertRaises(Exception):
            svc.parse_skill_file("---\nname: x\n---\n", "x.md")


class ProbeCredentialTest(unittest.IsolatedAsyncioTestCase):
    async def test_models_endpoint_success(self):
        import httpx

        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]}))
        with patch("app.services.user_ai_service.probe_public_url", return_value=(True, "")):
            ok, detail, models = await svc.probe_credential(BASE_URL, "sk-x", "gpt-4o-mini", transport=transport)
        self.assertTrue(ok)
        self.assertEqual(models, ["gpt-4o-mini"])

    async def test_401_is_translated(self):
        import httpx

        transport = httpx.MockTransport(lambda req: httpx.Response(401, text="nope"))
        with patch("app.services.user_ai_service.probe_public_url", return_value=(True, "")):
            ok, detail, _ = await svc.probe_credential(BASE_URL, "bad", "m", transport=transport)
        self.assertFalse(ok)
        self.assertIn("API Key 被拒绝", detail)

    async def test_blocked_url_never_sends_request(self):
        with patch("app.services.user_ai_service.probe_public_url", return_value=(False, "不允许接入内网")):
            ok, detail, _ = await svc.probe_credential("http://127.0.0.1/v1", "sk-x", "m")
        self.assertFalse(ok)
        self.assertIn("内网", detail)

    async def test_missing_models_falls_back_to_chat(self):
        import httpx

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/models"):
                return httpx.Response(404)
            return httpx.Response(200, json={"choices": []})

        with patch("app.services.user_ai_service.probe_public_url", return_value=(True, "")):
            ok, detail, _ = await svc.probe_credential(BASE_URL, "sk-x", "m", transport=httpx.MockTransport(handler))
        self.assertTrue(ok)
        self.assertIn("对话请求", detail)


def _client(db, user_id: int = 1):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(user_ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    return TestClient(app)


class ChatIntegrationGuardTest(unittest.TestCase):
    """可选增强不能拖垮主链路：回退、跳过预检、透传。"""

    def test_resolve_user_ai_falls_back_on_any_db_failure(self):
        from app.services import ai_chat_service

        broken = SimpleNamespace()
        with patch.object(ai_chat_service, "logger"):
            self.assertEqual(ai_chat_service._resolve_user_ai(broken, 1), (None, []))

    def test_resolve_user_ai_returns_override_and_skills(self):
        from app.services import ai_chat_service

        with (
            patch.object(ai_chat_service, "resolve_model_override", return_value={"model": "m"}) as override,
            patch.object(ai_chat_service, "build_skills_payload", return_value=[{"name": "s"}]),
        ):
            result = ai_chat_service._resolve_user_ai(SimpleNamespace(), 42)
        self.assertEqual(result, ({"model": "m"}, [{"name": "s"}]))
        self.assertEqual(override.call_args.args[1], 42)

    def test_byok_turn_skips_points_precheck(self):
        from app.services import ai_chat_service

        config = SimpleNamespace(chat_billing_override=lambda: {})
        with patch.object(ai_chat_service, "precheck_flow_points") as precheck:
            ai_chat_service._precheck_turn_points(SimpleNamespace(), SimpleNamespace(), config, byok_active=True)
            precheck.assert_not_called()
            ai_chat_service._precheck_turn_points(SimpleNamespace(), SimpleNamespace(), config)
            precheck.assert_called_once()


class UserAiEndpointTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_credential_response_never_leaks_plaintext(self):
        client = _client(self.db)
        resp = client.put("/api/user-ai/credential", json=_credential().model_dump())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["data"]
        self.assertNotIn("api_key", body)
        self.assertEqual(body["api_key_masked"], "sk-t…1234")
        self.assertTrue(body["has_api_key"])
        got = client.get("/api/user-ai/credential").json()["data"]
        self.assertNotIn("sk-test-abcdefgh-1234", str(got))

    def test_private_base_url_is_rejected(self):
        payload = _credential().model_dump()
        payload["base_url"] = "http://127.0.0.1:11434/v1"
        resp = _client(self.db).put("/api/user-ai/credential", json=payload)
        self.assertEqual(resp.status_code, 422)

    def test_test_endpoint_uses_stored_key_when_body_omits_it(self):
        client = _client(self.db)
        client.put("/api/user-ai/credential", json=_credential().model_dump())
        fake = AsyncMock(return_value=(True, "接入可用", ["gpt-4o-mini"]))
        with patch("app.api.user_ai.svc.probe_credential", fake):
            resp = client.post("/api/user-ai/credential/test", json={"base_url": BASE_URL, "model": "gpt-4o-mini"})
        self.assertEqual(resp.json()["data"]["ok"], True)
        self.assertEqual(fake.call_args.args[1], "sk-test-abcdefgh-1234")
        self.assertIsNotNone(svc.get_credential(self.db, 1).last_verified_time)

    def test_skill_import_and_toggle_and_user_isolation(self):
        client = _client(self.db)
        body = b"---\nname: \xe9\x9d\xa2\xe8\xaf\x95\xe6\xa8\xa1\xe5\xbc\x8f\ndescription: STAR\n---\n\xe7\x94\xa8 STAR \xe9\x87\x8d\xe5\x86\x99\n"
        resp = client.post("/api/user-ai/skills/import", files={"file": ("SKILL.md", body, "text/markdown")})
        self.assertEqual(resp.status_code, 200, resp.text)
        skill = resp.json()["data"]
        self.assertEqual(skill["source"], "upload")
        self.assertEqual(skill["name"], "面试模式")

        toggled = client.patch(f"/api/user-ai/skills/{skill['id']}", json={"enabled": False})
        self.assertFalse(toggled.json()["data"]["enabled"])
        listed = client.get("/api/user-ai/skills").json()["data"]
        self.assertEqual(listed["active"], 0)

        other = _client(self.db, user_id=2)
        self.assertEqual(other.get("/api/user-ai/skills").json()["data"]["total"], 0)
        self.assertEqual(other.patch(f"/api/user-ai/skills/{skill['id']}", json={"enabled": True}).json()["code"], 404)

    def test_bad_skill_upload_rejected(self):
        client = _client(self.db)
        resp = client.post("/api/user-ai/skills/import", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
        self.assertEqual(resp.json()["code"], 400)

    def test_delete_credential_when_absent(self):
        self.assertEqual(_client(self.db).delete("/api/user-ai/credential").json()["code"], 404)


if __name__ == "__main__":
    unittest.main()
