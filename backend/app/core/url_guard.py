"""BYOK 接入地址的安全校验（SSRF 防护）。

用户填的 base_url 会被服务端主动请求（探活）并转交给 AI 服务调用模型，
本质上是一个「用户可控的出站 URL」。默认拒绝回环、私网、链路本地与云元数据
地址；部署方若确实要接本机 Ollama / 内网网关，可显式打开
``BYOK_ALLOW_PRIVATE_NETWORK`` 自行承担风险。

拆成两级：``literal_public_url`` 不联网（schema 校验、每轮对话解析都用它），
``probe_public_url`` 会做 DNS 解析（只在用户点「测试连接」时跑一次）。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings

# 常见元数据服务与本机别名，即使解析到公网 IP 也不放行。
_BLOCKED_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "169.254.169.254",
})


def _public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def literal_public_url(url: str, *, allow_private: bool | None = None) -> tuple[bool, str]:
    """只检查字面量，不做 DNS 查询。返回 (是否允许, 拒绝原因)。

    ``allow_private`` 为 None 时跟随 ``BYOK_ALLOW_PRIVATE_NETWORK``；PDF 导出等
    「用户内容触发的出站请求」传 False，不受该 BYOK 开关影响。
    """
    if allow_private is None:
        allow_private = settings.byok_allow_private_network
    if allow_private:
        return True, ""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return False, "接入地址必须是 http:// 或 https://"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False, "接入地址缺少主机名"
    if host in _BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".internal"):
        return False, "出于安全考虑，不允许接入本机或内网地址"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 域名：DNS 解析交给 probe_public_url，避免每次对话都做一次网络查询。
        return True, ""
    if not _public_ip(ip):
        return False, "出于安全考虑，不允许接入私网或保留地址"
    return True, ""


def probe_public_url(url: str, *, allow_private: bool | None = None) -> tuple[bool, str]:
    """解析 DNS 后再校验，防域名指向内网；解析失败一律拒绝。"""
    if allow_private is None:
        allow_private = settings.byok_allow_private_network
    if allow_private:
        return True, ""
    ok, reason = literal_public_url(url, allow_private=allow_private)
    if not ok:
        return ok, reason
    parsed = urlparse(url.strip())
    host = parsed.hostname or ""
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        return False, f"域名 {host} 解析失败，请检查接入地址"
    if not infos:
        return False, f"域名 {host} 解析失败，请检查接入地址"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, "接入地址解析结果异常"
        if not _public_ip(ip):
            return False, "出于安全考虑，不允许接入指向内网的域名"
    return True, ""
