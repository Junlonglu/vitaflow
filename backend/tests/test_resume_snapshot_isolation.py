"""简历快照的隔离性：复制、存版本、换模板都不能共享可变 dict。

SQLAlchemy 的 JSON 列不追踪原地修改，两个行对象指向同一个 dict 时，改一份
会静默改到另一份 —— 复制出来的简历和原简历、历史版本和当前简历都会串。
"""

import unittest
from types import SimpleNamespace

from app.schemas.resume import ResumeUpdate
from app.services import resume_service


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None

    def __iter__(self):
        return iter(self._items)


class _DbStub:
    def __init__(self, resume):
        self.resume = resume
        self.added = []
        self.commits = 0

    def get(self, _model, _key):
        return self.resume

    def add(self, obj):
        self.added.append(obj)

    def scalars(self, _stmt):
        # create_version 会先查「最新一版」做内容去重；stub 里没有历史版本。
        return _ScalarResult([])

    def scalar(self, _stmt):
        return 0

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        pass


def _source_resume():
    return SimpleNamespace(
        id=7,
        user_id=1,
        title="张三的简历",
        language="zh-CN",
        resume_data={"basics": {"name": "张三"}, "work": [{"id": "w1", "company": "A 公司"}]},
        template_id="tech",
        template_config={"template_id": "tech", "theme_color": "#2563eb", "font_family": "vf-sans"},
    )


class DuplicateIsolationTest(unittest.TestCase):
    def test_editing_the_copy_does_not_touch_the_original(self) -> None:
        source = _source_resume()
        copy = resume_service.duplicate_resume(_DbStub(source), 1, 7, None)

        copy.resume_data["basics"]["name"] = "李四"
        copy.resume_data["work"].append({"id": "w2", "company": "B 公司"})
        copy.template_config["theme_color"] = "#000000"

        self.assertEqual(source.resume_data["basics"]["name"], "张三")
        self.assertEqual(len(source.resume_data["work"]), 1)
        self.assertEqual(source.template_config["theme_color"], "#2563eb")


class VersionSnapshotIsolationTest(unittest.TestCase):
    def test_version_snapshot_is_detached_from_the_live_resume(self) -> None:
        source = _source_resume()
        version, created = resume_service.create_version(_DbStub(source), 1, 7, "AI 修改前")

        self.assertTrue(created)
        source.resume_data["basics"]["name"] = "改过的名字"
        source.template_config["theme_color"] = "#111111"

        self.assertEqual(version.resume_data["basics"]["name"], "张三", "历史版本必须冻结")
        self.assertEqual(version.template_config["theme_color"], "#2563eb")


class UpdateNormalizesTemplateConfigTest(unittest.TestCase):
    def test_injected_style_values_are_not_persisted(self) -> None:
        source = _source_resume()
        payload = ResumeUpdate(
            template_config={
                "theme_color": "#fff; } body { display: none } .x{",
                "font_family": "evil; } body { display:none",
                "name_font_size": 999,
                "bg_color": "#FFFFFF",
            }
        )

        resume_service.update_resume(_DbStub(source), 1, 7, payload)

        self.assertNotIn("theme_color", source.template_config)
        self.assertNotIn("font_family", source.template_config)
        self.assertNotIn("name_font_size", source.template_config)
        self.assertEqual(source.template_config["bg_color"], "#ffffff", "合法值保留并规范化")

    def test_switching_template_does_not_mutate_the_original_dict_in_place(self) -> None:
        source = _source_resume()
        original = source.template_config

        resume_service.update_resume(_DbStub(source), 1, 7, ResumeUpdate(template_id="classic"))

        self.assertEqual(original["template_id"], "tech", "原 dict 不该被就地改写")
        self.assertEqual(source.template_config["template_id"], "classic")


if __name__ == "__main__":
    unittest.main()
