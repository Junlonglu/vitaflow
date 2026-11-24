"""一次消息的附图总量预算（RF-044）。

单张 5MB × 9 张 = 45MB，base64 后约 60MB。不设总量时，Node 会在「用户消息
已落库」之后才 413 —— 于是留下一个永远等不到回复的回合，用户只看到消息发出
去了、没有回答。

预算必须在**落库之前**判，且判的是真实字节数（``uploaded_files.file_size``），
不是前端自报的数量。
"""
from __future__ import annotations

from unittest.mock import patch

from app.core import contracts
from app.services import ai_chat_service


def _attachments(count: int = 1) -> list[dict]:
    return [{"object_name": f"ai-chat/img-{i}.png"} for i in range(count)]


def test_over_budget_is_rejected_before_persisting():
    over = contracts.CHAT_ATTACHMENT_TOTAL_MAX_BYTES + 1
    with patch.object(ai_chat_service, "owned_upload_total_bytes", return_value=over):
        issue = ai_chat_service._attachment_budget_issue(None, 1, _attachments(2))

    assert issue is not None
    assert str(contracts.CHAT_ATTACHMENT_TOTAL_MAX_MB) in issue
    assert "分批" in issue


def test_exactly_at_budget_passes():
    """刚好顶到上限要放行：边界值不该因为一个 `>` 写成 `>=` 就被判非法。"""
    at_limit = contracts.CHAT_ATTACHMENT_TOTAL_MAX_BYTES
    with patch.object(ai_chat_service, "owned_upload_total_bytes", return_value=at_limit):
        assert ai_chat_service._attachment_budget_issue(None, 1, _attachments(9)) is None


def test_attachments_without_object_name_skip_the_query():
    """纯外链图片没有对象名，不计入预算，也不该为它查一次库。"""
    with patch.object(ai_chat_service, "owned_upload_total_bytes") as mock_total:
        issue = ai_chat_service._attachment_budget_issue(
            None, 1, [{"url": "https://example.com/a.png"}]
        )

    assert issue is None
    mock_total.assert_not_called()


def test_budget_uses_only_owned_bytes():
    """总字节数来自 storage 层按 (user_id, object_name) 求和，不是前端声明。"""
    with patch.object(ai_chat_service, "owned_upload_total_bytes", return_value=0) as mock_total:
        assert ai_chat_service._attachment_budget_issue(None, 1, _attachments(1)) is None

    _db, user_id, names = mock_total.call_args.args
    assert user_id == 1
    assert names == ["ai-chat/img-0.png"]
