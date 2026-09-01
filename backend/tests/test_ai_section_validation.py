"""Pure-unit tests for validate_section_update — the section-level validation seam.

Mirrors validate_basics_field_update but generalizes to any section: the AI service's
update_section tool proposes a section change (merged by item id), and this guard runs
the numeric anti-hallucination check and returns a merged resume snapshot to stage as a
pending change. Pure/DB-free by design.
"""
import unittest

from app.services.ai_chat_service import (
    get_section_context,
    validate_propose_change,
    validate_section_update,
)


CURRENT = {
    "basics": {"name": "张三", "title": "后端工程师", "location": "广州", "phone": "13800138000"},
    "summary": {"content": "五年前端经验"},
    "work": [
        {"id": "work_1", "company": "A 公司", "role": "后端", "duration": "2020-2021"},
        {"id": "work_2", "company": "B 公司", "role": "前端", "duration": "2021-2022"},
    ],
    "projects": [{"id": "project_1", "name": "项目一"}],
}


def _history():
    return [
        {"role": "user", "content": "把 A 公司那段经历改成 2021-2022 的实习经历"},
        {"role": "assistant", "content": "好的，我已准备好修改方案。"},
    ]


class ValidateSectionUpdateTest(unittest.TestCase):
    def test_field_level_location_change_via_basics_section(self):
        merged, suggestions, issue = validate_section_update(
            CURRENT, "basics", {"location": "深圳"}, _history(), "把地点改成深圳"
        )
        self.assertEqual(issue, "")
        self.assertEqual(merged["basics"]["location"], "深圳")
        self.assertEqual(merged["basics"]["name"], "张三")  # untouched basics survive
        self.assertIn("基本信息", suggestions[0])

    def test_list_item_patched_by_id_keeps_siblings(self):
        merged, suggestions, issue = validate_section_update(
            CURRENT,
            "work",
            [{"id": "work_1", "role": "实习"}],
            _history(),
            "把 A 公司的经历改成实习经历",
        )
        self.assertEqual(issue, "")
        self.assertEqual(len(merged["work"]), 2)
        self.assertEqual(merged["work"][0]["role"], "实习")
        self.assertEqual(merged["work"][1]["company"], "B 公司")  # sibling untouched

    def test_hallucinated_quantified_result_is_blocked(self):
        # The model invents a time range that appears ONLY in its proposal (never in the
        # resume or the user's own words), so the numeric anti-hallucination guard blocks it.
        merged, suggestions, issue = validate_section_update(
            CURRENT,
            "work",
            [{"id": "work_1", "duration": "2023-2024"}],
            [{"role": "user", "content": "把 A 公司经历的时间跨度改一下"}],
            "把 A 公司经历的时间跨度改一下",
        )
        self.assertIsNone(merged)
        self.assertEqual(suggestions, [])
        self.assertIn("量化", issue)

    def test_no_actual_change_is_reported(self):
        merged, suggestions, issue = validate_section_update(
            CURRENT, "summary", {"content": "五年前端经验"}, _history()
        )
        self.assertIsNone(merged)
        self.assertEqual(suggestions, [])
        self.assertIn("变化", issue)

    def test_unknown_section_is_rejected(self):
        merged, suggestions, issue = validate_section_update(CURRENT, "hobbies", [{"id": "x"}], _history())
        self.assertIsNone(merged)
        self.assertEqual(suggestions, [])
        self.assertTrue(issue)

    def test_empty_proposal_is_rejected(self):
        for proposed in (None, [], {}, [{"id": "work_999", "company": ""}]):
            with self.subTest(proposed=proposed):
                merged, suggestions, issue = validate_section_update(CURRENT, "work", proposed, _history())
                self.assertIsNone(merged)
                self.assertEqual(suggestions, [])
                self.assertTrue(issue)

    def test_explicit_removal_is_a_valid_change(self):
        merged, suggestions, issue = validate_section_update(
            CURRENT, "work", [], _history(), "删除 A 公司的经历", removed_ids=["work_1"]
        )
        self.assertEqual(issue, "")
        self.assertEqual([item["id"] for item in merged["work"]], ["work_2"])
        self.assertTrue(suggestions)

    def test_appending_a_new_list_entry_survives_validation(self):
        # A brand-new entry (no id) is minted by the merge; the numeric anti-hallucination
        # guard must NOT flag the generated id's trailing digit. Locks the append path end-to-end.
        merged, suggestions, issue = validate_section_update(
            {"education": [{"id": "edu_1", "school": "甲大学"}]},
            "education",
            [{"school": "乙大学", "degree": "硕士"}],
            [{"role": "user", "content": "新增一段教育经历"}],
            "新增一段教育经历",
        )
        self.assertEqual(issue, "")
        self.assertEqual(len(merged["education"]), 2)
        self.assertEqual(merged["education"][1]["school"], "乙大学")
        self.assertTrue(merged["education"][1]["id"].startswith("edu_"))


