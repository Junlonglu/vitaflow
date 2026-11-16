"""真实客户端 IP 解析。

代理头（``X-Forwarded-For`` 等）是**客户端可以自己带**的，直接取首段等于把
限频、审计、兑换码 IP 唯一性全部交给调用方决定。这里的规则是：

1. 直连对端不是受信代理 → 一律用 TCP 对端地址，忽略所有代理头；
2. 直连对端是受信代理 → 在代理链里**从右往左**跳过受信代理，取第一个非受信
   地址（这才是最靠近我们、且不可被更上游伪造的那一跳）；
3. 整条链都是受信地址 → 退回 TCP 对端。

受信网段由 ``TRUSTED_PROXY_NETWORKS`` 配置，默认覆盖回环 + Docker 私网。
前端 Nginx（``frontend/nginx.conf``）同时用 ``$remote_addr`` **覆盖**而非追加
``X-Forwarded-For``，两层保险：即使这里判错，链上也只有一个值。
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

from app.core.config import settings

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_TRUSTED_PROXY_NETWORKS = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

# 代理头按可信度排序：XFF 是标准链路，其余多为单一跳数。
_PROXY_HEADERS = ("x-real-ip", "cf-connecting-ip", "x-client-ip")


@lru_cache(maxsize=8)
def _parse_networks(raw: str) -> tuple[IpNetwork, ...]:
    """把逗号分隔的网段解析成元组；非法项直接跳过（配置错不该让请求 500）。"""
    networks: list[IpNetwork] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _as_ip(value: str):
    try:
        # 去掉 IPv6 zone id（fe80::1%eth0）和可能的端口/括号
        return ipaddress.ip_address(value.split("%", 1)[0].strip())
    except ValueError:
        return None


def _is_trusted(value: str, networks: tuple[IpNetwork, ...]) -> bool:
    address = _as_ip(value)
    if address is None:
        return False
    return any(address in network for network in networks)


def _candidate_addresses(request: Request) -> list[str]:
    """按优先级列出可能的客户端地址（XFF 从右往左，其余头原序）。"""
    candidates: list[str] = []
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [item.strip() for item in forwarded.split(",") if item.strip()]
        candidates.extend(reversed(hops))
    for header in _PROXY_HEADERS:
        value = request.headers.get(header)
        if value:
            candidates.append(value.strip())
    return candidates


def get_client_ip(request: Request) -> str:
    """返回最可信的客户端 IP；无法确定时返回 TCP 对端地址。"""
    direct = (request.client.host if request.client and request.client.host else "").strip()
    if not direct:
        return ""

    raw_networks = settings.trusted_proxy_networks.strip() or DEFAULT_TRUSTED_PROXY_NETWORKS
    networks = _parse_networks(raw_networks)
    if not _is_trusted(direct, networks):
        # 直连方不在受信网段：它写的任何代理头都不作数。
        return direct[:64]

    for candidate in _candidate_addresses(request):
        if not _is_trusted(candidate, networks):
            return candidate[:64]
    return direct[:64]
