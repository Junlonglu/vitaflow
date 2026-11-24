"""客户端 IP 解析：代理头只在直连方是受信代理时才作数。

这组测试锁定的是「限频 / 审计 / 兑换码 IP 唯一性」的信任边界：客户端自带
``X-Forwarded-For`` 不得改变后端认定的来源 IP。
"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.core.request_utils import get_client_ip

TRUSTED_PEER = "172.18.0.5"  # Docker 私网，落在默认受信网段内
PUBLIC_PEER = "203.0.113.9"  # 公网直连


def _request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 40000) if peer else None,
    }
    return Request(scope)


class ClientIpTrustBoundaryTest(unittest.TestCase):
    def test_untrusted_peer_cannot_spoof_ip(self) -> None:
        request = _request(PUBLIC_PEER, {"X-Forwarded-For": "1.2.3.4"})

        self.assertEqual(get_client_ip(request), PUBLIC_PEER, "公网直连时忽略客户端自带的代理头")

    def test_untrusted_peer_cannot_spoof_via_other_headers(self) -> None:
        request = _request(
            PUBLIC_PEER,
            {"X-Real-IP": "1.2.3.4", "CF-Connecting-IP": "5.6.7.8"},
        )

        self.assertEqual(get_client_ip(request), PUBLIC_PEER)

    def test_trusted_proxy_forwarded_ip_is_honored(self) -> None:
        request = _request(TRUSTED_PEER, {"X-Forwarded-For": "1.2.3.4"})

        self.assertEqual(get_client_ip(request), "1.2.3.4")

    def test_trusted_proxy_takes_the_rightmost_untrusted_hop(self) -> None:
        # 攻击者在最左边塞了假地址，受信代理追加了真实来源。
        request = _request(TRUSTED_PEER, {"X-Forwarded-For": "9.9.9.9, 1.2.3.4"})

        self.assertEqual(get_client_ip(request), "1.2.3.4", "从右往左找第一个非受信地址")

    def test_all_trusted_hops_fall_back_to_the_peer(self) -> None:
        request = _request(TRUSTED_PEER, {"X-Forwarded-For": "10.0.0.7, 172.18.0.2"})

        self.assertEqual(get_client_ip(request), TRUSTED_PEER, "链上全是受信代理时退回 TCP 对端")

    def test_single_value_headers_are_used_when_no_forwarded_for(self) -> None:
        request = _request(TRUSTED_PEER, {"X-Real-IP": "1.2.3.4"})

        self.assertEqual(get_client_ip(request), "1.2.3.4")

    def test_missing_peer_returns_empty_string(self) -> None:
        self.assertEqual(get_client_ip(_request(None, {"X-Forwarded-For": "1.2.3.4"})), "")

    def test_ipv6_zone_id_and_port_suffix_do_not_break_parsing(self) -> None:
        request = _request("::1", {"X-Forwarded-For": "2001:db8::1"})

        self.assertEqual(get_client_ip(request), "2001:db8::1")

    def test_invalid_trusted_network_config_is_ignored(self) -> None:
        request = _request(TRUSTED_PEER, {"X-Forwarded-For": "1.2.3.4"})
        with patch("app.core.request_utils.settings") as settings:
            settings.trusted_proxy_networks = "not-a-network,172.18.0.0/16"

            self.assertEqual(get_client_ip(request), "1.2.3.4", "非法项跳过，合法项仍生效")


class _DbStub:
    def scalar(self, *_args):
        return None

    def add(self, _obj):
        pass

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _login_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: _DbStub()
    return TestClient(app)


class LoginThrottleIdentityTest(unittest.TestCase):
    """回归：伪造 XFF 不得把限频计数拆成多条，否则等于取消限频。"""

    def test_spoofed_forwarded_for_does_not_split_the_counter(self) -> None:
        client = _login_client()
        seen: dict[str, set[str]] = {}

        def record(scope, identity, _ttl):
            seen.setdefault(scope, set()).add(identity)
            return 1

        with (
            patch("app.api.auth.failure_count", return_value=0),
            patch("app.api.auth.record_failure", side_effect=record),
            patch("app.api.auth.verify_password", return_value=False),
        ):
            for spoofed in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
                resp = client.post(
                    "/api/auth/login",
                    json={"email": "user@example.com", "password": "wrong"},
                    headers={"X-Forwarded-For": spoofed},
                )
                self.assertEqual(resp.status_code, 400)

        # 两个维度（邮箱+IP、per-IP）各自只能有一条计数：伪造 XFF 既不拆开
        # 「邮箱+IP」，也不拆开 per-IP。
        self.assertEqual(set(seen), {"login", "login_ip"})
        for scope, identities in seen.items():
            self.assertEqual(len(identities), 1, f"{scope} 维度被拆开了：{identities}")


if __name__ == "__main__":
    unittest.main()
