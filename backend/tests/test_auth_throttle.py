"""登录与验证码的失败限频。

两条链路都是"短凭据 + 无次数限制 = 可穷举"：登录密码可在线爆破，6 位验证码
在 10 分钟有效期内可以试完一百万次。这组测试固定限频的边界行为。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI

from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.services.verification_code_service import VerificationCodeService


class _Pipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    def delete(self, *keys):
        self.ops.append(("delete", keys))
        return self

    def set(self, key, value, ex=None):
        self.client.store[key] = value
        return self

    def sadd(self, key, member):
        self.ops.append(("sadd", key, member))
        return self

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "incr":
                results.append(self.client.incr(op[1]))
            elif op[0] == "expire":
                self.client.ttl[op[1]] = op[2]
                results.append(True)
            elif op[0] == "delete":
                self.client.delete(*op[1])
                results.append(1)
            elif op[0] == "sadd":
                self.ops_sadd(op[1], op[2])
                results.append(1)
        self.ops = []
        return results

    def ops_sadd(self, key, member):
        self.client.sets.setdefault(key, set()).add(member)


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}
        self.ttl = {}

    def get(self, key):
        return self.store.get(key)

    def incr(self, key):
        self.store[key] = int(self.store.get(key) or 0) + 1
        return self.store[key]

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.sets.pop(key, None)
            self.ttl.pop(key, None)

    def exists(self, key):
        return 1 if key in self.store else 0

    def smembers(self, key):
        return set(self.sets.get(key) or ())

    def ttl(self, key):  # pragma: no cover - 仅被提示文案使用
        return self._ttl.get(key, -1) if hasattr(self, "_ttl") else -1

    def pipeline(self, transaction=True):
        return _Pipeline(self)


class VerificationAttemptLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _FakeRedis()
        self.service = VerificationCodeService(self.client)
        self.email = "user@example.com"
        # 手动塞一条有效验证码，模拟「已发送」。
        code = "123456"
        self.client.store[self.service._code_key(self.email, self.service._code_id(self.email, code), "register")] = "1"
        self.code = code

    def test_wrong_codes_are_cut_off_after_the_limit(self) -> None:
        for _ in range(5):
            with self.assertRaises(Exception) as ctx:
                self.service.verify_registration_code(self.email, "000000")
            self.assertEqual(ctx.exception.code, 400)

        with self.assertRaises(Exception) as ctx:
            self.service.verify_registration_code(self.email, "000000")
        self.assertEqual(ctx.exception.code, 429, "超过上限后不再继续比对，直接作废")

        with self.assertRaises(Exception) as ctx:
            self.service.verify_registration_code(self.email, self.code)
        self.assertEqual(ctx.exception.code, 429, "正确验证码也已被作废，必须重新获取")

    def test_success_clears_the_counter(self) -> None:
        for _ in range(3):
            with self.assertRaises(Exception):
                self.service.verify_registration_code(self.email, "000000")

        self.service.verify_registration_code(self.email, self.code)

        self.assertEqual(self.service._verify_attempts(self.email, "register"), 0)
        # 计数器被清掉后，后续错误重新从 1 开始累计。
        with self.assertRaises(Exception):
            self.service.verify_registration_code(self.email, "000000")
        self.assertEqual(self.service._verify_attempts(self.email, "register"), 1)


class _DbStub:
    def __init__(self, user):
        self.user = user
        self.commits = 0

    def scalar(self, *_args):
        return self.user

    def add(self, _obj):
        pass

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        pass


class _StubUser(SimpleNamespace):
    """登录成功路径会把 user 序列化成 UserOut，字段得齐。"""

    def __init__(self, **overrides):
        super().__init__(
            **{
                "id": 7,
                "username": "tester",
                "email": "user@example.com",
                "avatar_url": None,
                "flow_points": 0.0,
                "password_hash": "x",
                "status": "active",
                "role": "user",
                "token_version": 0,
                **overrides,
            }
        )


def _login_client(user):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: _DbStub(user)
    return TestClient(app)


class LoginThrottleTest(unittest.TestCase):
    def test_locked_identity_is_rejected_before_password_check(self) -> None:
        client = _login_client(_StubUser())
        with (
            patch("app.api.auth.failure_count", return_value=5),
            patch("app.api.auth.verify_password") as verify,
        ):
            resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong"})

        self.assertEqual(resp.status_code, 429)
        verify.assert_not_called()
        self.assertIn("次数过多", resp.json()["message"])

    def test_bad_password_records_a_failure(self) -> None:
        client = _login_client(_StubUser())
        with (
            patch("app.api.auth.failure_count", return_value=0),
            patch("app.api.auth.verify_password", return_value=False),
            patch("app.api.auth.record_failure", return_value=1) as record,
        ):
            resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong"})

        self.assertEqual(resp.status_code, 400)
        # 两个维度都要记：邮箱+IP 与 per-IP。
        self.assertEqual(record.call_count, 2)
        self.assertEqual({call.args[0] for call in record.call_args_list}, {"login", "login_ip"})

    def test_per_ip_limit_blocks_rotating_emails(self) -> None:
        """换邮箱不能绕开限频：这是没加 per-IP 之前的核心问题。"""

        def counts(scope, _identity):
            return 0 if scope == "login" else settings.auth_login_ip_max_attempts

        client = _login_client(_StubUser())
        with (
            patch("app.api.auth.failure_count", side_effect=counts),
            patch("app.api.auth.verify_password") as verify,
        ):
            resp = client.post("/api/auth/login", json={"email": "another@example.com", "password": "wrong"})

        self.assertEqual(resp.status_code, 429)
        # 被 per-IP 拦下时不该再跑 bcrypt（这正是这条限频存在的目的）。
        verify.assert_not_called()
        self.assertIn("该网络", resp.json()["message"])

    def test_successful_login_clears_the_counter(self) -> None:
        client = _login_client(_StubUser())
        with (
            patch("app.api.auth.failure_count", return_value=2),
            patch("app.api.auth.verify_password", return_value=True),
            patch("app.api.auth.clear_throttle") as clear,
            patch("app.api.auth.create_access_token", return_value="token"),
        ):
            resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "right"})

        self.assertEqual(resp.status_code, 200)
        # 只清「邮箱+IP」；per-IP 计数保留，否则单账号即可洗白整台机器的额度。
        clear.assert_called_once()
        self.assertEqual(clear.call_args.args[0], "login")


if __name__ == "__main__":
    unittest.main()
