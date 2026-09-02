"""Tests for the one-shot task proxy (ticket 09): generate/import requests are
forwarded to the Node AI service's /v1/task and their NDJSON stream is relayed;
``ai_service_task_result`` aggregates the stream into (result, usage) and turns
error events into AppException so callers can settle the AiTask as failed.
"""
import asyncio
import json
import unittest

import httpx

from app.core.config import settings
from app.core.exceptions import AppException
from app.services.ai_service_client import ai_service_task_events, ai_service_task_result


def _ndjson_response(lines: list[str]) -> httpx.Response:
    body = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "application/x-ndjson"})


def _collect_events(transport: httpx.AsyncBaseTransport, task_type: str, payload: dict) -> list[dict]:
    async def run() -> list[dict]:
        return [event async for event in ai_service_task_events(task_type, payload, transport=transport)]

    return asyncio.run(run())


class AiServiceTaskEventsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._token = settings.ai_service_token
        self._url = settings.ai_service_url
        settings.ai_service_token = "test-secret"
        settings.ai_service_url = "http://ai.example.com"

    def tearDown(self) -> None:
        settings.ai_service_token = self._token
        settings.ai_service_url = self._url

    def test_forwards_task_input_and_model_ref(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["auth"] = request.headers["authorization"]
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        payload = {"target_position": "后端工程师", "personal_info": {"name": "张三"}}

        async def run() -> list[dict]:
            return [
                event
                async for event in ai_service_task_events(
                    "generate_resume", payload, model_ref="deepseek/deepseek-chat", transport=httpx.MockTransport(handler)
                )
            ]

        events = asyncio.run(run())

        self.assertEqual(captured["path"], "/v1/task")
        self.assertEqual(captured["auth"], "Bearer test-secret")
        self.assertEqual(captured["payload"]["task"], "generate_resume")
        self.assertEqual(captured["payload"]["input"], payload)
        self.assertEqual(captured["payload"]["model"], "deepseek/deepseek-chat")
        self.assertEqual(events[0]["type"], "start")

    def test_omits_model_field_without_model_ref(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return _ndjson_response(['{"type":"start","data":{}}'])

        _collect_events(httpx.MockTransport(handler), "import_resume", {"resume_text": "张三 后端"})
        self.assertNotIn("model", captured["payload"])

    def test_non_200_returns_error_with_detail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, content=json.dumps({"message": "未授权"}).encode("utf-8"))

        events = _collect_events(httpx.MockTransport(handler), "generate_resume", {})
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["message"], "未授权")

    def test_missing_token_yields_error(self) -> None:
        settings.ai_service_token = ""
        events = _collect_events(httpx.MockTransport(lambda request: httpx.Response(200)), "generate_resume", {})
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("令牌", events[0]["message"])


