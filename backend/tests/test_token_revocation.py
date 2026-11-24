"""登录态吊销：改密/重置密码之后，旧 token 必须立刻失效。

以前 JWT 只有 `sub` + `exp`，改密不改变任何东西——怀疑被盗后改密码，攻击者
手上的旧 token 仍然能用满 24 小时（RF-027）。这里固定三件事：

1. 签发时把 `users.token_version` 写进 `tv` claim；
2. 校验时版本号对不上就 401，且错误文案要说「密码已变更」而不是「登录已过期」
   ——前者告诉用户该重新登录，后者会让人以为是自己放太久没动；
3. 改密当下给当前会话补发一个 token，否则用户改完自己的密码就被踢出去。
"""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import router as auth_router
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, get_password_hash
from app.models.user import User


OLD_PASSWORD = "old-pass-123"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _user(db, **overrides):
    user = User(
        id=1,
        username="tester",
        email="tester@example.com",
        password_hash=get_password_hash(OLD_PASSWORD),
        role="user",
        status="active",
        flow_points=Decimal("0.00"),
        token_version=0,
        create_time=datetime.now(),
        update_time=datetime.now(),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    db.add(user)
    db.commit()
    return user


def _client(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _legacy_token(user_id: int) -> str:
    """模拟本次升级前签发、没有 `tv` claim 的 token。"""
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


class TokenRevocationTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        self.user = _user(self.db)
        self.client = _client(self.db)

    def tearDown(self):
        self.db.close()

    def test_login_token_carries_version(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"email": "tester@example.com", "password": OLD_PASSWORD},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        token = resp.json()["data"]["access_token"]
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        self.assertEqual(claims["tv"], 0)

    def test_legacy_token_without_version_still_works(self):
        """升级瞬间不能把所有人踢下线：没有 tv 的 token 按 0 处理。"""
        resp = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {_legacy_token(self.user.id)}"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_password_change_revokes_old_token(self):
        old_token = create_access_token(self.user.id, token_version=0)
        resp = self.client.put(
            "/api/auth/password",
            json={"current_password": OLD_PASSWORD, "new_password": "new-pass-456"},
            headers={"Authorization": f"Bearer {old_token}"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        new_token = resp.json()["data"]["access_token"]
        self.assertNotEqual(new_token, old_token)

        # 旧 token：401 且文案点明原因
        old_attempt = self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}
        )
        self.assertEqual(old_attempt.status_code, 401)
        self.assertIn("密码已变更", old_attempt.json()["message"])

        # 新 token：当前会话不被打断
        fresh = self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}
        )
        self.assertEqual(fresh.status_code, 200, fresh.text)

    def test_password_change_bumps_database_version(self):
        token = create_access_token(self.user.id, token_version=0)
        self.client.put(
            "/api/auth/password",
            json={"current_password": OLD_PASSWORD, "new_password": "new-pass-456"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.db.expire_all()
        self.assertEqual(self.db.get(User, self.user.id).token_version, 1)

    def test_reset_password_revokes_every_session(self):
        """重置密码可能是「被盗后找回」，要连当前会话一起踢。"""
        token = create_access_token(self.user.id, token_version=0)
        with patch("app.api.auth.verification_code_service") as service:
            service.acquire_password_reset_lock.return_value = "lock"
            resp = self.client.post(
                "/api/auth/reset-password",
                json={
                    "email": "tester@example.com",
                    "verification_code": "123456",
                    "new_password": "reset-pass-789",
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)

    def test_revocation_is_per_account_not_global(self):
        """A 改密只吊销 A 的旧 token，不能顺手把 B 也踢下线。"""
        other = _user(self.db, id=2, username="other", email="other@example.com")
        other_token = create_access_token(other.id, token_version=other.token_version)

        token = create_access_token(self.user.id, token_version=0)
        self.client.put(
            "/api/auth/password",
            json={"current_password": OLD_PASSWORD, "new_password": "new-pass-456"},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {other_token}"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["email"], "other@example.com")

    def test_token_with_wrong_version_is_rejected(self):
        """版本号是签发时的快照，伪造/过期版本一律对不上。"""
        token = create_access_token(self.user.id, token_version=99)
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("密码已变更", resp.json()["message"])

    def test_token_with_non_numeric_version_is_rejected(self):
        payload = {
            "sub": str(self.user.id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "tv": "not-a-number",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
