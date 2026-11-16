"""注册与重置密码端点。

这两个端点都有「查重/状态校验 → 锁 → 验证码 → 写库 → 清理」的固定顺序，且带
副作用（赠送积分、发验证码邮件）。这里固定：查重与状态分支的状态码、同名新密码
被拒、验证码失败不发生产库写入、注册赠送积分真的落一条流水、各锁必然释放。
"""
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers


class _AuthDb:
    """scalar 按预定序列返回：register 依次查用户名、邮箱；reset 只查一次。"""

    def __init__(self, scalar_results=None):
        self.scalar_results = list(scalar_results or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed = []

    def scalar(self, _stmt):
        return self.scalar_results.pop(0) if self.scalar_results else None

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 42
        # 真实 User 未 flush，column default 字段此时是 None；补上供 UserOut 校验。
        for field, value in (
            ("avatar_url", None),
            ("role", "user"),
            ("status", "active"),
            ("flow_points", Decimal("0.00")),
        ):
            if getattr(obj, field, None) is None:
                setattr(obj, field, value)
        self.refreshed.append(obj)


def _user(**overrides):
    base = {
        "id": 7,
        "username": "tester",
        "email": "user@example.com",
        "avatar_url": None,
        "flow_points": Decimal("0.00"),
        "password_hash": "old-hash",
        "status": "active",
        "role": "user",
        "register_ip": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_client(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _register_body(**overrides):
    base = {
        "username": "newuser",
        "email": "New@Example.com",
        "password": "secret123",
        "verification_code": "123456",
    }
    base.update(overrides)
    return base


class RegisterTest(unittest.TestCase):
    def _run(self, db, gift_points=0, code_ok=True):
        """统一 patch 掉 register 的外部依赖，返回 response。"""
        vcs = MagicMock()
        vcs.acquire_registration_lock.return_value = "lock-token"
        if not code_ok:
            from app.core.exceptions import AppException

            vcs.verify_registration_code.side_effect = AppException("验证码错误或已过期", 400)
        with (
            patch("app.api.auth.verification_code_service", vcs),
            patch("app.api.auth.get_client_ip", return_value="1.2.3.4"),
            patch("app.api.auth.get_password_hash", return_value="hashed-pw"),
            patch("app.api.auth.get_int_setting", return_value=gift_points),
            patch("app.api.auth.add_point_transaction") as add_points,
        ):
            resp = _make_client(db).post("/api/auth/register", json=_register_body())
        return resp, vcs, add_points

    def test_success_normalizes_email_and_records_ip(self):
        db = _AuthDb(scalar_results=[None, None])
        resp, vcs, add_points = self._run(db)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["email"], "new@example.com")  # 去空白 + 小写
        self.assertEqual(data["username"], "newuser")
        created = db.added[0]
        self.assertEqual(created.register_ip, "1.2.3.4")
        vcs.verify_registration_code.assert_called_once_with("new@example.com", "123456")
        vcs.invalidate_registration_codes.assert_called_once_with("new@example.com")
        add_points.assert_not_called()  # 未配置赠送时不加点

    def test_duplicate_username_and_email_are_409(self):
        for result in (_user(username="taken"), _user(email="taken@example.com")):
            with self.subTest(result=result.username):
                db = _AuthDb(scalar_results=[result])
                resp, _, _ = self._run(db)
                self.assertEqual(resp.status_code, 409)
                self.assertEqual(db.commits, 0)  # 查重失败不写库

    def test_bad_verification_code_is_400_and_no_user_written(self):
        db = _AuthDb(scalar_results=[None, None])
        resp, _, _ = self._run(db, code_ok=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(db.added), 0)
        self.assertEqual(db.commits, 0)

    def test_signup_gift_points_writes_a_transaction(self):
        db = _AuthDb(scalar_results=[None, None])
        resp, _, add_points = self._run(db, gift_points=50)
        self.assertEqual(resp.status_code, 200)
        add_points.assert_called_once()
        args = add_points.call_args.args
        self.assertEqual(args[2], "signup_gift")
        self.assertEqual(args[3], 50)
        self.assertEqual(db.commits, 2)  # 一次建用户，一次赠送积分

    def test_lock_is_released_even_on_failure(self):
        db = _AuthDb(scalar_results=[_user(username="taken")])
        resp, vcs, _ = self._run(db)
        self.assertEqual(resp.status_code, 409)
        vcs.release_registration_lock.assert_called_once_with("new@example.com", "lock-token")

    def test_password_over_72_bytes_is_422(self):
        # 25 个中文 = 75 字节（>72），虽然字符数在 6-100 内，schema 层就该拒。
        vcs = MagicMock()
        vcs.acquire_registration_lock.return_value = "t"
        with patch("app.api.auth.verification_code_service", vcs):
            resp = _make_client(_AuthDb()).post(
                "/api/auth/register",
                json=_register_body(password="中" * 25),
            )
        self.assertEqual(resp.status_code, 422)


class ResetPasswordTest(unittest.TestCase):
    def _run(self, user, code_ok=True, same_password=False):
        db = _AuthDb(scalar_results=[user])
        vcs = MagicMock()
        vcs.acquire_password_reset_lock.return_value = "reset-lock"
        if not code_ok:
            from app.core.exceptions import AppException

            vcs.verify_password_reset_code.side_effect = AppException("验证码错误或已过期", 400)
        with (
            patch("app.api.auth.verification_code_service", vcs),
            patch("app.api.auth.verify_password", return_value=same_password),
            patch("app.api.auth.get_password_hash", return_value="new-hash"),
        ):
            resp = _make_client(db).post(
                "/api/auth/reset-password",
                json={
                    "email": "User@Example.com",
                    "verification_code": "654321",
                    "new_password": "newsecret",
                },
            )
        return resp, db, vcs

    def test_success_updates_hash_and_clears_codes(self):
        user = _user()
        resp, db, vcs = self._run(user)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("已重置", resp.json()["message"])
        self.assertEqual(user.password_hash, "new-hash")
        self.assertEqual(db.commits, 1)
        vcs.verify_password_reset_code.assert_called_once_with("user@example.com", "654321")
        vcs.invalidate_password_reset_codes.assert_called_once_with("user@example.com")

    def test_new_password_same_as_old_is_400_without_db_write(self):
        resp, db, vcs = self._run(_user(), same_password=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不能与原密码相同", resp.json()["message"])
        self.assertEqual(db.commits, 0)
        vcs.verify_password_reset_code.assert_not_called()

    def test_unregistered_email_is_400(self):
        resp, _, _ = self._run(None)
        self.assertEqual(resp.status_code, 400)

    def test_disabled_account_is_403(self):
        resp, _, _ = self._run(_user(status="disabled"))
        self.assertEqual(resp.status_code, 403)

    def test_bad_code_is_400_and_hash_unchanged(self):
        user = _user()
        resp, db, _ = self._run(user, code_ok=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(user.password_hash, "old-hash")
        self.assertEqual(db.commits, 0)

    def test_lock_is_released_even_on_failure(self):
        _, _, vcs = self._run(None)
        vcs.release_password_reset_lock.assert_called_once_with("user@example.com", "reset-lock")


class PasswordResetCodeTest(unittest.TestCase):
    def test_unregistered_email_is_400_and_no_mail(self):
        db = _AuthDb(scalar_results=[None])
        with patch("app.api.auth.verification_code_service.issue_password_reset_code") as issue:
            resp = _make_client(db).post("/api/auth/password-reset-code", json={"email": "nobody@example.com"})
        self.assertEqual(resp.status_code, 400)
        issue.assert_not_called()

    def test_disabled_account_is_403(self):
        db = _AuthDb(scalar_results=[_user(status="disabled")])
        with patch("app.api.auth.verification_code_service.issue_password_reset_code") as issue:
            resp = _make_client(db).post("/api/auth/password-reset-code", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 403)
        issue.assert_not_called()

    def test_active_account_sends_code(self):
        db = _AuthDb(scalar_results=[_user()])
        with patch("app.api.auth.verification_code_service.issue_password_reset_code") as issue:
            resp = _make_client(db).post("/api/auth/password-reset-code", json={"email": "User@Example.com"})
        self.assertEqual(resp.status_code, 200)
        issue.assert_called_once()
        self.assertEqual(issue.call_args.args[0], "user@example.com")


if __name__ == "__main__":
    unittest.main()
