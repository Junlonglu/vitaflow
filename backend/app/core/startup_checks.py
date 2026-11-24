"""启动自检：配置之间的**关系**必须成立，否则拒绝启动。

单项合法性由 pydantic 的字段校验保证（类型、正数），JWT/BYOK 由各自的校验器
负责（``validate_jwt_secret`` / ``validate_encryption_config``，在 ``main`` 里
先跑）。这里只管「两个值凑在一起才成立」的约束——它们各自都合法，组合起来
却会在运行时互相踩脚，而且往往只在生产暴露：

* 后端代理读超时比 Node 回合总时长还短 → 回合被代理先掐断，前端只看到断流；
* 只声明了单张图片上限、没声明一次消息的附图总量 → 用户消息已落库之后才 413；
* 连接池超时还是 30s 默认值 → 池满时请求堆在池外，连健康检查都排队。

**为什么分两档**：

* 静态检查（只读本进程的配置）在 production 环境下失败即拒绝启动；
* 跨服务检查（Node 侧的回合时长、body 上限、provider 模式）只在**能连上**时
  判定。容器编排里 ai-service 可能比 backend 晚几秒就绪，为此让后端拒绝启动，
  等于把「慢启动」升级成「起不来」；但一旦连上就必须自洽——那是宁可拒绝
  启动也不带病运行的那一类问题。
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.core import contracts
from app.core.config import Settings, settings as global_settings

logger = logging.getLogger(__name__)

# 拿不到连接就快速失败：池外排队的请求等 30s 只会叠成雪崩。
MAX_POOL_TIMEOUT_SECONDS = 10
# 探活预算：启动路径上不能等太久，连不上就记 warning 继续。
PROBE_TIMEOUT = httpx.Timeout(connect=2, read=3, write=3, pool=2)


class StartupContractError(RuntimeError):
    """配置组合不成立。消息里逐条列出原因，而不是只报第一条。"""


def static_problems(cfg: Settings) -> list[str]:
    """不依赖网络、只读本进程配置的问题（空表示通过）。

    故意设计成纯函数：``Settings`` 可以随手构造，单测不需要起服务，也不会
    顺带触发任何模块级单例。
    """
    problems: list[str] = []

    # ---- 超时链：Node 总时长 ≤ 后端代理读 ≤ Nginx 读。
    problems.extend(
        contracts.timeout_chain_problems(
            cfg.ai_turn_timeout_ms,
            cfg.ai_proxy_read_timeout_ms,
            cfg.nginx_proxy_read_timeout_ms,
        )
    )

    # ---- 对话请求体预算：附图总量 base64 后必须还装得进 Node 的 body 上限，
    # 而 Node 的上限又必须严于 Nginx（内层先拒才能给 JSON 错误）。
    problems.extend(contracts.chat_body_budget_problems())

    # ---- 服务票据：有效期必须覆盖一个完整回合。短了就会出现「对话成功但没扣点」，
    # 因为收尾的 usage 回调拿着一张过期票据回来。
    problems.extend(
        contracts.ticket_ttl_problems(contracts.SERVICE_TICKET_TTL_SECONDS, cfg.ai_turn_timeout_ms)
    )

    # ---- 连接池：必须能快速失败。等 30s 是 SQLAlchemy 的默认值，也是最糟的
    # 那一档——池满时请求全部堆在池外，健康检查也跟着排队。
    if cfg.db_pool_timeout > MAX_POOL_TIMEOUT_SECONDS:
        problems.append(
            f"DB_POOL_TIMEOUT={cfg.db_pool_timeout}s 太长，池满时请求会堆在池外并拖垮"
            f"健康检查；建议 ≤ {MAX_POOL_TIMEOUT_SECONDS}s（默认 "
            f"{contracts.DB_POOL_TIMEOUT_SECONDS}s）"
        )
    if cfg.db_pool_size + cfg.db_max_overflow < 5:
        problems.append(
            "连接池上限不足 5，正常并发会立刻排队，检查 DB_POOL_SIZE / DB_MAX_OVERFLOW"
        )

    # ---- 私有对象不能走公开地址：对话图片是用户内容，走 public 模式等于把
    # 「登录 + 签名」两条校验一起绕开，对象名虽然含 uuid 但一旦外泄就永久可读。
    if (cfg.storage_public_url_mode or "").strip().lower() == "public":
        message = (
            "STORAGE_PUBLIC_URL_MODE=public：对话图片会以对象存储的公开地址直接下发，"
            "绕过登录校验与签名 URL，等于任何人都能读用户上传的图片"
        )
        if cfg.is_production:
            problems.append(message)
        else:
            logger.warning("配置组合可疑：%s", message)

    # ---- 渲染基址：只给服务端 Chromium 用，必须是绝对 http(s) 地址。
    # 回环地址在「Chromium 与 uvicorn 同容器」时是正确的（docker-compose 默认），
    # 所以只在拆开部署时才是问题——这里无法判断拓扑，只提示，不拦。
    parsed = urlparse(cfg.pdf_base_url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        problems.append(
            f"PDF_BASE_URL={cfg.pdf_base_url!r} 不是绝对的 http(s) 地址，"
            "Chromium 无法据此加载头像与字体，导出的 PDF 会缺图"
        )
    elif cfg.is_production and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "PDF_BASE_URL 指向回环地址（%s）：仅当 Chromium 与后端同容器时才正确；"
            "若渲染进程在别的容器/机器上，请改成它也够得着的地址",
            cfg.pdf_base_url,
        )

    return problems


async def ai_service_problems(cfg: Settings) -> tuple[list[str], str]:
    """向 AI 服务要一份契约自述并核对。

    返回 ``(problems, unreachable_reason)``：连不上时 problems 为空、原因非空，
    由调用方决定是 warning 还是失败（默认 warning，见模块说明）。
    """
    url = f"{cfg.ai_service_url.rstrip('/')}/v1/contracts"
    headers = {"x-ai-service-token": cfg.ai_service_token} if cfg.ai_service_token else {}
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return [], f"连不上 AI 服务 {url}（{exc.__class__.__name__}），跳过跨服务契约核对"

    if response.status_code == 401:
        return [], (
            f"AI 服务拒绝了本进程的令牌（HTTP 401，{url}）——"
            "AI_SERVICE_TOKEN 两边不一致，对话链路会全量 401"
        )
    if response.status_code != 200:
        return [], f"AI 服务 /v1/contracts 返回 {response.status_code}，跳过跨服务契约核对"

    try:
        payload = response.json()
    except ValueError:
        return [], "AI 服务 /v1/contracts 返回的不是 JSON，跳过跨服务契约核对"

    problems: list[str] = []
    turn_ms = int(payload.get("turn_timeout_ms") or 0)
    body_bytes = int(payload.get("body_limit_bytes") or 0)

    # Node 侧的实际回合时长可能被 env 调过，以它为准再算一次链。
    problems.extend(
        contracts.timeout_chain_problems(
            turn_ms,
            cfg.ai_proxy_read_timeout_ms,
            cfg.nginx_proxy_read_timeout_ms,
        )
    )
    if body_bytes:
        problems.extend(contracts.chat_body_budget_problems(body_max_bytes=body_bytes))
    else:
        problems.append("AI 服务未上报 body 上限（AI_BODY_LIMIT_MB），无法核对附图预算")

    if cfg.is_production and (payload.get("provider") or "faux") == "faux":
        problems.append(
            "AI 服务仍在 faux 模式（PI_PROVIDER=faux）：生产环境所有对话都会返回"
            "假数据，请配置真实 provider 后重启 ai-service"
        )
    return problems, ""


async def run_startup_checks(cfg: Settings | None = None) -> None:
    """执行全部自检。静态问题在 production 下拒绝启动；跨服务问题一律拒绝。

    非 production 的静态问题只记 warning：本地开发常见「还没配 SMTP / 还没换
    JWT」的状态，拦住启动只会让人去改环境而不去改代码。
    """
    cfg = cfg or global_settings
    problems = static_problems(cfg)

    remote, unreachable = await ai_service_problems(cfg)
    if unreachable:
        logger.warning("%s", unreachable)
    problems.extend(remote)
    # 静态档用本进程配置算一遍链，远端档用 Node 自报的时长再算一遍；两边配置
    # 一致时结论相同，会得到两条一模一样的文案。去重后「共 N 项」才是真数。
    problems = list(dict.fromkeys(problems))

    if not problems:
        logger.info("启动自检通过（配置组合自洽）")
        return

    if not cfg.is_production:
        # 开发环境：跨服务问题依然要吵（否则它就是永远不会被看见的 warning），
        # 本地配置问题只记 warning。
        if remote:
            raise StartupContractError(_format(remote))
        for item in problems:
            logger.warning("配置组合可疑：%s", item)
        return

    raise StartupContractError(_format(problems))


def _format(problems: list[str]) -> str:
    lines = "\n".join(f"  - {item}" for item in problems)
    return f"配置组合不成立，拒绝启动（共 {len(problems)} 项）：\n{lines}"
