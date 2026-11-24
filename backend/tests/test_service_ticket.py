"""服务间回合票据：静态令牌证明「你是 Node」，票据证明「你在替谁办事」。

RF-008 的要害不是「令牌会不会泄漏」，而是**泄漏之后能干什么**：以前 `/service/*`
用请求体里的明文 `user_id` 自证，于是任何持令牌者都能读任意用户的信息库、给任意
用户记账。票据把「这一轮属于谁」签进请求，越权从「静默成功」变成 403。

这里固定四件事：票据本身（签发/验签/过期/篡改）、作用域比对（用户与简历）、
端点行为（缺票据 401、越权 403）、以及「有效期必须覆盖一个完整回合」这条配置约束。
"""

from __future__ import annotations

import unittest

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.ai import require_turn_ticket
from app.core import contracts
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.service_ticket import (
    TICKET_HEADER,
    TicketError,
    TurnTicket,
    issue_ticket,
    verify_ticket,
)
from tests.service_ticket_support import SERVICE_TOKEN, service_headers


class TicketPrimitivesTest(unittest.TestCase):
    def test_round_trip(self):
        raw = issue_ticket(user_id=7, resume_id=3, turn_id=55)
        ticket = verify_ticket(raw)
        self.assertEqual((ticket.user_id, ticket.resume_id, ticket.turn_id), (7, 3, 55))

    def test_turn_id_may_be_absent(self):
        """任务类回调没有「被回复的消息」，票据照样要能签。"""
        ticket = verify_ticket(issue_ticket(user_id=7, resume_id=3))
        self.assertIsNone(ticket.turn_id)

    def test_expired_ticket_is_rejected(self):
        raw = issue_ticket(user_id=7, resume_id=3, ttl_seconds=10, now=1_000)
        with self.assertRaises(TicketError) as excinfo:
            verify_ticket(raw, now=1_011)
        self.assertIn("过期", excinfo.exception.reason)

    def test_tampered_payload_is_rejected(self):
        raw = issue_ticket(user_id=7, resume_id=3, turn_id=55)
        parts = raw.split(".")
        parts[1] = "8"  # 把 user_id 改成别人，签名没跟着变
        with self.assertRaises(TicketError) as excinfo:
            verify_ticket(".".join(parts))
        self.assertIn("签名", excinfo.exception.reason)

    def test_tampered_signature_is_rejected(self):
        raw = issue_ticket(user_id=7, resume_id=3, turn_id=55)
        head, _, signature = raw.rpartition(".")
        flipped = signature[:-1] + ("A" if signature[-1] != "A" else "B")
        with self.assertRaises(TicketError):
            verify_ticket(f"{head}.{flipped}")

    def test_garbage_is_rejected_without_crashing(self):
        for raw in ("", "v1", "v1.7.3.55", "v2.7.3.55.9999999999.sig", "a.b.c.d.e.f"):
            with self.assertRaises(TicketError):
                verify_ticket(raw)

    def test_non_numeric_fields_are_rejected(self):
        raw = issue_ticket(user_id=7, resume_id=3)
        parts = raw.split(".")
        parts[1] = "seven"
        with self.assertRaises(TicketError):
            verify_ticket(".".join(parts))

    def test_scope_passes_for_matching_identity(self):
        ticket = verify_ticket(issue_ticket(user_id=7, resume_id=3))
        self.assertEqual(ticket.scope_problems(user_id=7, resume_id=3), "")

    def test_scope_rejects_other_user(self):
        ticket = verify_ticket(issue_ticket(user_id=7, resume_id=3))
        self.assertIn("票据属于用户 7", ticket.scope_problems(user_id=99))

    def test_scope_rejects_other_resume(self):
        ticket = verify_ticket(issue_ticket(user_id=7, resume_id=3))
        self.assertIn("简历", ticket.scope_problems(user_id=7, resume_id=4))

    def test_scope_allows_absent_resume(self):
        """不涉及简历的回调（例如信息库检索）不该被简历维度拦住。"""
        ticket = verify_ticket(issue_ticket(user_id=7, resume_id=3))
        self.assertEqual(ticket.scope_problems(user_id=7, resume_id=None), "")


class TicketTtlContractTest(unittest.TestCase):
    def test_ttl_must_cover_a_whole_turn(self):
        self.assertEqual(contracts.ticket_ttl_problems(600, 300_000), [])
        problems = contracts.ticket_ttl_problems(60, 300_000)
        self.assertEqual(len(problems), 1)
        self.assertIn("短于一个回合", problems[0])

    def test_default_ttl_is_valid(self):
        self.assertEqual(
            contracts.ticket_ttl_problems(contracts.SERVICE_TICKET_TTL_SECONDS, contracts.AI_TURN_TIMEOUT_MS),
            [],
        )


def _guard_app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/probe")
    def probe(ticket: TurnTicket = Depends(require_turn_ticket)):
        return {"user_id": ticket.user_id, "resume_id": ticket.resume_id, "turn_id": ticket.turn_id}

    return TestClient(app)


class ServiceCallbackGuardTest(unittest.TestCase):
    def setUp(self):
        self._original = settings.ai_service_token
        settings.ai_service_token = SERVICE_TOKEN
        self.client = _guard_app()

    def tearDown(self):
        settings.ai_service_token = self._original

    def _post(self, headers, body=None):
        return self.client.post("/probe", json=body or {"user_id": 7, "resume_id": 3}, headers=headers)

    def test_valid_ticket_passes(self):
        resp = self._post(service_headers(7, 3, turn_id=55))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["user_id"], 7)

    def test_missing_ticket_is_401(self):
        resp = self._post({"x-ai-service-token": SERVICE_TOKEN})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("回合票据", resp.json()["message"])

    def test_bad_service_token_is_401_even_with_valid_ticket(self):
        resp = self._post(service_headers(7, 3, token="wrong-token"))
        self.assertEqual(resp.status_code, 401)
        self.assertIn("服务令牌", resp.json()["message"])

    def test_expired_ticket_is_401(self):
        resp = self._post(service_headers(7, 3, ttl_seconds=-1))
        self.assertEqual(resp.status_code, 401)

    def test_ticket_for_another_user_is_403(self):
        """验收线：拿 A 的票据给 B 记账，必须 403（以前会成功）。"""
        resp = self._post(service_headers(7, 3), body={"user_id": 99, "resume_id": 3})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("越权", resp.json()["message"])

    def test_ticket_for_another_resume_is_403(self):
        resp = self._post(service_headers(7, 3), body={"user_id": 7, "resume_id": 4})
        self.assertEqual(resp.status_code, 403)

    def test_query_identity_mismatch_is_403(self):
        resp = self.client.post(
            "/probe?user_id=99",
            json={},
            headers=service_headers(7, 3),
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
