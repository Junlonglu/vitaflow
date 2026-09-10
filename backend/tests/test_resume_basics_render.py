"""基本信息联系方式的行内统一排序契约。

渲染模板（partials/basics.html 共享宏 + blue_timeline 副本）按 row 分行，
同一行内内置字段与自定义字段按 order 统一排序——自定义字段不再固定垫底；
normalize 回填缺失的 row/order，缺值不崩、排最后。
"""
import re
import unittest

from app.services.preview_service import render_resume_html
from app.services.resume_normalization import normalize_resume_data


def _resume_data() -> dict:
    return {
        "basics": {
            "name": "张三",
            "phone": "13800000000",
            "email": "zhang@example.com",
            "field_config": {
                "phone": {"label": "电话", "icon": "Phone", "row": 1, "order": 2},
                "email": {"label": "邮箱", "icon": "Mail", "row": 1, "order": 3},
            },
            "custom_fields": [
                {"id": "f1", "label": "微信", "value": "wx123", "icon": "MessageCircle", "row": 1, "order": 1},
                {"id": "f2", "label": "驾照", "value": "C1", "icon": "Car", "row": 1},
            ],
        }
    }


def _meta_values(html: str, meta_class: str) -> list[str]:
    block = re.search(rf'<div class="{meta_class}">(.*?)</div>', html, re.S)
    if not block:
        return []
    spans = re.findall(r"<span>(.*?)</span>", block.group(1), re.S)
    return [stripped.strip() for span in spans if (stripped := re.sub(r"<[^>]+>", "", span)).strip()]


class UnifiedFieldOrderTest(unittest.TestCase):
    def test_custom_field_interleaves_with_builtins_in_classic(self):
        html = render_resume_html(_resume_data(), "classic", {})
        values = _meta_values(html, "resume-meta")
        self.assertEqual(values[:4], ["wx123", "13800000000", "zhang@example.com", "C1"])

    def test_custom_field_interleaves_with_builtins_in_blue_timeline(self):
        html = render_resume_html(_resume_data(), "blue_timeline", {})
        values = _meta_values(html, "blue-timeline-meta")
        self.assertEqual(values[:4], ["wx123", "13800000000", "zhang@example.com", "C1"])

    def test_normalization_backfills_custom_field_row_order(self):
        data = normalize_resume_data(_resume_data(), {"language": "zh-CN"})
        fields = data["basics"]["custom_fields"]
        self.assertEqual(fields[0]["order"], 1)
        self.assertEqual(fields[1]["order"], 1001)
        self.assertEqual(fields[1]["row"], 1)

    def test_builtin_only_renders_in_order_unchanged(self):
        data = {
            "basics": {
                "name": "张三",
                "phone": "13800000000",
                "email": "zhang@example.com",
                "field_config": {
                    "email": {"label": "邮箱", "icon": "Mail", "row": 1, "order": 1},
                    "phone": {"label": "电话", "icon": "Phone", "row": 1, "order": 2},
                },
            }
        }
        html = render_resume_html(data, "classic", {})
        values = _meta_values(html, "resume-meta")
        self.assertEqual(values[:2], ["zhang@example.com", "13800000000"])

    def test_dirty_order_values_do_not_crash(self):
        # order 混入字符串/None：|int 兜底，排序不崩
        data = {
            "basics": {
                "name": "张三",
                "phone": "13800000000",
                "email": "zhang@example.com",
                "field_config": {
                    "phone": {"label": "电话", "icon": "Phone", "row": 1, "order": "2"},
                    "email": {"label": "邮箱", "icon": "Mail", "row": 1, "order": None},
                },
            }
        }
        html = render_resume_html(data, "classic", {})
        values = _meta_values(html, "resume-meta")
        # order "2" 强转 2 排前；None 兜底 9999 排后——不崩溃且顺序合理
        self.assertEqual(values[:2], ["13800000000", "zhang@example.com"])

    def test_normalization_clamps_out_of_range_row(self):
        data = _resume_data()
        data["basics"]["custom_fields"][0]["row"] = 9
        normalized = normalize_resume_data(data, {"language": "zh-CN"})
        self.assertEqual(normalized["basics"]["custom_fields"][0]["row"], 1)


if __name__ == "__main__":
    unittest.main()