class ValidateProposeChangeTest(unittest.TestCase):
    def test_applies_multiple_sections_atomically(self):
        merged, suggestions, issue = validate_propose_change(
            CURRENT,
            [
                {"section": "work", "proposed": [{"id": "work_1", "role": "实习"}]},
                {"section": "basics", "proposed": {"location": "深圳"}},
            ],
            _history(),
            "把 A 公司经历改成实习，并换到深圳",
        )
        self.assertEqual(issue, "")
        self.assertEqual(merged["work"][0]["role"], "实习")
        self.assertEqual(merged["work"][1]["company"], "B 公司")
        self.assertEqual(merged["basics"]["location"], "深圳")

    def test_missed_section_field_in_single_update_reevaluated_by_id(self):
        # Updates targeting the same list section as separate entries are both applied.
        merged, suggestions, issue = validate_propose_change(
            CURRENT,
            [
                {"section": "work", "proposed": [{"id": "work_1", "role": "实习"}]},
                {"section": "work", "proposed": [{"id": "work_2", "role": "高级前端"}]},
            ],
            _history(),
            "改成两个不同岗位",
        )
        self.assertEqual(issue, "")
        self.assertEqual(merged["work"][0]["role"], "实习")
        self.assertEqual(merged["work"][1]["role"], "高级前端")

    def test_empty_scoped_updates_are_rejected(self):
        for updates in (None, [], [{"section": "nope", "proposed": [{"id": "x"}]}]):
            with self.subTest(updates=updates):
                merged, suggestions, issue = validate_propose_change(CURRENT, updates, _history())
                self.assertIsNone(merged)
                self.assertEqual(suggestions, [])
                self.assertTrue(issue)

    def test_hallucinated_number_inside_propose_is_blocked(self):
        merged, suggestions, issue = validate_propose_change(
            CURRENT,
            [{"section": "work", "proposed": [{"id": "work_1", "duration": "2030-2032"}]}],
            [{"role": "user", "content": "把经历时间改一下"}],
            "把经历时间改一下",
        )
        self.assertIsNone(merged)
        self.assertEqual(suggestions, [])
        self.assertIn("量化", issue)

    def test_no_detectable_change_is_reported(self):
        merged, suggestions, issue = validate_propose_change(
            CURRENT,
            [{"section": "summary", "proposed": {"content": "五年前端经验"}}],
            _history(),
        )
        self.assertIsNone(merged)
        self.assertEqual(suggestions, [])
        self.assertIn("变化", issue)


class GetSectionContextTest(unittest.TestCase):
    def test_reads_requested_section_on_demand(self):
        ctx = get_section_context(CURRENT, "work")
        self.assertIn("work", ctx)
        self.assertEqual(ctx["work"], CURRENT["work"])
        self.assertNotIn("basics", ctx)

    def test_reads_comma_separated_sections(self):
        ctx = get_section_context(CURRENT, "work,summary,basics")
        self.assertEqual(set(ctx.keys()), {"work", "summary", "basics"})

    def test_omits_unknown_or_invalid_sections(self):
        ctx = get_section_context(CURRENT, "nope,work,hobbies")
        self.assertEqual(set(ctx.keys()), {"work"})

    def test_empty_section_string_returns_nothing(self):
        self.assertEqual(get_section_context(CURRENT, ""), {})
        self.assertEqual(get_section_context(CURRENT, "   "), {})


if __name__ == "__main__":
    unittest.main()
