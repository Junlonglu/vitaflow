"""BYOK 多模型凭证 / 推理强度 / 用户技能包：加密、SSRF、service 规则与路由契约。

service 层跑真实 SQLite 内存库；路由层用 TestClient 断言「密钥绝不回传」、
跨用户隔离、zip/文件夹导入与探活接线。
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.user_ai import router as user_ai_router
from app.core import crypto
from app.core.config import DEFAULT_JWT_SECRET_KEY
from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.core.database import Base, get_db
from app.core.exceptions import register_exception_handlers
from app.core.redact import SecretRedactingFilter, redact
from app.core.security import get_current_user
from app.core.url_guard import literal_public_url
from app.models.user_ai import UserAiAuditLog, UserAiCredential, UserSkill
from app.schemas.user_ai import UserAiCredentialIn, UserSkillIn, UserSkillUpdateIn
from app.services import user_ai_service as svc

BASE_URL = "https://api.example.com/v1"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[UserAiCredential.__table__, UserSkill.__table__, UserAiAuditLog.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _fernet_for(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def _jwt_legacy_token(jwt_secret: str, plain: str) -> str:
    """旧版 0036/0037 的密文：fernet:<token>，密钥由 JWT_SECRET_KEY 派生。"""
    return "fernet:" + _fernet_for(f"vitaflow-byok::{jwt_secret}").encrypt(plain.encode()).decode()


def _kid_token(kid: str, secret: str, plain: str) -> str:
    return f"fernet:{kid}:" + _fernet_for(f"vitaflow-byok::{kid}::{secret}").encrypt(plain.encode()).decode()


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


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


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


class CryptoRotationTest(unittest.TestCase):
    """独立密钥、多版本轮换、旧密文兼容与弱默认拒绝。"""

    def test_dedicated_key_writes_kid_and_reads_old_versions(self):
        with patch.object(crypto, "settings") as fake:
            fake.byok_encryption_keys = "v2=new-secret-aaaa,v1=old-secret-bbbb"
            fake.jwt_secret_key = "jwt-x"
            self.assertEqual(crypto.active_kid(), "v2")
            self.assertEqual(crypto.encryption_status()["mode"], "dedicated")
            self.assertEqual(crypto.validate_encryption_config(strict=True), "")

            token = crypto.encrypt_secret("sk-new-12345678")
            self.assertTrue(token.startswith("fernet:v2:"))
            self.assertEqual(crypto.decrypt_secret(token), "sk-new-12345678")
            self.assertFalse(crypto.needs_rotation(token))

            # 上一把口令、以及由 JWT 派生的旧格式，都还能解。
            self.assertEqual(
                crypto.decrypt_secret(_kid_token("v1", "old-secret-bbbb", "sk-old-87654321")),
                "sk-old-87654321",
            )
            self.assertEqual(
                crypto.decrypt_secret(_jwt_legacy_token("jwt-x", "sk-legacy-1111")),
                "sk-legacy-1111",
            )
            self.assertTrue(crypto.needs_rotation(_jwt_legacy_token("jwt-x", "x")))
            self.assertEqual(crypto.decrypt_secret("fernet:v9:garbage"), "")

    def test_weak_default_is_rejected_in_production(self):
        with patch.object(crypto, "settings") as fake:
            fake.byok_encryption_keys = ""
            fake.jwt_secret_key = DEFAULT_JWT_SECRET_KEY
            self.assertTrue(crypto.encryption_status()["weak_default"])
            with self.assertRaises(crypto.ByokKeyError):
                crypto.validate_encryption_config(strict=True)
            self.assertIn("BYOK_ENCRYPTION_KEYS", crypto.validate_encryption_config(strict=False))

    def test_non_default_jwt_is_warned_but_allowed(self):
        with patch.object(crypto, "settings") as fake:
            fake.byok_encryption_keys = ""
            fake.jwt_secret_key = "a-real-random-secret"
            self.assertIn("建议配置", crypto.validate_encryption_config(strict=True))

    def test_malformed_keyring_is_rejected(self):
        for value in ("no-equals", "legacy=x", "v1=x,v1=y", "=secret", "bad kid!=x"):
            with patch.object(crypto, "settings") as fake:
                fake.byok_encryption_keys = value
                fake.jwt_secret_key = "x"
                with self.assertRaises(crypto.ByokKeyError, msg=value):
                    crypto.active_kid()


class RedactTest(unittest.TestCase):
    def test_redacts_headers_json_and_query_params(self):
        key = "sk-abcdefghijklmnop"
        text = f'Authorization: Bearer {key} | {{"api_key": "{key}"}} | https://x/v1?key={key}'
        cleaned = redact(text)
        self.assertNotIn(key, cleaned)
        self.assertIn("[REDACTED]", cleaned)
        self.assertIn("https://x/v1", cleaned)

    def test_known_plaintext_is_removed_even_without_key_name(self):
        self.assertNotIn("hunter2-long-secret", redact("boom hunter2-long-secret", ["hunter2-long-secret"]))

    def test_logging_filter_scrubs_message_and_args(self):
        record = logging.LogRecord("t", logging.ERROR, "", 0, "api_key=%s", ("sk-abcdefghijkl",), None)
        self.assertTrue(SecretRedactingFilter().filter(record))
        self.assertNotIn("sk-abcdefghijkl", record.getMessage())

    def test_logging_filter_scrubs_exception_traceback(self):
        import sys

        try:
            raise RuntimeError("Bearer sk-traceback-abcdefgh")
        except RuntimeError:
            record = logging.LogRecord("t", logging.ERROR, "", 0, "failed", (), sys.exc_info())
        self.assertTrue(SecretRedactingFilter().filter(record))
        self.assertNotIn("sk-traceback-abcdefgh", record.exc_text or "")


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

    def test_create_requires_api_key_and_encrypts(self):
        with self.assertRaises(Exception) as ctx:
            svc.create_credential(self.db, 1, _credential(api_key=None))
        self.assertIn("API Key", str(ctx.exception))

        first = svc.create_credential(self.db, 1, _credential())
        self.assertEqual(first.api_key_masked, "sk-t…1234")
        self.assertNotIn("sk-test-abcdefgh-1234", first.api_key_encrypted)
        self.assertTrue(first.is_default)

    def test_update_keeps_stored_key_when_blank(self):
        first = svc.create_credential(self.db, 1, _credential())
        second = svc.update_credential(self.db, 1, first.id, _credential(model="gpt-4o", api_key=None))
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.api_key_masked, "sk-t…1234")
        self.assertEqual(decrypt_secret(second.api_key_encrypted), "sk-test-abcdefgh-1234")

    def test_multiple_models_with_one_default(self):
        a = svc.create_credential(self.db, 1, _credential())
        b = svc.create_credential(self.db, 1, _credential(provider_label="DeepSeek", model="deepseek-chat"))
        c = svc.create_credential(self.db, 1, _credential(provider_label="Qwen", model="qwen-plus", is_default=True))
        self.assertEqual(len(svc.list_credentials(self.db, 1)), 3)
        self.assertEqual(sum(1 for item in (a, b, c) if item.is_default), 1)
        self.assertEqual(svc.get_credential(self.db, 1).id, c.id)

        svc.set_default_credential(self.db, 1, a.id)
        rows = svc.list_credentials(self.db, 1)
        self.assertEqual([item.id for item in rows if item.is_default], [a.id])

    def test_unchecking_default_promotes_another_row(self):
        a = svc.create_credential(self.db, 1, _credential())
        b = svc.create_credential(self.db, 1, _credential(model="other"))
        updated = svc.update_credential(self.db, 1, a.id, _credential(model="gpt-4o-mini", api_key=None, is_default=False))
        self.assertFalse(updated.is_default)
        self.assertEqual(sum(1 for row in svc.list_credentials(self.db, 1) if row.is_default), 1)
        self.assertEqual(svc.get_credential(self.db, 1).id, b.id)

    def test_deleting_default_promotes_another_row(self):
        a = svc.create_credential(self.db, 1, _credential())
        b = svc.create_credential(self.db, 1, _credential(model="other"))
        svc.delete_credential(self.db, 1, a.id)
        rows = svc.list_credentials(self.db, 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_default)
        self.assertEqual(rows[0].id, b.id)

    def test_model_count_is_capped(self):
        from app.schemas.user_ai import MAX_MODELS_PER_USER

        for index in range(MAX_MODELS_PER_USER):
            svc.create_credential(self.db, 1, _credential(model=f"model-{index}"))
        with self.assertRaises(Exception) as ctx:
            svc.create_credential(self.db, 1, _credential(model="one-too-many"))
        self.assertIn("最多", str(ctx.exception))
        # 配额按用户计，别的用户不受影响
        svc.create_credential(self.db, 2, _credential())

    def test_reasoning_effort_reaches_override(self):
        row = svc.create_credential(self.db, 1, _credential(reasoning_effort="high"))
        override = svc.resolve_model_override(self.db, 1)
        self.assertEqual(override["reasoning_effort"], "high")
        self.assertEqual(override["credential_id"], row.id)
        self.assertEqual(svc.create_credential(self.db, 2, _credential()).reasoning_effort, "auto")

    def test_override_selects_explicit_model_and_platform_escape(self):
        a = svc.create_credential(self.db, 1, _credential())
        b = svc.create_credential(self.db, 1, _credential(model="deepseek-chat", base_url="https://api.deepseek.com/v1"))
        self.assertEqual(svc.resolve_model_override(self.db, 1, b.id)["model"], "deepseek-chat")
        self.assertIsNone(svc.resolve_model_override(self.db, 1, 0), "0 表示明确使用平台模型")
        self.assertIsNone(svc.resolve_model_override(self.db, 1, 9999), "跨用户/不存在的 id 不生效")
        self.assertEqual(svc.resolve_model_override(self.db, 1)["model"], "gpt-4o-mini")
        self.assertEqual(a.id, svc.get_credential(self.db, 1).id)

    def test_override_resolves_only_when_enabled_and_decryptable(self):
        svc.create_credential(self.db, 1, _credential())
        override = svc.resolve_model_override(self.db, 1)
        self.assertEqual(override["api_key"], "sk-test-abcdefgh-1234")
        self.assertEqual(override["provider"], "byok-1")

        row = svc.get_credential(self.db, 1)
        svc.update_credential(self.db, 1, row.id, _credential(enabled=False, api_key=None))
        self.assertIsNone(svc.resolve_model_override(self.db, 1))
        svc.update_credential(self.db, 1, row.id, _credential(enabled=True, api_key=None))
        row.api_key_encrypted = "fernet:broken"
        self.db.commit()
        self.assertIsNone(svc.resolve_model_override(self.db, 1))

    def test_private_url_is_never_resolved(self):
        with patch("app.core.url_guard.settings") as fake:
            fake.byok_allow_private_network = True
            svc.create_credential(self.db, 1, _credential(base_url="http://127.0.0.1:11434/v1"))
        self.assertIsNone(svc.resolve_model_override(self.db, 1))

    def test_legacy_upsert_and_user_isolation(self):
        first = svc.upsert_credential(self.db, 1, _credential())
        second = svc.upsert_credential(self.db, 1, _credential(model="gpt-4o", api_key=None))
        self.assertEqual(first.id, second.id)
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
        self.assertEqual(a.slug, c.slug)
        self.assertTrue(a.slug.startswith("skill-"))

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


class SkillPackageTest(unittest.TestCase):
    def test_zip_with_multiple_skill_folders_and_references(self):
        raw = _zip(
            {
                "quantify/SKILL.md": "---\nname: 量化成果\ndescription: 补数字\n---\n每条经历补一个可核验数字。".encode(),
                "quantify/references/examples.md": "例：把「负责增长」改成「DAU 提升 18%」".encode(),
                "star/SKILL.md": "用 STAR 重写经历".encode(),
                "notes.txt": "这个包被忽略（不属于任何技能目录）".encode(),
            }
        )
        files, skipped = svc.collect_zip_files(raw)
        self.assertEqual(skipped, [])
        payloads, warnings = svc.parse_skill_package(files)
        self.assertEqual(warnings, [])
        self.assertEqual(len(payloads), 2)
        quantify = next(item for item in payloads if item.name == "量化成果")
        self.assertEqual(quantify.description, "补数字")
        self.assertIn("## 参考资料", quantify.instructions)
        self.assertIn("DAU 提升 18%", quantify.instructions)
        self.assertEqual(next(item for item in payloads if item.name != "量化成果").name, "star")

    def test_flat_zip_with_single_markdown_is_accepted(self):
        raw = _zip({"SKILL.md": "直接写指令".encode()})
        files, _ = svc.collect_zip_files(raw)
        payloads, _ = svc.parse_skill_package(files)
        self.assertEqual(len(payloads), 1)

    def test_zip_without_manifest_and_multiple_files_is_rejected(self):
        raw = _zip({"a.md": "A".encode(), "b.md": "B".encode()})
        files, _ = svc.collect_zip_files(raw)
        with self.assertRaises(Exception) as ctx:
            svc.parse_skill_package(files)
        self.assertIn("SKILL.md", str(ctx.exception))

    def test_path_traversal_and_junk_are_dropped(self):
        raw = _zip(
            {
                "../evil/SKILL.md": "越界".encode(),
                "safe/SKILL.md": "正常".encode(),
                "__MACOSX/._SKILL.md": "垃圾".encode(),
                "safe/script.py": b"print('not executed')",
            }
        )
        files, _ = svc.collect_zip_files(raw)
        self.assertEqual([item.path for item in files], ["safe/SKILL.md"])
        payloads, _ = svc.parse_skill_package(files)
        self.assertEqual(payloads[0].instructions, "正常")

    def test_zip_bomb_is_rejected_before_extraction(self):
        raw = _zip({"big/SKILL.md": b"x" * (svc.MAX_PACKAGE_UNCOMPRESSED_BYTES + 1)})
        with self.assertRaises(Exception) as ctx:
            svc.collect_zip_files(raw)
        self.assertIn("过大", str(ctx.exception))

    def test_symlink_entry_is_skipped(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("safe/SKILL.md")
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, b"link")
            archive.writestr("real/SKILL.md", "真实内容".encode())
        files, skipped = svc.collect_zip_files(buffer.getvalue())
        self.assertEqual([item.path for item in files], ["real/SKILL.md"])
        self.assertTrue(any("符号链接" in item for item in skipped))

    def test_folder_entries_keep_relative_paths(self):
        entries = [
            ("my-skill/SKILL.md", "---\nname: 面试模式\n---\n用 STAR 回答".encode()),
            ("my-skill/references/tips.md", "补充材料".encode()),
        ]
        files, skipped = svc.collect_folder_files(entries)
        self.assertEqual(skipped, [])
        payloads, _ = svc.parse_skill_package(files)
        self.assertEqual(payloads[0].name, "面试模式")
        self.assertIn("补充材料", payloads[0].instructions)


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

    async def test_error_body_never_echoes_api_key(self):
        import httpx

        key = "sk-probe-secret-abcdefgh"

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/models"):
                return httpx.Response(404)
            return httpx.Response(400, text=f"bad request api_key={key} Authorization: Bearer {key}")

        with patch("app.services.user_ai_service.probe_public_url", return_value=(True, "")):
            ok, detail, _ = await svc.probe_credential(BASE_URL, key, "m", transport=httpx.MockTransport(handler))
        self.assertFalse(ok)
        self.assertNotIn(key, detail)

    async def test_unexpected_exception_is_sanitized(self):
        import httpx

        key = "sk-probe-secret-abcdefgh"

        def handler(_req: httpx.Request) -> httpx.Response:
            raise ValueError(f"leaked {key}")

        with (
            patch("app.services.user_ai_service.probe_public_url", return_value=(True, "")),
            patch("app.services.user_ai_service.logger"),
        ):
            ok, detail, _ = await svc.probe_credential(BASE_URL, key, "m", transport=httpx.MockTransport(handler))
        self.assertFalse(ok)
        self.assertNotIn(key, detail)


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

    def test_resolve_user_ai_forwards_selected_credential(self):
        from app.services import ai_chat_service

        with (
            patch.object(ai_chat_service, "resolve_model_override", return_value=None) as override,
            patch.object(ai_chat_service, "build_skills_payload", return_value=[]),
        ):
            ai_chat_service._resolve_user_ai(SimpleNamespace(), 42, 7)
        self.assertEqual(override.call_args.args[2], 7)

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

    def test_model_crud_and_never_leaks_plaintext(self):
        client = _client(self.db)
        resp = client.post("/api/user-ai/models", json=_credential(reasoning_effort="high").model_dump())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["data"]
        self.assertNotIn("api_key", body)
        self.assertEqual(body["api_key_masked"], "sk-t…1234")
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertTrue(body["is_default"])

        second = client.post(
            "/api/user-ai/models",
            json=_credential(provider_label="Qwen", model="qwen-plus", base_url="https://api.qwen.example/v1").model_dump(),
        ).json()["data"]
        listed = client.get("/api/user-ai/models").json()["data"]
        self.assertEqual(listed["total"], 2)
        self.assertEqual(listed["default_id"], body["id"])
        self.assertNotIn("sk-test-abcdefgh-1234", str(listed))

        defaulted = client.post(f"/api/user-ai/models/{second['id']}/default").json()["data"]
        self.assertEqual(defaulted["default_id"], second["id"])
        self.assertEqual(sum(1 for item in defaulted["items"] if item["is_default"]), 1)

        client.delete(f"/api/user-ai/models/{body['id']}")
        self.assertEqual(client.get("/api/user-ai/models").json()["data"]["total"], 1)

    def test_private_base_url_is_rejected(self):
        payload = _credential().model_dump()
        payload["base_url"] = "http://127.0.0.1:11434/v1"
        resp = _client(self.db).post("/api/user-ai/models", json=payload)
        self.assertEqual(resp.status_code, 422)

    def test_invalid_reasoning_effort_is_rejected(self):
        payload = _credential().model_dump()
        payload["reasoning_effort"] = "ultra"
        self.assertEqual(_client(self.db).post("/api/user-ai/models", json=payload).status_code, 422)

    def test_model_test_uses_stored_key_and_marks_verified(self):
        client = _client(self.db)
        created = client.post("/api/user-ai/models", json=_credential().model_dump()).json()["data"]
        fake = AsyncMock(return_value=(True, "接入可用", ["gpt-4o-mini"]))
        with patch("app.api.user_ai.svc.probe_credential", fake):
            resp = client.post(
                f"/api/user-ai/models/{created['id']}/test",
                json={"base_url": BASE_URL, "model": "gpt-4o-mini"},
            )
        self.assertEqual(resp.json()["data"]["ok"], True)
        self.assertEqual(fake.call_args.args[1], "sk-test-abcdefgh-1234")
        self.assertIsNotNone(svc.get_credential(self.db, 1).last_verified_time)

    def test_remote_model_list_uses_stored_key_and_returns_metadata(self):
        """拉列表只为「选一个」，密钥可以留空用已存的；资料字段原样带回。"""
        client = _client(self.db)
        created = client.post("/api/user-ai/models", json=_credential().model_dump()).json()["data"]
        upstream = {
            "models": [
                {
                    "id": "deepseek-flash",
                    "name": None,
                    "context_window": 1048576,
                    "max_tokens": 393216,
                    "input": ["text", "image"],
                }
            ]
        }
        with (
            patch("app.api.user_ai.probe_public_url", return_value=(True, "")),
            patch("app.api.user_ai.call_catalog", return_value=upstream) as catalog,
        ):
            resp = client.post(
                "/api/user-ai/models/remote-models",
                json={"base_url": BASE_URL, "credential_id": created["id"]},
            )
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["models"][0]["context_window"], 1048576)
        # 用的是解密后的已存密钥，且没有 provider_key：交给 ai-service 按域名猜 models.dev
        self.assertEqual(catalog.call_args.args[2]["api_key"], "sk-test-abcdefgh-1234")
        self.assertEqual(catalog.call_args.args[2]["provider_key"], "")
        self.assertEqual(catalog.call_args.args[1], "/v1/catalog/remote-models")

    def test_remote_model_list_requires_key_and_blocks_private_host(self):
        client = _client(self.db)
        # 地址校验在前（地址有问题时先修地址），这里把它放行才轮得到「缺密钥」
        with patch("app.api.user_ai.probe_public_url", return_value=(True, "")):
            resp = client.post("/api/user-ai/models/remote-models", json={"base_url": BASE_URL})
        self.assertEqual(resp.json()["code"], 400)
        self.assertIn("API Key", resp.json()["message"])

        # 字面内网地址在 schema 层就拦下了；解析到内网的域名由 probe_public_url 兜
        bad = client.post(
            "/api/user-ai/models/remote-models",
            json={"base_url": "http://127.0.0.1:11434/v1", "api_key": "sk-x"},
        )
        self.assertEqual(bad.status_code, 422)

        with patch("app.api.user_ai.probe_public_url", return_value=(False, "接入地址不能指向内网")):
            guarded = client.post(
                "/api/user-ai/models/remote-models",
                json={"base_url": BASE_URL, "api_key": "sk-x"},
            )
        self.assertEqual(guarded.json()["code"], 400)
        self.assertIn("内网", guarded.json()["message"])

    def test_legacy_credential_endpoint_still_works(self):
        client = _client(self.db)
        saved = client.put("/api/user-ai/credential", json=_credential().model_dump()).json()["data"]
        self.assertTrue(saved["is_default"])
        self.assertEqual(client.get("/api/user-ai/credential").json()["data"]["id"], saved["id"])
        self.assertEqual(client.delete("/api/user-ai/credential").json()["code"], 200)
        self.assertEqual(client.get("/api/user-ai/models").json()["data"]["total"], 0)

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

    def test_zip_package_creates_multiple_skills(self):
        raw = _zip(
            {
                "a/SKILL.md": "---\nname: 技能A\n---\n规则A".encode(),
                "b/SKILL.md": "规则B".encode(),
            }
        )
        resp = _client(self.db).post("/api/user-ai/skills/import", files={"file": ("skills.zip", raw, "application/zip")})
        self.assertEqual(resp.status_code, 200, resp.text)
        result = resp.json()["data"]
        self.assertEqual(result["total"], 2)
        self.assertEqual([item["source"] for item in result["items"]], ["package", "package"])
        self.assertEqual(_client(self.db).get("/api/user-ai/skills").json()["data"]["total"], 2)

    def test_folder_import_uses_import_package_route(self):
        client = _client(self.db)
        files = [
            ("files", ("pack/SKILL.md", b"---\nname: \xe6\x96\x87\xe4\xbb\xb6\xe5\xa4\xb9\xe6\x8a\x80\xe8\x83\xbd\n---\n\xe8\xa7\x84\xe5\x88\x99", "text/markdown")),
            ("files", ("pack/references/tips.md", "参考".encode(), "text/markdown")),
        ]
        resp = client.post("/api/user-ai/skills/import-package", files=files)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["total"], 1)
        skill = _client(self.db).get("/api/user-ai/skills").json()["data"]["items"][0]
        self.assertEqual(skill["name"], "文件夹技能")
        self.assertIn("参考", skill["instructions"])

    def test_bad_skill_upload_rejected(self):
        client = _client(self.db)
        resp = client.post("/api/user-ai/skills/import", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
        self.assertEqual(resp.json()["code"], 400)
        bad_zip = client.post("/api/user-ai/skills/import", files={"file": ("broken.zip", b"not a zip", "application/zip")})
        self.assertEqual(bad_zip.json()["code"], 400)

    def test_delete_absent_credential_and_cross_user_model(self):
        self.assertEqual(_client(self.db).delete("/api/user-ai/models/999").json()["code"], 404)
        created = _client(self.db).post("/api/user-ai/models", json=_credential().model_dump()).json()["data"]
        self.assertEqual(_client(self.db, user_id=2).delete(f"/api/user-ai/models/{created['id']}").json()["code"], 404)


class AuditAndClearTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_audit_is_user_scoped_and_never_stores_key(self):
        svc.record_audit(
            self.db, 1, "credential.create", credential_id=7,
            label="我的模型", detail="api_key=sk-abcdefghijklmnop",
        )
        svc.record_audit(self.db, 2, "chat.use", label="别人的模型")
        rows = svc.list_audit_logs(self.db, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, "credential.create")
        self.assertNotIn("sk-abcdefghijklmnop", rows[0].detail)
        self.assertEqual(svc.AUDIT_ACTION_LABELS["chat.use"], "对话使用该模型")

    def test_rotation_upgrades_legacy_ciphertext(self):
        with patch.object(crypto, "settings") as fake:
            fake.byok_encryption_keys = "v2=rotated-secret"
            fake.jwt_secret_key = "jwt-legacy"
            row = svc.create_credential(self.db, 1, _credential())
            row.api_key_encrypted = _jwt_legacy_token("jwt-legacy", "sk-abcdefgh-1234")
            self.db.commit()
            self.assertEqual(svc.rotate_legacy_ciphertexts(self.db, 1), 1)
            self.assertTrue(row.api_key_encrypted.startswith("fernet:v2:"))
            self.assertEqual(crypto.decrypt_secret(row.api_key_encrypted), "sk-abcdefgh-1234")
            self.assertEqual(svc.resolve_model_override(self.db, 1)["api_key"], "sk-abcdefgh-1234")

    def test_clear_all_only_removes_current_users_configs(self):
        svc.create_credential(self.db, 1, _credential())
        svc.create_credential(self.db, 1, _credential(model="second"))
        svc.create_credential(self.db, 2, _credential())
        self.assertEqual(svc.clear_all_credentials(self.db, 1), 2)
        self.assertEqual(len(svc.list_credentials(self.db, 1)), 0)
        self.assertEqual(len(svc.list_credentials(self.db, 2)), 1)


class SecurityEndpointTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_security_status_never_leaks_secrets(self):
        data = _client(self.db).get("/api/user-ai/security").json()["data"]
        self.assertIn(data["mode"], ("dedicated", "legacy"))
        self.assertTrue(data["clearable"])
        self.assertTrue(data["audited"])
        self.assertNotIn("rotated-secret", str(data))
        self.assertNotIn(DEFAULT_JWT_SECRET_KEY, str(data))

    def test_crud_is_audited_and_clear_removes_everything(self):
        client = _client(self.db)
        client.post("/api/user-ai/models", json=_credential().model_dump())
        logs = client.get("/api/user-ai/audit-logs").json()["data"]
        self.assertIn("credential.create", [item["action"] for item in logs["items"]])
        self.assertEqual(logs["items"][0]["action_label"], "添加模型配置")
        self.assertNotIn("sk-test-abcdefgh-1234", str(logs))

        cleared = client.delete("/api/user-ai/keys").json()["data"]
        self.assertEqual(cleared["removed"], 1)
        self.assertEqual(client.get("/api/user-ai/models").json()["data"]["total"], 0)
        actions = [item["action"] for item in client.get("/api/user-ai/audit-logs").json()["data"]["items"]]
        self.assertIn("keys.clear_all", actions)

    def test_audit_logs_are_not_visible_across_users(self):
        client = _client(self.db)
        client.post("/api/user-ai/models", json=_credential().model_dump())
        self.assertEqual(_client(self.db, user_id=2).get("/api/user-ai/audit-logs").json()["data"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
