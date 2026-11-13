"""模板配置白名单：CSS 注入与 PDF 渲染期 SSRF 的防线。

`template_config` 由前端整包提交，值会直接进 `<style>`。这组测试固定三件事：
合法值原样保留、非法值被丢弃、渲染结果里不出现注入片段。
"""

import unittest

from app.core.template_config import ALLOWED_FONT_FAMILIES, normalize_template_config
from app.schemas.resume import default_resume_data
from app.services.preview_service import FONT_PRESETS, css_font_family, render_resume_html


class NormalizeTemplateConfigTest(unittest.TestCase):
    def test_keeps_valid_values(self) -> None:
        config = normalize_template_config(
            {
                "template_id": "tech",
                "theme_color": "#2563EB",
                "bg_color": "#fff",
                "font_family": "vf-serif",
                "name_font_size": 30,
                "line_height": 1.55,
                "page_margin_top": 12,
                "show_avatar": False,
                "avatar_position": "center",
            }
        )

        self.assertEqual(config["theme_color"], "#2563eb", "颜色统一小写")
        self.assertEqual(config["bg_color"], "#ffffff", "三位色补全为六位")
        self.assertEqual(config["font_family"], "vf-serif")
        self.assertEqual(config["name_font_size"], 30)
        self.assertEqual(config["line_height"], 1.55)
        self.assertEqual(config["page_margin_top"], 12)
        self.assertFalse(config["show_avatar"])
        self.assertEqual(config["avatar_position"], "center")

    def test_drops_css_injection_payloads(self) -> None:
        config = normalize_template_config(
            {
                "theme_color": "#fff; } .page { background-image: url(http://169.254.169.254/latest) }",
                "bg_color": "red",
                "icon_color": "expression(alert(1))",
                "font_family": 'x"; } body { display: none } .a{',
                "name_font_size": "28px; } body { display:none",
            }
        )

        self.assertEqual(config, {}, "所有注入值都必须被丢弃，交给模板默认值兜底")

    def test_drops_out_of_range_numbers(self) -> None:
        config = normalize_template_config(
            {
                "name_font_size": 400,
                "body_font_size": 2,
                "line_height": 9,
                "page_margin_left": -5,
                "page_margin_right": 200,
            }
        )

        self.assertEqual(config, {})

    def test_accepts_whole_float_from_json(self) -> None:
        # 前端 JSON 序列化偶尔给 14.0，不该当成非法值丢掉。
        self.assertEqual(normalize_template_config({"page_margin_top": 14.0})["page_margin_top"], 14)

    def test_ignores_non_dict_input(self) -> None:
        self.assertEqual(normalize_template_config(None), {})
        self.assertEqual(normalize_template_config(["theme_color"]), {})
        self.assertEqual(normalize_template_config("tech"), {})

    def test_rejects_template_id_with_path_characters(self) -> None:
        self.assertNotIn("template_id", normalize_template_config({"template_id": "../etc/passwd"}))
        self.assertNotIn("template_id", normalize_template_config({"template_id": "tech/../../x"}))


class FontPresetAlignmentTest(unittest.TestCase):
    def test_allowed_font_keys_match_renderer_presets(self) -> None:
        # 两处集合一旦漂移，前端能选的字体就会被白名单静默丢弃。
        self.assertEqual(set(ALLOWED_FONT_FAMILIES), set(FONT_PRESETS))


class RenderSanitizationTest(unittest.TestCase):
    def test_malicious_config_never_reaches_the_style_block(self) -> None:
        html = render_resume_html(
            default_resume_data(),
            "classic",
            {
                "theme_color": "#fff; background-image: url(http://169.254.169.254/latest/meta-data)",
                "font_family": "x; } body { display: none } .a{",
                "name_font_size": "28px; } body { display:none",
                "bg_color": "#ffffff",
            },
            "zh-CN",
        )

        self.assertNotIn("169.254.169.254", html, "外链不能进入渲染结果（PDF 会真的去请求）")
        self.assertNotIn("display: none", html)
        self.assertIn("#2563eb", html, "非法主题色回落到模板默认值")
        self.assertIn("VitaFlow Sans SC", html, "非法字体名回落到默认预设")

    def test_render_does_not_mutate_the_caller_config(self) -> None:
        config = {"theme_color": "#123456"}
        render_resume_html(default_resume_data(), "classic", config, "zh-CN")

        self.assertEqual(config, {"theme_color": "#123456"}, "渲染不该往调用方的 dict 里塞 template_id")


class CssFontFamilyTest(unittest.TestCase):
    def test_unknown_font_falls_back_to_default_preset(self) -> None:
        self.assertIn("VitaFlow Sans SC", str(css_font_family("whatever; } body { display:none")))
        self.assertNotIn("display:none", str(css_font_family("whatever; } body { display:none")))

    def test_preset_key_expands_to_font_stack(self) -> None:
        self.assertIn("LXGW WenKai", str(css_font_family("vf-kai")))


if __name__ == "__main__":
    unittest.main()
