"""Tests for the admin dashboard's tool-trace quality aggregation (工具调用质量卡片).

`_tool_trace_breakdown` consumes the raw `tool_trace` JSON rows stored on
assistant chat messages (curated steps with status/ok/category/duration_ms —
see the 统一错误信封 work in ai-service) and produces the per-category failure
and duration stats the admin dashboard renders. Pure function, no DB.
"""
import unittest

from app.api.admin import _tool_trace_breakdown


def _step(status="end", ok=True, category=None, duration=None):
    step = {"id": "tc1", "name": "get_resume_context", "label": "查阅简历", "status": status, "ok": ok}
    if category is not None:
        step["category"] = category
    if duration is not None:
        step["duration_ms"] = duration
    return step


class ToolTraceBreakdownTest(unittest.TestCase):
    def test_empty_rows_yield_zeroed_breakdown(self):
        for empty in ([], [[]], [[{"status": "start"}]]):
            with self.subTest(empty=empty):
                breakdown = _tool_trace_breakdown(empty)
                self.assertEqual(breakdown["total"], 0)
                self.assertEqual(breakdown["failure_rate"], 0.0)
                self.assertEqual(breakdown["success"]["count"], 0)
                self.assertIsNone(breakdown["success"]["avg_ms"])
                # 固定类别顺序，便于前端稳定渲染
                self.assertEqual([item["category"] for item in breakdown["failures"]], ["validation", "business", "infra", "unknown"])

    def test_aggregates_success_and_failure_categories_with_durations(self):
        rows = [
            [_step(ok=True, duration=800), _step(ok=True, duration=1200)],
            [_step(ok=False, category="validation", duration=100), _step(ok=False, category="business", duration=300)],
            [_step(ok=False, category="validation", duration=200)],
        ]
        breakdown = _tool_trace_breakdown(rows)

        self.assertEqual(breakdown["total"], 5)
        self.assertEqual(breakdown["success_count"], 2)
        self.assertEqual(breakdown["failure_count"], 3)
        self.assertEqual(breakdown["failure_rate"], 60.0)
        self.assertEqual(breakdown["success"]["avg_ms"], 1000)
        self.assertEqual(breakdown["success"]["max_ms"], 1200)

        by_category = {item["category"]: item for item in breakdown["failures"]}
        self.assertEqual(by_category["validation"]["count"], 2)
        self.assertEqual(by_category["validation"]["avg_ms"], 150)
        self.assertEqual(by_category["validation"]["max_ms"], 200)
        self.assertEqual(by_category["business"]["count"], 1)
        self.assertEqual(by_category["business"]["avg_ms"], 300)
        self.assertEqual(by_category["infra"]["count"], 0)
        self.assertIsNone(by_category["infra"]["avg_ms"])

    def test_failure_without_category_lands_in_unknown(self):
        breakdown = _tool_trace_breakdown([[_step(ok=False, duration=50)]])
        by_category = {item["category"]: item for item in breakdown["failures"]}
        self.assertEqual(by_category["unknown"]["count"], 1)
        self.assertEqual(by_category["unknown"]["avg_ms"], 50)

    def test_non_dict_steps_and_bad_durations_are_ignored(self):
        rows = [
            ["junk", _step(ok=True, duration=-5), _step(ok=True, duration="fast"), _step(ok=False, category="infra")],
        ]
        breakdown = _tool_trace_breakdown(rows)
        self.assertEqual(breakdown["total"], 3)
        self.assertEqual(breakdown["success_count"], 2)
        # 没有合法耗时的步骤计入次数但不进 avg/max
        self.assertIsNone(breakdown["success"]["avg_ms"])
        by_category = {item["category"]: item for item in breakdown["failures"]}
        self.assertEqual(by_category["infra"]["count"], 1)
        self.assertIsNone(by_category["infra"]["avg_ms"])


if __name__ == "__main__":
    unittest.main()