class AiServiceTaskResultTest(unittest.TestCase):
    def setUp(self) -> None:
        self._token = settings.ai_service_token
        self._url = settings.ai_service_url
        settings.ai_service_token = "test-secret"
        settings.ai_service_url = "http://ai.example.com"

    def tearDown(self) -> None:
        settings.ai_service_token = self._token
        settings.ai_service_url = self._url

    def test_returns_result_and_usage_from_result_event(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ndjson_response(
                [
                    '{"type":"start","data":{}}',
                    '{"type":"delta","text":"{\\"resume_data\\""}',
                    '{"type":"result","data":{"resume_data":{"basics":{}},"language":"zh-CN"},"usage":[{"input_tokens":120,"output_tokens":45}]}',
                ]
            )

        async def run():
            return await ai_service_task_result("generate_resume", {}, transport=httpx.MockTransport(handler))

        data, usage = asyncio.run(run())
        self.assertEqual(data["resume_data"], {"basics": {}})
        self.assertEqual(data["language"], "zh-CN")
        self.assertEqual(usage, [{"input_tokens": 120, "output_tokens": 45}])

    def test_error_event_raises_app_exception(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ndjson_response(['{"type":"error","message":"AI 返回的内容无法解析为合法 JSON，请重试"}'])

        async def run():
            return await ai_service_task_result("generate_resume", {}, transport=httpx.MockTransport(handler))

        with self.assertRaises(AppException) as ctx:
            asyncio.run(run())
        self.assertIn("JSON", str(ctx.exception))

    def test_missing_result_raises_app_exception(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ndjson_response(['{"type":"start","data":{}}'])

        async def run():
            return await ai_service_task_result("generate_resume", {}, transport=httpx.MockTransport(handler))

        with self.assertRaises(AppException) as ctx:
            asyncio.run(run())
        self.assertIn("未返回任务结果", str(ctx.exception))


class AiServiceTaskStreamResponseTest(unittest.TestCase):
    """The FastAPI relay around /v1/task: validates resume_data, settles the
    AiTask synchronously from the result event's usage, and refunds on failure."""

    def _drain(self, events, task=None):
        from types import SimpleNamespace
        from unittest.mock import patch

        from app.api.ai import _ai_service_task_stream_response

        db = SimpleNamespace(refresh=lambda *_a, **_k: None)
        task = task or SimpleNamespace(id=1, points_used=100, tokens_used=0, input_data={"request_input_tokens": 10})
        finished = []

        def fake_finish(db, task, status, error_message=None, output_data=None, token_usage=None):
            finished.append({"status": status, "error_message": error_message, "token_usage": token_usage})
            task.status = status

        async def _collect():
            with (
                patch("app.api.ai.ai_service_task_events", return_value=_aiter(events)),
                patch("app.api.ai.finish_ai_task", side_effect=fake_finish),
                patch("app.api.ai.normalize_resume_data", side_effect=lambda data, _payload: data),
            ):
                response = _ai_service_task_stream_response(
                    db, task, "generate_resume", {}, normalize_context={}
                )
                return [chunk async for chunk in response.body_iterator]

        import json as _json

        chunks = asyncio.run(_collect())
        return [_json.loads(chunk) for chunk in chunks], finished

    def test_success_settles_task_and_attaches_usage(self) -> None:
        events = [
            {"type": "start", "data": {}},
            {
                "type": "result",
                "data": {"resume_data": {"basics": {}}, "language": "zh-CN"},
                "usage": [{"input_tokens": 120, "output_tokens": 45}],
            },
        ]
        parsed, finished = self._drain(events)
        self.assertEqual(finished[0]["status"], "success")
        self.assertEqual(finished[0]["token_usage"]["input_tokens"], 120)
        self.assertEqual(finished[0]["token_usage"]["output_tokens"], 45)
        self.assertFalse(finished[0]["token_usage"]["estimated"], "provider-reported usage is not estimated")
        result = [event for event in parsed if event["type"] == "result"][0]
        self.assertEqual(result["data"]["resume_data"], {"basics": {}})
        self.assertIn("usage", result["data"], "flow-point usage echoed back to the frontend")

    def test_result_without_resume_data_fails_task(self) -> None:
        events = [{"type": "result", "data": {"explanation": "空的"}, "usage": []}]
        parsed, finished = self._drain(events)
        self.assertEqual(finished[0]["status"], "failed")
        self.assertIn("resume_data", finished[0]["error_message"])
        error = [event for event in parsed if event["type"] == "error"][0]
        self.assertIn("resume_data", error["message"])

    def test_error_event_fails_task_and_relays_message(self) -> None:
        events = [{"type": "error", "message": "AI 返回的内容无法解析为合法 JSON，请重试"}]
        parsed, finished = self._drain(events)
        self.assertEqual(finished[0]["status"], "failed")
        self.assertIn("JSON", finished[0]["error_message"])
        self.assertEqual(parsed[0]["type"], "error")


async def _aiter(items):
    for item in items:
        yield item


if __name__ == "__main__":
    unittest.main()
