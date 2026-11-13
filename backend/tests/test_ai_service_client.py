import asyncio
import base64
import json
import unittest
from unittest.mock import patch

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

    def _collect(
        self,
        transport: httpx.AsyncBaseTransport,
        message: str = "你好",
        context: dict | None = None,
        attachments: list[dict] | None = None,
        model_ref: str | None = None,
        model_override: dict | None = None,
        skills: list[dict] | None = None,
    ) -> list[dict]:
        async def run() -> list[dict]:
            return [
                event
                async for event in ai_service_chat_events(
                    message,
                    context=context,
                    transport=transport,
                    attachments=attachments,
                    model_ref=model_ref,
                    model_override=model_override,
                    skills=skills,
                )
            ]

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

    def test_forwards_context_in_payload(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        context = {
            "resume": {"id": 7, "title": "后端工程师简历", "target_position": "后端工程师"},
            "history": [{"role": "user", "content": "第一轮：帮我看看简历"}],
            "pending_change": {"exists": False, "message_id": None, "summary": []},
        }
        self._collect(httpx.MockTransport(handler), "第二轮：突出项目经历", context)

        self.assertEqual(captured["payload"]["message"], "第二轮：突出项目经历")
        self.assertEqual(captured["payload"]["context"], context)

    def test_forwards_attachments_as_base64_image_blocks(self) -> None:
        captured: dict = {}
        raw = b"\x89PNG\r\n\x1a\nfakepngbytes"  # a small stand-in image payload

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        attachments = [
            {
                "url": "http://127.0.0.1:9000/vitaflow/ai-chat/1.png",
                "name": "简历截图.png",
                "object_name": "ai-chat/1.png",
                "content_type": "image/png",
            }
        ]
        with patch("app.services.ai_service_client.read_uploaded_file", return_value=(raw, "image/png")):
            self._collect(httpx.MockTransport(handler), "帮我照图片改简历", attachments=attachments)

        images = captured["payload"]["images"]
        self.assertEqual(len(images), 1, "one image attachment becomes one ImageContent block")
        self.assertEqual(images[0]["type"], "image")
        self.assertEqual(images[0]["mimeType"], "image/png")
        self.assertEqual(images[0]["data"], base64.b64encode(raw).decode("ascii"))

    def test_omits_images_key_when_no_attachments(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        self._collect(httpx.MockTransport(handler), "这份简历怎么样？")
        self.assertNotIn("images", captured["payload"], "text-only requests never carry images")

    def test_skips_unresolvable_or_non_image_attachments(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        attachments = [
            # non-image content_type → dropped
            {"url": "http://x/notes.txt", "object_name": "ai-chat/1.txt", "content_type": "text/plain"},
            # image type but object_name missing → dropped (no bytes to read)
            {"url": "http://x/2.png", "content_type": "image/png"},
            # image type with object_name → kept, but read fails → dropped without raising
            {"url": "http://x/3.png", "object_name": "ai-chat/missing.png", "content_type": "image/png"},
        ]
        with patch("app.services.ai_service_client.read_uploaded_file", side_effect=OSError("no object")):
            self._collect(httpx.MockTransport(handler), "按图片改", attachments=attachments)
        self.assertNotIn("images", captured["payload"], "no resolvable image → no images key")

    def test_forwards_model_ref_as_model_field(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        self._collect(
            httpx.MockTransport(handler),
            "帮我把地点改成深圳",
            model_ref="deepseek/deepseek-v4-flash-vision-exp",
        )
        # The pi model ref routes the Node side to the mapped models.json entry
        # and keys the per-model usage report (ticket 07).
        self.assertEqual(captured["payload"]["model"], "deepseek/deepseek-v4-flash-vision-exp")

    def test_omits_model_field_when_no_model_ref(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        self._collect(httpx.MockTransport(handler), "这份简历怎么样？")
        self.assertNotIn("model", captured["payload"], "unmapped config → no model key (faux default)")

    def test_forwards_byok_override_and_enabled_skills(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        override = {"provider": "byok-7", "model": "my-model", "base_url": "https://api.example.com/v1", "api_key": "sk-secret"}
        skills = [{"name": "量化成果", "description": "", "instructions": "每条经历补一个数字"}]
        self._collect(
            httpx.MockTransport(handler),
            "帮我优化简历",
            model_ref="deepseek/deepseek-chat",
            model_override=override,
            skills=skills,
        )
        self.assertEqual(captured["payload"]["model_override"], override)
        self.assertEqual(captured["payload"]["skills"], skills)
        # 平台 model 仍保留，AI 服务用 override 覆盖它，便于回退与日志追踪。
        self.assertEqual(captured["payload"]["model"], "deepseek/deepseek-chat")

    def test_omits_byok_keys_when_not_configured(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        self._collect(httpx.MockTransport(handler), "这份简历怎么样？", model_override=None, skills=[])
        self.assertNotIn("model_override", captured["payload"])
        self.assertNotIn("skills", captured["payload"])

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
