"""PDF 导出的出站请求边界。

简历里的 ``basics.avatar`` 是用户可写字符串，渲染 PDF 时后端会替用户取那张图。
如果不设白名单，一份简历就能让服务器去打内网服务或云元数据。这组测试锁定
「哪些 URL 允许取、哪些必须挡」以及「挡下来的是空白图，不是整份导出失败」。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import settings
from app.services import pdf_export_service as pdf


def _url_under_pdf_base(path: str) -> str:
    return f"{settings.pdf_base_url.rstrip('/')}/api/files/{path}"


class RenderableAssetTest(unittest.TestCase):
    def test_non_http_schemes_are_rejected(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/a.png",
            "gopher://example.com:6379/_INFO",
        ):
            with self.subTest(url=url):
                self.assertFalse(pdf._is_renderable_asset(url))

    def test_loopback_and_private_addresses_are_rejected(self) -> None:
        for url in (
            "http://127.0.0.1:3306/",
            "http://localhost:8000/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.1.2.3/internal.png",
            "http://[::1]/x.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(pdf._is_renderable_asset(url))

    def test_private_network_switch_does_not_loosen_rendering(self) -> None:
        # BYOK_ALLOW_PRIVATE_NETWORK 是给自建模型网关用的，不该顺带放开简历外链。
        with patch.object(settings, "byok_allow_private_network", True):
            self.assertFalse(pdf._is_renderable_asset("http://127.0.0.1:9000/x.png"))

    def test_inline_and_public_urls_are_allowed(self) -> None:
        self.assertTrue(pdf._is_renderable_asset("data:image/png;base64,iVBORw0KGgo="))
        with patch.object(pdf, "probe_public_url", return_value=(True, "")):
            self.assertTrue(pdf._is_renderable_asset("https://cdn.example.com/a.png"))

    def test_domain_resolving_inward_is_rejected(self) -> None:
        with patch.object(pdf, "probe_public_url", return_value=(False, "指向内网")):
            self.assertFalse(pdf._is_renderable_asset("https://evil.example.com/a.png"))

    def test_malformed_url_is_rejected(self) -> None:
        self.assertFalse(pdf._is_renderable_asset("http://[::1/a.png"))


class _NeverFetched:
    def __call__(self, *_args, **_kwargs):  # pragma: no cover - 命中即失败
        raise AssertionError("被拒绝的资源不该再发起真实请求")


class UrlFetcherTest(unittest.TestCase):
    def test_uploaded_object_is_read_from_storage(self) -> None:
        url = _url_under_pdf_base("avatars/a.png")
        with (
            patch.object(pdf, "read_uploaded_file", return_value=(b"img", "image/png")) as read,
            patch.object(pdf, "_get_weasyprint", return_value=(None, _NeverFetched())),
        ):
            result = pdf._pdf_url_fetcher(url)

        read.assert_called_once_with("avatars/a.png")
        self.assertEqual(result["string"], b"img")
        self.assertEqual(result["mime_type"], "image/png")

    def test_failed_storage_read_does_not_fall_back_to_network(self) -> None:
        # 关键回归：pdf_base_url 常是内网地址，读失败就发请求等于把 SSRF 放回来。
        with (
            patch.object(pdf, "read_uploaded_file", side_effect=RuntimeError("boom")),
            patch.object(pdf, "_get_weasyprint", return_value=(None, _NeverFetched())),
        ):
            result = pdf._pdf_url_fetcher(_url_under_pdf_base("avatars/a.png"))

        self.assertEqual(result["string"], pdf._BLANK_IMAGE)

    def test_rejected_url_returns_a_blank_image(self) -> None:
        with (
            patch.object(pdf, "_get_weasyprint", return_value=(None, _NeverFetched())),
        ):
            result = pdf._pdf_url_fetcher("file:///etc/passwd")

        self.assertEqual(result["string"], pdf._BLANK_IMAGE)


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.calls: list[str] = []

    def continue_(self) -> None:
        self.calls.append("continue")

    def abort(self) -> None:
        self.calls.append("abort")

    def fulfill(self, **_kwargs) -> None:
        self.calls.append("fulfill")


class FulfillRouteTest(unittest.TestCase):
    def test_private_url_is_aborted(self) -> None:
        route = _FakeRoute("http://169.254.169.254/latest/meta-data/")

        pdf._fulfill_uploaded_file_route(route)

        self.assertEqual(route.calls, ["abort"])

    def test_inline_url_passes_through(self) -> None:
        route = _FakeRoute("data:image/png;base64,iVBORw0KGgo=")

        pdf._fulfill_uploaded_file_route(route)

        self.assertEqual(route.calls, ["continue"])

    def test_uploaded_object_is_fulfilled_from_storage(self) -> None:
        route = _FakeRoute(_url_under_pdf_base("avatars/a.png"))
        with patch.object(pdf, "read_uploaded_file", return_value=(b"img", "image/png")):
            pdf._fulfill_uploaded_file_route(route)

        self.assertEqual(route.calls, ["fulfill"])

    def test_failed_storage_read_aborts(self) -> None:
        route = _FakeRoute(_url_under_pdf_base("avatars/a.png"))
        with patch.object(pdf, "read_uploaded_file", side_effect=RuntimeError("boom")):
            pdf._fulfill_uploaded_file_route(route)

        self.assertEqual(route.calls, ["abort"], "读不到就别让浏览器自己去取")


if __name__ == "__main__":
    unittest.main()
