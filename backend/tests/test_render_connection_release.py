"""渲染前必须先把数据库连接还回池里（RF-053）。

请求级 Session 的只读事务会一直持有连接直到请求结束，而 Chromium 首次渲染要
5–60s。15 个并发预览/导出就能把池占满，全站接口跟着排队——这不是「慢」，
是「整站不可用」。

这条不变量只能靠「render 被调用时事务已经结束」来断言：render 之后 rollback
也算通过的话，就完全测不出问题。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api import resumes as resumes_api
from app.services import pdf_export_service


class _RecordingDb:
    """只记录「滚回了没有」以及调用顺序。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    def rollback(self) -> None:
        self.events.append("rollback")

    def get(self, _model, _pk):
        return None

    def add(self, obj) -> None:
        self.events.append("add")

    def commit(self) -> None:
        self.events.append("commit")


def _resume() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        user_id=1,
        resume_data={"basics": {"name": "张三"}},
        template_id="tech",
        template_config={},
        language="zh-CN",
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=1)


def test_preview_pdf_rolls_back_before_rendering():
    db = _RecordingDb()
    seen: list[list[str]] = []

    def _fake_get_pdf_path(_source):
        # 这里就是「渲染入口」：此刻事务必须已经结束。
        seen.append(list(db.events))
        return Path("unused.pdf")

    with (
        patch.object(resumes_api, "get_resume", return_value=_resume()),
        patch.object(resumes_api, "get_pdf_path", side_effect=_fake_get_pdf_path),
    ):
        resumes_api.preview_pdf(7, db, _user())

    assert seen == [["rollback"]]


def test_preview_html_rolls_back_before_rendering():
    db = _RecordingDb()
    seen: list[list[str]] = []

    def _fake_render(*_args, **_kwargs):
        seen.append(list(db.events))
        return "<html></html>"

    with (
        patch.object(resumes_api, "get_resume", return_value=_resume()),
        patch.object(resumes_api, "render_resume_html", side_effect=_fake_render),
    ):
        resumes_api.preview_html(7, None, db, _user())

    assert seen == [["rollback"]]


def test_preview_html_by_data_rolls_back_before_rendering():
    db = _RecordingDb()
    seen: list[list[str]] = []

    def _fake_render(*_args, **_kwargs):
        seen.append(list(db.events))
        return "<html></html>"

    payload = SimpleNamespace(resume_data={"basics": {"name": "张三"}})
    with (
        patch.object(resumes_api, "get_resume", return_value=_resume()),
        patch.object(resumes_api, "render_resume_html", side_effect=_fake_render),
        patch.object(resumes_api, "normalize_resume_data", side_effect=lambda data, _ctx: data),
    ):
        resumes_api.preview_html_by_data(7, payload, db, _user())

    assert seen == [["rollback"]]


def test_export_pdf_rolls_back_before_rendering():
    """导出同样在请求里渲染，同样要先把连接还回去。"""
    db = _RecordingDb()
    seen: list[list[str]] = []

    def _fake_get_pdf_path(_source):
        seen.append(list(db.events))
        return Path("unused.pdf")

    with patch.object(pdf_export_service, "get_pdf_path", side_effect=_fake_get_pdf_path):
        pdf_export_service.export_pdf(db, 1, _resume())

    assert seen == [["rollback"]]
    # 渲染完之后才写流水：顺序反了就意味着用一条长事务包住了整个渲染。
    assert db.events == ["rollback", "add", "commit"]
