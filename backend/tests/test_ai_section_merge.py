"""Pure-unit tests for merge_section_update — the id-bounded section merge seam.

Ticket 04 extends incremental modification to any section. The risky part is list
sections (work/projects/education/awards/skills): the model must edit by item id so
untouched entries survive, and it must not be able to silently reorder or renumber entries.
These tests lock down that merge behaviour in isolation, DB-free.
"""
import unittest

from app.services.ai_chat_service import merge_section_update


class MergeSectionUpdateTest(unittest.TestCase):
    def test_updates_only_matched_list_item_in_place(self):
        """A work item matched by id is patched; the sibling item and its position are untouched."""
        current = {
            "work": [
                {"id": "work_1", "company": "A 公司", "role": "后端", "duration": "2020-2021"},
                {"id": "work_2", "company": "B 公司", "role": "前端", "duration": "2021-2022"},
            ]
        }
        merged = merge_section_update(current, "work", [{"id": "work_1", "role": "全栈"}])

        self.assertEqual(len(merged["work"]), 2)
        self.assertEqual(merged["work"][0]["id"], "work_1")
        self.assertEqual(merged["work"][0]["role"], "全栈")  # patched field
        self.assertEqual(merged["work"][0]["company"], "A 公司")  # untouched field survives
        # Sibling unchanged, same position.
        self.assertEqual(merged["work"][1], {"id": "work_2", "company": "B 公司", "role": "前端", "duration": "2021-2022"})

    def test_untouched_items_are_preserved(self):
        """Items not named by the model are left exactly as they were."""
        current = {
            "projects": [
                {"id": "project_1", "name": "项目一"},
                {"id": "project_2", "name": "项目二", "description": "描述二"},
            ]
        }
        merged = merge_section_update(current, "projects", [{"id": "project_1", "name": "项目一（改）"}])

        self.assertEqual([item["id"] for item in merged["projects"]], ["project_1", "project_2"])
        self.assertEqual(merged["projects"][0]["name"], "项目一（改）")
        self.assertEqual(merged["projects"][1], {"id": "project_2", "name": "项目二", "description": "描述二"})

    def test_explicitly_removed_item_is_deleted(self):
        current = {
            "work": [
                {"id": "work_1", "company": "A"},
                {"id": "work_2", "company": "B"},
            ]
        }
        merged = merge_section_update(current, "work", [], removed_ids=["work_1"])
        self.assertEqual([item["id"] for item in merged["work"]], ["work_2"])

    def test_new_item_without_id_gets_generated_id_and_appends(self):
        """A brand-new list entry (no known id) is appended, not merged, and gets a stable generated id."""
        current = {"education": [{"id": "edu_1", "school": "甲大学"}]}
        merged = merge_section_update(current, "education", [{"school": "乙大学", "degree": "硕士"}])

        self.assertEqual(len(merged["education"]), 2)
        # Original untouched & first; the new entry added at the end with a generated id.
        self.assertEqual(merged["education"][0]["id"], "edu_1")
        self.assertEqual(merged["education"][1]["school"], "乙大学")
        self.assertTrue(str(merged["education"][1]["id"]).startswith("edu_"))

    def test_model_cannot_renumber_existing_ids(self):
        """An id supplied by the model that is not in the current data is treated as new,
        so the model cannot claim an existing entry is a different id (reordering/renumbering)."""
        current = {"projects": [{"id": "project_1", "name": "项目一"}]}
        merged = merge_section_update(current, "projects", [{"id": "project_999", "name": "篡改"}])

        self.assertEqual(len(merged["projects"]), 2)
        self.assertEqual(merged["projects"][0]["id"], "project_1")
        self.assertEqual(merged["projects"][1]["name"], "篡改")
        self.assertNotEqual(merged["projects"][1]["id"], "project_999")  # never trusts model ids for new entries

    def test_dict_section_merges_field_level(self):
        """A dict section (summary) merges field-level, dropping omitted keys onto the current snapshot."""
        current = {"summary": {"content": "五年前端经验"}}
        merged = merge_section_update(current, "summary", {"content": "八年全栈经验"})
        self.assertEqual(merged["summary"]["content"], "八年全栈经验")

    def test_layout_section_merges_field_level(self):
        current = {"layout": {"section_order": ["basics", "summary"], "hidden_sections": []}}
        merged = merge_section_update(current, "layout", {"section_order": ["basics", "summary", "work"]})
        self.assertEqual(merged["layout"]["section_order"], ["basics", "summary", "work"])
        self.assertEqual(merged["layout"]["hidden_sections"], [])

    def test_custom_section_is_addressed_by_id(self):
        """custom_sections entries are located by their own id, and only that entry is patched."""
        current = {
            "custom_sections": [
                {"id": "custom_1", "title": "自定义模块", "lines": ["一行"]},
            ]
        }
        merged = merge_section_update(current, "custom_sections", [{"id": "custom_1", "lines": ["二行"]}])
        self.assertEqual(len(merged["custom_sections"]), 1)
        self.assertEqual(merged["custom_sections"][0]["id"], "custom_1")
        self.assertEqual(merged["custom_sections"][0]["lines"], ["二行"])
        self.assertEqual(merged["custom_sections"][0]["title"], "自定义模块")

    def test_unknown_section_or_empty_proposal_is_noop(self):
        current = {"skills": [{"id": "skill_1", "name": "Python"}]}
        self.assertEqual(merge_section_update(current, "hobbies", [{"id": "x"}]), current)
        self.assertEqual(merge_section_update(current, "skills", []), current)


if __name__ == "__main__":
    unittest.main()
