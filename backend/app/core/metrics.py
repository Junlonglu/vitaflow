"""进程内运行指标：回答「现在忙不忙、卡在哪」，不回答「历史趋势」。

**为什么不用 Prometheus 客户端**：这套系统是单机部署，出问题时运维只有
SSH 和 curl。多一个 exporter、一套抓取配置、一个 TSDB，换来的只是把同一组
数字搬到一个需要额外维护的地方。这里把最关键的几项做成一个只读接口，`curl`
一次就能看到「池子在等、线程全占满、PDF 排了 7 个、有 3 个任务租约过期」。

**口径**（每一项都写清是窗口值还是瞬时值，否则数字会被读错）：

* `db_pool`：等待 = 申请连接 → 拿到连接的耗时，冷启动那一次包含建连；
* `threads`：anyio 默认线程池的占用，同步端点（含渲染）都在这里排队；
* `pdf`：正在渲染 / 正在等锁的数量，瞬时值；
* `tasks` / `billing`：进数据库算，慢但准，只给管理端偶尔拉一次。

这个模块**不 import 任何 app 内模块**：`database.py` 要 import 它，反向依赖
会成环。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from sqlalchemy.pool import QueuePool

# 保留最近多少次采样：够算 p95，又不至于把指标本身变成内存问题。
SAMPLE_WINDOW = 512


def _percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * ratio))))
    return sorted_values[index]


@dataclass
class LatencyTrack:
    """一段耗时分布的滚动快照（次数 / 均值 / p95 / 最大，单位毫秒）。"""

    label: str
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLE_WINDOW))
    count: int = 0
    total: float = 0.0
    peak: float = 0.0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, seconds: float) -> None:
        with self._lock:
            self.samples.append(seconds)
            self.count += 1
            self.total += seconds
            if seconds > self.peak:
                self.peak = seconds

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            values = sorted(self.samples)
            count = self.count
            total = self.total
            peak = self.peak
        return {
            "label": self.label,
            # 采样窗口内保留的样本数：p95 只代表这段时间，不是全生命周期
            "samples": len(values),
            "count": count,
            "avg_ms": round(total / count * 1000, 2) if count else 0.0,
            "p95_ms": round(_percentile(values, 0.95) * 1000, 2),
            "max_ms": round(peak * 1000, 2),
        }


# 连接池 checkout 等待：池满时请求堆在这里，也是「页面偶发变慢」最常见的原因。
pool_wait = LatencyTrack("db_pool_wait")
# PDF 渲染等锁：同一份简历并发导出会串行，排队长度比耗时更值得看。
pdf_wait = LatencyTrack("pdf_render_wait")

_pdf_state = {"active": 0, "waiting": 0}
_pdf_lock = Lock()

_counters: dict[str, int] = {}
_counters_lock = Lock()


def increment(name: str, amount: int = 1) -> None:
    """累加一个进程内计数器（如「未配置渲染器回退」次数）。"""
    with _counters_lock:
        _counters[name] = _counters.get(name, 0) + amount


def counters_snapshot() -> dict[str, int]:
    with _counters_lock:
        return dict(_counters)


def pdf_render_waiting() -> None:
    with _pdf_lock:
        _pdf_state["waiting"] += 1


def pdf_render_started() -> None:
    """等到锁了：从「等」变成「跑」。"""
    with _pdf_lock:
        _pdf_state["waiting"] = max(0, _pdf_state["waiting"] - 1)
        _pdf_state["active"] += 1


def pdf_render_finished() -> None:
    with _pdf_lock:
        _pdf_state["active"] = max(0, _pdf_state["active"] - 1)


def pdf_snapshot() -> dict[str, Any]:
    with _pdf_lock:
        state = dict(_pdf_state)
    state["wait"] = pdf_wait.snapshot()
    return state


class MeasuredQueuePool(QueuePool):
    """`QueuePool` + 一次 checkout 等待采样。

    `_do_get()` 是 SQLAlchemy 的内部方法，但它是「申请连接 → 拿到连接」之间
    唯一可控的接缝：所有连接池度量工具都挂在这个位置。这里只包一层计时，
    不改任何行为——取不到连接照样按 `pool_timeout` 抛，异常原样透出。
    """

    def _do_get(self):  # type: ignore[override]
        started = time.perf_counter()
        try:
            return super()._do_get()
        finally:
            pool_wait.record(time.perf_counter() - started)


def pool_snapshot(pool: Any) -> dict[str, Any]:
    """连接池瞬时水位 + checkout 等待分布。

    `overflow` > 0 说明已经在用超出 `pool_size` 的临时连接；持续如此就该调大
    `DB_POOL_SIZE`，而不是等 `pool_timeout` 到点后开始 500。
    """
    snapshot: dict[str, Any] = {}
    try:
        # 形如 "Pool size: 10  Connections in pool: 0  Current Overflow: 2 ..."
        snapshot["status"] = str(pool.status())
    except Exception:  # noqa: BLE001 - 指标接口不能因为拿不到水位就整体 500
        snapshot["status"] = "unavailable"
    for name in ("size", "checkedout", "checkedin"):
        getter = getattr(pool, name, None)
        if callable(getter):
            try:
                snapshot[name] = int(getter())
            except Exception:  # noqa: BLE001
                pass
    snapshot["wait"] = pool_wait.snapshot()
    return snapshot


def thread_snapshot() -> dict[str, Any]:
    """anyio 默认线程池占用。同步端点、`db.rollback()` 之外的阻塞调用都排这里。

    必须在事件循环里调用；没有循环时（脚本、单测）返回 `available=False`，
    而不是抛 `NoEventLoopError`。
    """
    try:
        from anyio.to_thread import current_default_thread_limiter

        limiter = current_default_thread_limiter()
    except Exception:  # noqa: BLE001 - 无事件循环 / 版本差异都不该让接口失败
        return {"available": False}
    total = int(getattr(limiter, "total_tokens", 0) or 0)
    borrowed = int(getattr(limiter, "borrowed_tokens", 0) or 0)
    return {
        "available": True,
        "total": total,
        "borrowed": borrowed,
        "idle": max(total - borrowed, 0),
    }
