"""Regression: every ``AppException`` must carry an explicit status code.

``AppException`` 默认 ``code=500``，只表示「约定之外的意外」。但历史上大量业务
分支直接 ``raise AppException("请先在管理后台配置 AI 模型")`` 就结束了，前端和其他
调用方拿到的永远是 500，分不清是「服务没配好」（503）、「上游挂了」（502）还是
「提交内容有问题」（400）——只能靠解析中文文案猜。

这里用 AST 静态扫描 ``backend/app`` 下所有 ``AppException(...)`` 调用，要求每处都
显式给出状态码，且落在 HTTP 语义区间内。这样新增分支时不会悄悄退回 500。
"""
from __future__ import annotations

import ast
import pathlib
import unittest


APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
# 允许的语义码：4xx 客户端/状态类，5xx 服务端类。其余（如 200、302）不属于异常。
ALLOWED_CODES = {400, 401, 402, 403, 404, 405, 409, 410, 413, 415, 422, 429, 500, 502, 503, 504}


def _iter_calls():
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_app_exception = (
                (isinstance(func, ast.Name) and func.id == "AppException")
                or (isinstance(func, ast.Attribute) and func.attr == "AppException")
            )
            if is_app_exception:
                yield path, node, source


def _code_of(call: ast.Call) -> int | None:
    keyword = next((kw for kw in call.keywords if kw.arg == "code"), None)
    node = keyword.value if keyword is not None else (call.args[1] if len(call.args) >= 2 else None)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


class AppExceptionCodeTest(unittest.TestCase):
    def test_every_raise_declares_a_status_code(self):
        missing = []
        for path, call, source in _iter_calls():
            if _code_of(call) is None:
                line = ast.get_source_segment(source, call) or ""
                missing.append(f"{path.relative_to(APP_ROOT.parent)}:{call.lineno} {line[:60]}")
        self.assertEqual(missing, [], "以下 AppException 没有显式状态码：\n" + "\n".join(missing))

    def test_status_codes_are_http_semantic(self):
        bad = []
        for path, call, _ in _iter_calls():
            code = _code_of(call)
            if code is not None and code not in ALLOWED_CODES:
                bad.append(f"{path.relative_to(APP_ROOT.parent)}:{call.lineno} -> {code}")
        self.assertEqual(bad, [], "以下 AppException 的状态码不在约定集合内：\n" + "\n".join(bad))

    def test_scanner_sees_the_known_volume(self):
        # 防止扫描器本身失效（比如改了函数名）导致两条断言空跑。
        self.assertGreater(len(list(_iter_calls())), 150)


if __name__ == "__main__":
    unittest.main()
