"""管理后台看板窗口/聚合辅助函数的单测（纯函数，不连库）。

覆盖改版后新加的口径逻辑：时间窗口与环比、失败原因归一化、按日分组键、
模型 key 收敛。这些都是「算错了也不会报错、只会显示成另一个数字」的地方。
"""
import unittest
from datetime import datetime, timedelta

from app.api.admin.common import window_start
from app.api.admin.dashboard import (
    _day_key,
    _kpi,
    _normalize_reason,
    _pct_delta,
    _resolve_model_key,
    _resolve_window,
)


class ResolveWindowTest(unittest.TestCase):
    def test_seven_days_covers_today_and_previous_window(self):
        window = _resolve_window("7d")
        self.assertEqual(window["key"], "7d")
        self.assertEqual(window["days"], 7)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(window["start"], today - timedelta(days=6))
        self.assertEqual(window["prev_start"], today - timedelta(days=13))
        self.assertEqual(window["today"], today)

    def test_today_window_is_a_single_day(self):
        window = _resolve_window("today")
        self.assertEqual(window["days"], 1)
        self.assertEqual(window["start"], window["today"])
        self.assertEqual(window["prev_start"], window["today"] - timedelta(days=1))

    def test_unknown_range_falls_back_to_seven_days(self):
        self.assertEqual(_resolve_window("bogus")["key"], "7d")

    def test_window_start_returns_none_for_all_time(self):
        self.assertIsNone(window_start("all"))
        self.assertIsNone(window_start(None))
        self.assertIsNotNone(window_start("30d"))


class DeltaTest(unittest.TestCase):
    def test_delta_pct_rounds_and_handles_zero_base(self):
        self.assertEqual(_pct_delta(12, 10), 20.0)
        self.assertEqual(_pct_delta(5, 10), -50.0)
        # 上一窗口为 0 时没有可比基数，前端应显示「—」而不是 0% 或无穷大
        self.assertIsNone(_pct_delta(8, 0))

    def test_kpi_carries_value_prev_and_delta(self):
        self.assertEqual(_kpi(3, 2), {"value": 3, "prev": 2, "delta_pct": 50.0})


class NormalizeReasonTest(unittest.TestCase):
    def test_collapses_whitespace_and_newlines(self):
        self.assertEqual(_normalize_reason("上游 503\n  超时\t重试"), "上游 503 超时 重试")

    def test_truncates_long_reason(self):
        text = _normalize_reason("x" * 200)
        self.assertEqual(len(text), 140)
        self.assertTrue(text.endswith("..."))


class DayKeyTest(unittest.TestCase):
    def test_accepts_datetime_date_and_string(self):
        self.assertEqual(_day_key(datetime(2026, 11, 17, 13, 22)), "2026-11-17")
        self.assertEqual(_day_key("2026-11-17"), "2026-11-17")
        self.assertEqual(_day_key("2026-11-17T00:00:00"), "2026-11-17")


class ResolveModelKeyTest(unittest.TestCase):
    def test_bare_model_id_maps_to_unique_ref(self):
        self.assertEqual(
            _resolve_model_key({"model": "deepseek-flash"}, None, {"deepseek-flash": "deepseek/deepseek-flash"}),
            "deepseek/deepseek-flash",
        )

    def test_full_ref_is_kept_as_is(self):
        self.assertEqual(
            _resolve_model_key({"model": "deepseek/deepseek-flash"}, None, {}),
            "deepseek/deepseek-flash",
        )

    def test_falls_back_to_raw_name_then_config_name(self):
        self.assertEqual(_resolve_model_key({}, "vendor/model", {}), "vendor/model")
        self.assertEqual(_resolve_model_key({"model_name": "策略 A"}, None, {}), "策略 A")


if __name__ == "__main__":
    unittest.main()
