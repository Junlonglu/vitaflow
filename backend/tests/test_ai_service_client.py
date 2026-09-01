import asyncio
import json
import unittest

import httpx

from app.core.config import settings
from app.services.ai_service_client import ai_service_chat_events


def _ndjson_response(lines: list[str]) -> httpx.Response:
    body = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "application/x-ndjson"})


class AiServiceClientTest(unittest.TestCase):
    """Verify the FastAPI -> AI service proxy relays NDJSON events faithfully."""

    def setUp(self) -> None:
        self._token = settings.ai_service_token
        self._url = settings.ai_service_url
        settings.ai_service_token = "test-secret"
        settings.ai_service_url = "http://ai.example.com"

    def tearDown(self) -> None:
        settings.ai_service_token = self._token
        settings.ai_service_url = self._url

    def _collect(self, transport: httpx.AsyncBaseTransport, message: str = "你好") -> list[dict]:
        async def run() -> list[dict]:
            return [event async for event in ai_service_chat_events(message, transport=transport)]

        return asyncio.run(run())

    def test_relays_ndjson_events_and_forwards_auth_and_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/chat")
            self.assertEqual(request.headers["authorization"], "Bearer test-secret")
            payload = json.loads(request.content)
            self.assertEqual(payload["message"], "这份简历怎么样？")
            return _ndjson_response(
                [
                    '{"type":"start","data":{}}',
                    '{"type":"phase","phase":"replying","text":"正在组织回复"}',
                    '{"type":"delta","text":"我已收到你的提问"}',
                    '{"type":"result","data":{"assistant_message":{"id":1,"role":"assistant","content":"我已收到你的提问"}}}',
                ]
            )

        events = self._collect(httpx.MockTransport(handler), "这份简历怎么样？")

        self.assertEqual([e["type"] for e in events], ["start", "phase", "delta", "result"])
        self.assertEqual(events[2]["text"], "我已收到你的提问")
        self.assertEqual(events[3]["data"]["assistant_message"]["content"], "我已收到你的提问")

    def test_non_200_returns_error_with_detail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=json.dumps({"message": "AI 服务繁忙"}).encode("utf-8"))

        events = self._collect(httpx.MockTransport(handler))
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["message"], "AI 服务繁忙")

    def test_missing_token_yields_error(self) -> None:
        settings.ai_service_token = ""
        events = self._collect(httpx.MockTransport(lambda request: httpx.Response(200)))
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("令牌", events[0]["message"])

    def test_connection_failure_yields_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        events = self._collect(httpx.MockTransport(handler))
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("连接失败", events[0]["message"])


if __name__ == "__main__":
    unittest.main()
