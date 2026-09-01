"""Pure-unit tests for validate_basics_field_update, the seam the AI service's
update_basics_field tool calls back into.

This is the numeric anti-hallucination guard for field-level basics changes: a
quantified result the model proposes must already appear in the resume or the
conversation, otherwise the pending change is blocked. Pure/DB-free by design so
it runs despite the local venv being unable to load _sqlite3.
"""
import unittest

from app.services.ai_chat_service import validate_basics_field_update


CURRENT = {
    "basics": {
        "name": "张三",
        "title": "后端工程师",
        "location": "广州",
        "phone": "13800138000",
    },
    "summary": {"content": "五年前端经验"},
    "education": [{"school": "某大学"}],
    "skills": [{"name": "Python"}],
}


def _history():
    return [
        {"role": "user", "content": "帮我把地点从广州改成深圳"},
        {"role": "assistant", "content": "好的，我已准备好修改方案。"},
    ]


class ValidateBasicsFieldUpdateTest(unittest.TestCase):
    def test_valid_field_level_location_change(self):
        optimized, suggestions, issue = validate_basics_field_update(
            CURRENT, {"location": "深圳"}, _history(), "帮我把地点从广州改成深圳"
        )
        self.assertEqual(issue, "")
        self.assertIsNotNone(optimized)
        self.assertEqual(optimized["basics"]["location"], "深圳")
        # The rest of the basics section survives the field-level merge.
        self.assertEqual(optimized["basics"]["name"], "张三")
        self.assertEqual(optimized["basics"]["phone"], "13800138000")
        self.assertIn("所在城市", suggestions[0])
        self.assertIn("基本信息", suggestions[0])

    def test_hallucinated_quantified_result_is_blocked(self):
        # The model proposes a phone number that never appears in the resume or
        # conversation -> numeric anti-hallucination blocks the pending change.
        optimized, suggestions, issue = validate_basics_field_update(
            CURRENT, {"phone": "13999999999"}, _history(), "帮我把地点从广州改成深圳"
        )
        self.assertIsNone(optimized)
        self.assertEqual(suggestions, [])
        self.assertIn("量化", issue)

    def test_no_actual_change_is_reported(self):
        # Proposing the same value as the current basics produces no pending change.
        optimized, suggestions, issue = validate_basics_field_update(
            CURRENT, {"location": "广州"}, _history(), "保持地点不变"
        )
        self.assertIsNone(optimized)
        self.assertEqual(suggestions, [])
        self.assertIn("变化", issue)

    def test_empty_or_missing_basics_is_rejected(self):
        for basics in (None, {}, {"location": ""}):
            with self.subTest(basics=basics):
                optimized, suggestions, issue = validate_basics_field_update(CURRENT, basics, _history())
                self.assertIsNone(optimized)
                self.assertEqual(suggestions, [])
                self.assertTrue(issue)


if __name__ == "__main__":
    unittest.main()
