"""流式接口的会话生命周期。

FastAPI 在响应体开始发送之前就执行依赖清理，所以 ``Depends(get_db)`` 给的会话
在流真正跑起来时已经 close 了。旧实现把这个会话一路传进生成器，靠
``Session.close()`` 之后还能重新借用连接这一实现细节兜住——能跑，但事务边界和
连接占用都不可预期。

修复后会话由生成器自持（``db_factory`` 注入），本文件锁住三条契约：

1. 流正常结束 → 生成器自己开的会话被关闭；
2. 客户端中途断开（``aclose``）→ 同样关闭；
3. 一次性任务流中途断开 → 任务被判失败，会话关闭。
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import ai_chat_service
from app.services.ai_chat_service import stream_ai_service_chat, stream_ai_service_regenerate


def _drain(generator):
    async def _collect():
        return [event async for event in generator]

    return asyncio.run(_collect())


class _SessionStub:
    def __init__(self) -> None:
        self.closed = False
        self.committed = 0
        self.rolled_back = False

    def add(self, _obj):
        pass

    def commit(self):
        self.committed += 1

    def refresh(self, *_args, **_kwargs):
        pass

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def scalar(self, *_args, **_kwargs):
        return None

    def get(self, _model, _pk):
        return None


def _user():
    return SimpleNamespace(id=7)


def _resume():
    return SimpleNamespace(id=3)


async def _two_event_turn(*_args, **_kwargs):
    yield {"type": "delta", "text": "好的"}
    yield {"type": "result", "data": {}}


async def _endless_turn(*_args, **_kwargs):
    while True:
        yield {"type": "delta", "text": "..."}
        await asyncio.sleep(0)


class ChatStreamSessionLifecycleTest(unittest.TestCase):
    def test_closes_its_own_session_after_stream(self) -> None:
        db = _SessionStub()
        with patch.object(ai_chat_service, "_stream_chat_turn", side_effect=_two_event_turn):
            events = _drain(stream_ai_service_chat(_user(), _resume(), "你好", db_factory=lambda: db))

        self.assertEqual([event["type"] for event in events], ["delta", "result"])
        self.assertTrue(db.closed, "生成器必须关掉自己开的会话")

    def test_closes_session_when_client_disconnects(self) -> None:
        db = _SessionStub()

        async def run():
            gen = stream_ai_service_chat(_user(), _resume(), "你好", db_factory=lambda: db)
            with patch.object(ai_chat_service, "_stream_chat_turn", side_effect=_endless_turn):
                async for _event in gen:
                    break
            await gen.aclose()

        asyncio.run(run())
        self.assertTrue(db.closed, "客户端断开后生成器被 aclose，会话同样要关")

    def test_rolls_back_and_closes_when_turn_raises(self) -> None:
        db = _SessionStub()

        async def boom(*_args, **_kwargs):
            yield {"type": "delta", "text": "半截"}
            raise RuntimeError("上游断了")

        with patch.object(ai_chat_service, "_stream_chat_turn", side_effect=boom):
            with self.assertRaises(RuntimeError):
                _drain(stream_ai_service_chat(_user(), _resume(), "你好", db_factory=lambda: db))

        self.assertTrue(db.rolled_back, "异常路径要回滚未提交的写入")
        self.assertTrue(db.closed)

    def test_regenerate_owns_its_session_too(self) -> None:
        db = _SessionStub()
        with patch.object(ai_chat_service, "_regenerate_turn", side_effect=_two_event_turn):
            _drain(stream_ai_service_regenerate(_user(), _resume(), 10, db_factory=lambda: db))

        self.assertTrue(db.closed)


class TaskStreamSessionLifecycleTest(unittest.TestCase):
    def _drain_task_stream(self, events, finished):
        import json

        from app.api import ai as ai_api

        task = SimpleNamespace(id=1, points_used=0, tokens_used=0, input_data={})
        db = _SessionStub()
        db.get = lambda _model, _pk: task

        def fake_finish(_db, _task, status, error_message=None, output_data=None, token_usage=None):
            finished.append({"status": status, "error_message": error_message})

        async def run():
            with (
                patch.object(ai_api, "ai_service_task_events", return_value=events),
                patch.object(ai_api, "finish_ai_task", side_effect=fake_finish),
            ):
                response = ai_api._ai_service_task_stream_response(
                    1, "generate_resume", {}, db_factory=lambda: db
                )
                body = response.body_iterator
                async for _chunk in body:
                    break
                await body.aclose()

        asyncio.run(run())
        return db, finished

    def test_fails_task_and_closes_session_when_client_disconnects(self) -> None:
        async def endless():
            while True:
                yield {"type": "delta", "text": "..."}
                await asyncio.sleep(0)

        db, finished = self._drain_task_stream(endless(), [])
        self.assertTrue(db.closed)
        self.assertEqual([item["status"] for item in finished], ["failed"])
        self.assertIn("断开", finished[0]["error_message"])


if __name__ == "__main__":
    unittest.main()
