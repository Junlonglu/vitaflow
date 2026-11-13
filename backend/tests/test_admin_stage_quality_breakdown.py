"""Tests for the admin dashboard's stage-quality aggregation (对话环节质量卡).

`_stage_quality_breakdown` consumes (input_data, status) rows newest-first:
a row's ``turn_timing`` dict feeds the turn stages (回合环节)，its
``tool_quality`` steps feed one stage per tool. Rows come from AiTask
（ai-service 计费上报的事实行，与消息表解耦——删简历后口径仍与功能分布一致）.
Pure function, no DB.
"""
import unittest

from app.api.admin.dashboard import _stage_quality_breakdown


def _timing(**overrides):
    timing = {
        "first_token_ms": 1200,
        "tool_ms": 100,
        "total_ms": 5000,
        "loader_ms": 5,
        "session_create_ms": 3,
        "provider_ttfb_ms": 1100,
    }
    timing.update(overrides)
    return timing


def _row(timing=None, quality=None, status="success"):
    input_data: dict = {}
    if timing is not None:
        input_data["turn_timing"] = timing
    if quality is not None:
        input_data["tool_quality"] = quality
    return (input_data, status)


class StageQualityBreakdownTest(unittest.TestCase):
    def test_empty_rows_yield_zeroed_breakdown(self):
        breakdown = _stage_quality_breakdown([])
        self.assertEqual(breakdown["turns"], 0)
        self.assertEqual(breakdown["turn_failure_rate"], 0.0)
        self.assertEqual(breakdown["stages"], [])

    def test_turn_stages_aggregate_from_timing_in_fixed_order(self):
        breakdown = _stage_quality_breakdown([_row(timing=_timing())])
        self.assertEqual(breakdown["turns"], 1)
        self.assertEqual(breakdown["turn_failure_rate"], 0.0)
        self.assertEqual(
            [stage["key"] for stage in breakdown["stages"]],
            ["first_token_ms", "loader_ms", "session_create_ms", "provider_ttfb_ms", "generation_ms", "total_ms"],
        )
        by_key = {stage["key"]: stage for stage in breakdown["stages"]}
        self.assertEqual(by_key["total_ms"], {
            "key": "total_ms",
            "kind": "turn",
            "samples": 1,
            "avg_ms": 5000,
            "p50_ms": 5000,
            "p95_ms": None,
            "max_ms": 5000,
            "recent_avg_ms": 5000,
            "recent_delta_pct": None,
        })
        self.assertEqual(by_key["generation_ms"]["avg_ms"], 4900, "生成 = 整回合 - 工具执行")

    def test_p95_is_nearest_rank_and_hidden_below_20_samples(self):
        rows = [_row(timing=_timing(total_ms=value)) for value in range(1, 21)]
        by_key = {stage["key"]: stage for stage in _stage_quality_breakdown(rows)["stages"]}
        self.assertEqual(by_key["total_ms"]["p95_ms"], 19)
        self.assertEqual(by_key["total_ms"]["p50_ms"], 10, "nearest-rank 中位数随样本数始终返回")
        self.assertEqual(by_key["total_ms"]["max_ms"], 20)

        by_key19 = {stage["key"]: stage for stage in _stage_quality_breakdown(rows[:19])["stages"]}
        self.assertIsNone(by_key19["total_ms"]["p95_ms"])
        self.assertEqual(by_key19["total_ms"]["p50_ms"], 10)

    def test_recent_avg_takes_the_first_10_rows_newest_first(self):
        rows = [_row(timing=_timing(total_ms=100)) for _ in range(10)]
        rows += [_row(timing=_timing(total_ms=200)) for _ in range(10)]
        by_key = {stage["key"]: stage for stage in _stage_quality_breakdown(rows)["stages"]}
        self.assertEqual(by_key["total_ms"]["avg_ms"], 150)
        self.assertEqual(by_key["total_ms"]["recent_avg_ms"], 100, "行序新→旧：前 10 行是 100ms 的近期样本")
        self.assertEqual(by_key["total_ms"]["recent_delta_pct"], -33)

    def test_tool_stage_success_failures_and_recent_reasons(self):
        quality = [
            {"name": "update_section", "status": "end", "ok": False, "category": "validation", "duration_ms": 120, "reason": "数字缺少依据"},
            {"name": "update_section", "status": "end", "ok": False, "category": "validation", "duration_ms": 80, "reason": "没有实际内容变化"},
            {"name": "update_section", "status": "end", "ok": False, "category": "validation", "duration_ms": 60, "reason": "数字缺少依据"},
            {"name": "update_section", "status": "end", "ok": False, "duration_ms": 40},
            {"name": "update_section", "status": "end", "ok": True, "duration_ms": 30},
        ]
        stage = next(s for s in _stage_quality_breakdown([_row(quality=quality)])["stages"] if s["key"] == "update_section")
        self.assertEqual(stage["kind"], "tool")
        self.assertEqual(stage["samples"], 5)
        self.assertEqual(stage["success_rate"], 20.0)
        self.assertEqual(stage["avg_ms"], 66)
        self.assertEqual(stage["max_ms"], 120)

        by_category = {item["category"]: item for item in stage["failures"]}
        self.assertEqual([item["category"] for item in stage["failures"]], ["validation", "business", "infra", "unknown"])
        self.assertEqual(by_category["validation"], {"category": "validation", "count": 3, "avg_ms": 87})
        self.assertEqual(by_category["business"]["count"], 0)
        self.assertIsNone(by_category["business"]["avg_ms"])
        self.assertEqual(by_category["unknown"]["count"], 1, "没有 category 的失败步骤归入 unknown")

        self.assertEqual(stage["recent_reasons"], ["数字缺少依据", "没有实际内容变化"], "去重且按新→旧")

    def test_tool_stages_sort_by_samples_desc(self):
        quality = [
            {"name": "get_resume_context", "status": "end", "ok": True, "duration_ms": 30},
            {"name": "update_section", "status": "end", "ok": True, "duration_ms": 30},
            {"name": "update_section", "status": "end", "ok": True, "duration_ms": 30},
        ]
        stages = _stage_quality_breakdown([_row(quality=quality)])["stages"]
        self.assertEqual([stage["key"] for stage in stages], ["update_section", "get_resume_context"])

    def test_failed_rows_count_toward_turn_failure_rate_once(self):
        rows = [
            _row(timing=_timing(), status="failed"),
            _row(timing=_timing()),
            ({}, "failed"),
        ]
        breakdown = _stage_quality_breakdown(rows)
        self.assertEqual(breakdown["turns"], 2)
        self.assertEqual(breakdown["turn_failure_rate"], 66.7)

    def test_bad_rows_and_steps_are_ignored(self):
        rows = [
            ("junk", "success"),
            ({"tool_quality": ["junk", {"name": "update_section", "status": "start"}]}, "success"),
            ({"turn_timing": {"total_ms": -5, "first_token_ms": 100}}, "success"),
            ({"turn_timing": {"total_ms": 100, "first_token_ms": -1, "loader_ms": 2.5}}, "success"),
        ]
        by_key = {stage["key"]: stage for stage in _stage_quality_breakdown(rows)["stages"]}
        self.assertEqual(by_key["total_ms"]["samples"], 1)
        self.assertEqual(by_key["total_ms"]["avg_ms"], 100)
        self.assertNotIn("first_token_ms", by_key, "total_ms 无效的整行跳过；负的阶段值也丢弃")
        self.assertEqual(by_key["loader_ms"]["avg_ms"], 2, "float 耗时聚合时取整")
        self.assertNotIn("provider_ttfb_ms", by_key)
        self.assertNotIn("update_section", by_key, "start 步骤与非 dict 步骤不进聚合")

    def test_reason_is_ignored_on_success_steps(self):
        quality = [{"name": "propose_change", "status": "end", "ok": True, "reason": "已修改：实习/工作经历"}]
        stage = next(s for s in _stage_quality_breakdown([_row(quality=quality)])["stages"] if s["key"] == "propose_change")
        self.assertEqual(stage["recent_reasons"], [])


if __name__ == "__main__":
    unittest.main()
