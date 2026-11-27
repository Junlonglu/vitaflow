"""识别简历里的「模板占位值」。

新建简历带的是一份示例基本信息（``schemas.resume.default_resume_data``），
字段非空但内容是假的。信息库导入过去只填空白字段，于是 Elliott / zhangsan@example.com
这类示例值成了挡板：导入成功、界面提示成功、简历里一个字没变。

这里把「哪些值属于模板示例」收敛到一处，只给明确的覆盖场景用（用户点导入、
Agent 补齐基本信息）。判断分两层：

* 精确命中模板默认值（姓名、邮箱、示例链接等）；
* 形状命中（``@example.com`` 邮箱、连号 0 的手机号），覆盖用户手改了一部分
  但联系方式没换的常见情况。

用户真实填写的内容不会被误判成占位——「上海」「本科」这类值虽然也是模板默认，
但只在 ``overwrite_placeholders=True`` 的显式覆盖路径上才会被替换。
"""
from __future__ import annotations

import re
from typing import Any

from app.schemas.resume import default_resume_data

_PLACEHOLDER_VALUES: dict[str, set[str]] = {}
for _key, _value in (default_resume_data().get("basics") or {}).items():
    if isinstance(_value, str) and _value.strip():
        _PLACEHOLDER_VALUES.setdefault(str(_key), set()).add(_value.strip())

_EMPTY_PHONE = re.compile(r"^1[3-9]\d0{6,}$")
_EMPTY_EMAIL_DOMAINS = ("example.com", "example.org", "test.com")


def is_blank(value: Any) -> bool:
    return not str(value or "").strip()


def is_placeholder_value(key: str, value: Any) -> bool:
    """这个字段值是不是模板示例（空值不算占位，算「没填」）。"""
    text = str(value or "").strip()
    if not text:
        return False
    if text in _PLACEHOLDER_VALUES.get(str(key), set()):
        return True
    if key == "email":
        domain = text.rsplit("@", 1)[-1].lower() if "@" in text else ""
        if domain in _EMPTY_EMAIL_DOMAINS:
            return True
    if key == "phone":
        digits = re.sub(r"\D", "", text)
        if _EMPTY_PHONE.match(digits):
            return True
    if key in ("github", "website") and "example" in text.lower():
        return True
    return False


def placeholder_fields(basics: dict[str, Any] | None) -> list[str]:
    """返回基本信息里仍为模板示例的字段名（按字段表顺序）。"""
    if not isinstance(basics, dict):
        return []
    return [str(key) for key, value in basics.items() if is_placeholder_value(str(key), value)]


def basics_look_like_template(basics: dict[str, Any] | None) -> bool:
    """整份基本信息是否还是模板（用于提示词里判断「要不要先补齐」）。"""
    if not isinstance(basics, dict):
        return True
    name = basics.get("name")
    if is_placeholder_value("name", name):
        return True
    # 姓名是真的，但联系方式仍是示例：至少两个占位字段才提示，避免单字段误报。
    return len(placeholder_fields(basics)) >= 2
