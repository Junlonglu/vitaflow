"""AI 任务列表/导出用到的辅助函数单测（纯函数，不连库）。

- `_task_duration_ms`：列表里「耗时」列的唯一来源，取不到要给 None 让前端显示
  「—」，不能给 0（0ms 会被读成「瞬间完成」）。
- `_csv_cell`：CSV 用逗号分列，错误信息里同时出现逗号、引号、换行的概率不低，
  转义错一位整行就会错列。
"""
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.api.admin.records import _csv_cell, _task_duration_ms


class TaskDurationTest(unittest.TestCase):
    def _task(self, input_data=None, create=None, update=None):
        return SimpleNamespace(input_data=input_data, create_time=create, update_time=update)

    def test_prefers_turn_timing_total(self):
        task = self._task({"turn_timing": {"total_ms": 35494}}, datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 10, 0, 1))
        self.assertEqual(_task_duration_ms(task), 35494)

    def test_falls_back_to_create_update_gap(self):
        task = self._task({}, datetime(2026, 1, 1, 10, 0, 0), datetime(2026, 1, 1, 10, 0, 2))
        self.assertEqual(_task_duration_ms(task), 2000)

    def test_negative_gap_returns_none(self):
        task = self._task({}, datetime(2026, 1, 1, 10, 0, 2), datetime(2026, 1, 1, 10, 0, 0))
        self.assertIsNone(_task_duration_ms(task))

    def test_missing_timestamps_return_none(self):
        self.assertIsNone(_task_duration_ms(self._task({})))


class CsvCellTest(unittest.TestCase):
    def test_plain_value_is_untouched(self):
        self.assertEqual(_csv_cell("ai_chat"), "ai_chat")

    def test_comma_quote_and_newline_are_escaped(self):
        self.assertEqual(_csv_cell("a,b"), '"a,b"')
        self.assertEqual(_csv_cell('say "hi"'), '"say ""hi"""')
        self.assertEqual(_csv_cell("line1\nline2"), '"line1\nline2"')

    def test_none_becomes_empty(self):
        self.assertEqual(_csv_cell(None), "")


if __name__ == "__main__":
    unittest.main()
