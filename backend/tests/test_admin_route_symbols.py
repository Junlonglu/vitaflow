"""Regression: every registered API endpoint must be able to resolve its globals.

拆分 ``api/admin.py`` 时曾多次漏拷工具函数的导入（``_with_preview``、
``admin_task_model_info``、``point_number``、``normalize_token_usage``、
``sanitize_rich_text_html``）。这些名字只在列表有数据、或某个按钮被点到时才
执行得到，空库下接口照样 200，一旦有数据就直接 500，靠手工点页面很难覆盖。

这里用字节码反射静态检查所有路由函数（含内部推导式/闭包）引用到的全局名，
能在不发请求、不连数据库的前提下覆盖所有分支。
"""
from __future__ import annotations

import builtins
import dis
import importlib
import pkgutil
import types
import unittest

from fastapi import APIRouter

import app.api as api_pkg


def _iter_modules(package):
    yield package
    for info in pkgutil.iter_modules(package.__path__):
        name = f"{package.__name__}.{info.name}"
        module = importlib.import_module(name)
        if info.ispkg:
            yield from _iter_modules(module)
        else:
            yield module


def _iter_codes(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _iter_codes(const)


def _unresolved_globals(endpoint, module) -> list[str]:
    globals_ = vars(module)
    missing: set[str] = set()
    for code in _iter_codes(endpoint.__code__):
        for instr in dis.get_instructions(code):
            if instr.opname not in ("LOAD_GLOBAL", "LOAD_NAME"):
                continue
            name = instr.argval
            if name not in globals_ and not hasattr(builtins, name):
                missing.add(name)
    return sorted(missing)


class ApiRouteSymbolTest(unittest.TestCase):
    def test_all_endpoints_resolve_their_globals(self):
        problems: list[str] = []
        checked = 0
        for module in _iter_modules(api_pkg):
            for value in vars(module).values():
                if not isinstance(value, APIRouter):
                    continue
                for route in value.routes:
                    endpoint = getattr(route, "endpoint", None)
                    if endpoint is None or not hasattr(endpoint, "__code__"):
                        continue
                    checked += 1
                    missing = _unresolved_globals(endpoint, module)
                    if missing:
                        problems.append(
                            f"{module.__name__}.{endpoint.__name__} "
                            f"({getattr(route, 'path', '?')}): {', '.join(missing)}"
                        )
        self.assertGreater(checked, 50, "没有扫描到足够的路由，检查方式可能失效了")
        self.assertEqual([], problems, "存在未解析的全局名（漏导入）:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
