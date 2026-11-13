"""模板配置的服务端白名单。

`template_config` 由前端整体提交，而模板会把里面的颜色、字号、行高直接插进
`<style>` / `<style>` 内的 CSS 变量。Jinja 的自动转义只处理 `<>"'`，`;`、`{}`、
`(` 全部放行——只要一个值写成 `#fff; } .tip { ... }`，分享页就能被塞进任意
样式；写成 `url(http://169.254.169.254/...)` 则会在 PDF 渲染时由 Chromium 真实
发请求，成为盲 SSRF。

因此所有写入与渲染入口都先过这里：只保留模板真正会用到的键，值必须匹配预期的
类型与范围，非法值直接丢弃（模板侧本来就有 `or 默认值` 兜底）。
"""

from __future__ import annotations

from typing import Any

# 与 preview_service.FONT_PRESETS 的键保持一致；只允许预设键，不接受任意
# font stack —— 那条路径正是 CSS 注入的入口。tests/test_template_config.py
# 会校验两处集合没有漂移。
ALLOWED_FONT_FAMILIES = frozenset({"vf-sans", "vf-serif", "vf-rounded", "vf-kai"})

COLOR_KEYS = (
    "theme_color",
    "bg_color",
    "icon_color",
    "header_icon_color",
    "accent_color",
    "name_font_color",
    "title_font_color",
    "body_font_color",
)

# 键 → (最小值, 最大值)，单位 px。
PX_RANGES: dict[str, tuple[int, int]] = {
    "name_font_size": (12, 72),
    "title_font_size": (8, 40),
    "body_font_size": (8, 24),
    "section_margin_top": (0, 40),
    "section_margin_bottom": (0, 40),
    "section_title_margin_bottom": (0, 40),
}

# 页边距单位 mm，A4 高度 297mm，留出足够余量。
MARGIN_KEYS = (
    "page_margin_top",
    "page_margin_right",
    "page_margin_bottom",
    "page_margin_left",
    "next_page_margin_top",
    "next_page_margin_bottom",
)
MARGIN_RANGE = (0, 40)

LINE_HEIGHT_RANGE = (1.0, 2.5)
AVATAR_POSITIONS = frozenset({"left", "center", "right"})
MAX_TEMPLATE_ID_LENGTH = 50

_HEX_DIGITS = set("0123456789abcdef")


def normalize_color(value: Any) -> str | None:
    """`#rgb` / `#rrggbb`（也接受不带 #）→ 小写 `#rrggbb`；其余一律拒绝。"""
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) == 3:
        raw = "".join(digit * 2 for digit in raw)
    if len(raw) != 6 or any(digit not in _HEX_DIGITS for digit in raw):
        return None
    return f"#{raw}"


def _normalize_int(value: Any, low: int, high: int) -> int | None:
    # bool 是 int 的子类，`True` 不该被当成 1 号字号。
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = int(round(value))
    if number != value and isinstance(value, float):
        # 只接受整值浮点（前端序列化偶尔给 14.0），避免把 13.7px 静默截断。
        if abs(value - number) > 1e-6:
            return None
    return number if low <= number <= high else None


def _normalize_float(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not low <= number <= high:
        return None
    return round(number, 2)


def normalize_template_config(raw: Any) -> dict[str, Any]:
    """把外部传入的模板配置收敛成可安全渲染的字段集合。

    只做「过滤 + 规范化」，不补默认值：缺失的键由模板里的 `or` 兜底，
    这样服务端就不必跟着前端每加一个样式项就同步改一次默认表。
    """
    if not isinstance(raw, dict):
        return {}

    config: dict[str, Any] = {}

    template_id = raw.get("template_id")
    if isinstance(template_id, str):
        candidate = template_id.strip()
        if candidate and len(candidate) <= MAX_TEMPLATE_ID_LENGTH and candidate.isascii():
            if all(char.isalnum() or char in "_-" for char in candidate):
                config["template_id"] = candidate

    for key in COLOR_KEYS:
        color = normalize_color(raw.get(key))
        if color:
            config[key] = color

    for key, (low, high) in PX_RANGES.items():
        number = _normalize_int(raw.get(key), low, high)
        if number is not None:
            config[key] = number

    margin_low, margin_high = MARGIN_RANGE
    for key in MARGIN_KEYS:
        number = _normalize_int(raw.get(key), margin_low, margin_high)
        if number is not None:
            config[key] = number

    line_height = _normalize_float(raw.get("line_height"), *LINE_HEIGHT_RANGE)
    if line_height is not None:
        config["line_height"] = line_height

    font_family = raw.get("font_family")
    if isinstance(font_family, str) and font_family.strip() in ALLOWED_FONT_FAMILIES:
        config["font_family"] = font_family.strip()

    show_avatar = raw.get("show_avatar")
    if isinstance(show_avatar, bool):
        config["show_avatar"] = show_avatar

    avatar_position = raw.get("avatar_position")
    if isinstance(avatar_position, str) and avatar_position.strip() in AVATAR_POSITIONS:
        config["avatar_position"] = avatar_position.strip()

    return config
