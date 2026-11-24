"""对账：把「用户消息 / 助手消息 / 任务 / 点数流水」四张表按回合对齐。

M5 的不变量是「任何一次故障后，不看代码也能回答：这轮谁扣了多少钱、交没交
付、现在有没有卡住」。这四张表分别记着这四件事，但它们由三次独立提交写入
（用户消息、助手消息、计费各一个事务，见 RF-040），所以只读任何一张表都得
不出结论。这里的做法是：**以用户消息为行轴，左连接其余三张**。

四种残缺态各有名字，出问题时直接看清单，不用再拼 SQL：

* `missing_reply` —— 有提问、没回复（清扫器会给它补一条 failed 占位任务）；
* `missing_task` —— 有提问、有回复、没有账（漏计费）；
* `unbilled` —— 交付了、结算没落地（`status=success` 且 `billing_status=unbilled`）；
* `billing_failed` —— 结算过程自身抛错，需要人工核；

外加两个不依赖回合的检查：每个用户的「流水净额 == 余额」，以及每条任务的
「扣点金额 == 流水金额」。这三处任何一处对不上，都说明有人绕过了唯一写入口。

**成本说明**：这些都是管理端诊断接口，按窗口（默认 24 小时）+ 上限（默认 200 行）
取数，不追求跑大表。余额比对要扫全表——它本来就该是全量的，抽样等于自欺。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.ai_task import AiTask
from app.services.ai_task_service import BILLING_BILLED, BILLING_NONE, TASK_SUCCESS
from app.services.flow_points_service import point_number

# 回合级账本：一行一个用户消息。所有聚合都按 `turn_id` 对齐——任务行数不等于
# 回合数（一个回合可能命中多个模型），这是 RF-018 的核心口径问题。
TURN_LEDGER_SQL = text(
    """
    SELECT
        u.id                            AS turn_id,
        u.session_id                    AS session_id,
        u.user_id                       AS user_id,
        u.create_time                   AS user_time,
        (SELECT COUNT(*) FROM ai_chat_messages a
          WHERE a.session_id = u.session_id
            AND a.role = 'assistant'
            AND a.id > u.id)            AS reply_count,
        COALESCE(t.task_count, 0)           AS task_count,
        COALESCE(t.pending_count, 0)        AS pending_count,
        COALESCE(t.success_count, 0)        AS success_count,
        COALESCE(t.failed_count, 0)         AS failed_count,
        COALESCE(t.unbilled_count, 0)       AS unbilled_count,
        COALESCE(t.billing_failed_count, 0) AS billing_failed_count,
        COALESCE(t.points_used, 0)          AS points_used,
        COALESCE(p.points_delta, 0)         AS points_delta
    FROM ai_chat_messages u
    LEFT JOIN (
        SELECT turn_id,
               COUNT(*) AS task_count,
               SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
               SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END) AS failed_count,
               SUM(CASE WHEN billing_status = 'unbilled' THEN 1 ELSE 0 END) AS unbilled_count,
               SUM(CASE WHEN billing_status = 'failed'   THEN 1 ELSE 0 END) AS billing_failed_count,
               SUM(points_used) AS points_used
        FROM ai_tasks
        WHERE turn_id IS NOT NULL AND create_time >= :since
        GROUP BY turn_id
    ) t ON t.turn_id = u.id
    LEFT JOIN (
        SELECT tk.turn_id AS turn_id, SUM(f.points_delta) AS points_delta
        FROM flow_point_transactions f
        JOIN ai_tasks tk ON tk.id = f.task_id
        WHERE f.task_id IS NOT NULL AND f.create_time >= :since
        GROUP BY tk.turn_id
    ) p ON p.turn_id = u.id
    WHERE u.role = 'user' AND u.create_time >= :since
    ORDER BY u.id DESC
    LIMIT :limit
    """
)

# 余额 = 流水净额。任何绕过 add_point_transaction 的写入都会让这条不成立。
BALANCE_MISMATCH_SQL = text(
    """
    SELECT u.id AS user_id, u.flow_points AS balance,
           COALESCE(SUM(f.points_delta), 0) AS ledger
    FROM users u
    LEFT JOIN flow_point_transactions f ON f.user_id = u.id
    GROUP BY u.id, u.flow_points
    HAVING u.flow_points <> COALESCE(SUM(f.points_delta), 0)
    ORDER BY u.id
    LIMIT :limit
    """
)

# 漏计费：有助手回复，但被回复的那条用户消息没有任何任务行。
MISSING_TASK_SQL = text(
    """
    SELECT a.id AS message_id, a.session_id AS session_id, a.user_id AS user_id,
           a.create_time AS create_time,
           (SELECT MAX(u.id) FROM ai_chat_messages u
             WHERE u.session_id = a.session_id
               AND u.role = 'user'
               AND u.id < a.id) AS turn_id
    FROM ai_chat_messages a
    WHERE a.role = 'assistant' AND a.create_time >= :since
    ORDER BY a.id DESC
    LIMIT :limit
    """
)

# 幽灵计费：任务挂在一条不存在的消息上（消息被删、或 turn_id 拼错）。
GHOST_TASK_SQL = text(
    """
    SELECT t.id AS task_id, t.turn_id AS turn_id, t.user_id AS user_id,
           t.task_type AS task_type, t.create_time AS create_time
    FROM ai_tasks t
    LEFT JOIN ai_chat_messages m ON m.id = t.turn_id
    WHERE t.turn_id IS NOT NULL AND m.id IS NULL
    ORDER BY t.id DESC
    LIMIT :limit
    """
)

PENDING_TASK_SQL = text(
    """
    SELECT id, turn_id, user_id, task_type, create_time, expires_at
    FROM ai_tasks
    WHERE status = 'pending'
    ORDER BY id
    LIMIT :limit
    """
)

# 超过这个秒数还没结束，就说明「正在生成」已经不太可能是真的了。
LONG_RUNNING_SECONDS = 300


def _ledger_state(row: dict[str, Any]) -> str:
    """一行回合账本的结论。顺序即优先级：先看有没有交付，再看有没有记账。"""
    if int(row["reply_count"] or 0) == 0:
        return "missing_reply"
    if int(row["task_count"] or 0) == 0:
        return "missing_task"
    if int(row["success_count"] or 0) > 0 and int(row["unbilled_count"] or 0) > 0:
        return "unbilled"
    if int(row["billing_failed_count"] or 0) > 0:
        return "billing_failed"
    if int(row["pending_count"] or 0) > 0:
        return "pending"
    return "ok"


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def _as_datetime(value: Any) -> datetime | None:
    """把结果行里的时间统一成 datetime。

    手写 `text()` 查询没有类型信息：pymysql 会按游标描述把 DATETIME 转成
    datetime，而 SQLite 原样返回字符串。不统一的话，同一段代码在单测里就会
    因为「字符串没有 isoformat」而炸。
    """
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    moment = _as_datetime(value)
    return moment.isoformat(timespec="seconds") if moment else None


def turn_ledger(
    db: Session,
    *,
    since: datetime,
    limit: int = 200,
) -> dict[str, Any]:
    """回合级账本 + 四种残缺态的计数。

    `points_used` 是任务声明的应收点数，`points_delta` 是流水里实际动的金额
    （负数）。两者绝对值必须相等，否则说明有点数没经流水就动了。
    """
    rows = [_row_dict(row) for row in db.execute(TURN_LEDGER_SQL, {"since": since, "limit": limit})]
    summary: dict[str, int] = {"total": len(rows)}
    ledger_rows: list[dict[str, Any]] = []
    for row in rows:
        state = _ledger_state(row)
        summary[state] = summary.get(state, 0) + 1
        points_used = point_number(row["points_used"])
        points_delta = point_number(row["points_delta"])
        ledger_rows.append(
            {
                "turn_id": int(row["turn_id"]),
                "session_id": int(row["session_id"]),
                "user_id": int(row["user_id"]),
                "time": _iso(row["user_time"]),
                "state": state,
                "replies": int(row["reply_count"] or 0),
                "tasks": int(row["task_count"] or 0),
                "tasks_pending": int(row["pending_count"] or 0),
                "tasks_failed": int(row["failed_count"] or 0),
                "points_used": points_used,
                "points_delta": points_delta,
                # 声明该扣的和实际扣的必须对得上；0 表示这条回合自己的账是平的。
                "points_gap": round(points_used + points_delta, 2),
            }
        )
    return {"rows": ledger_rows, "summary": summary}


def balance_mismatches(db: Session, *, limit: int = 50) -> dict[str, Any]:
    """余额与流水净额对不上的用户。

    全表比对：这条检查的价值全在「不漏」，抽样等于自欺。行数上限只截展示。
    """
    rows = [_row_dict(row) for row in db.execute(BALANCE_MISMATCH_SQL, {"limit": limit})]
    return {
        "mismatched": len(rows),
        "users": [
            {
                "user_id": int(row["user_id"]),
                "balance": point_number(row["balance"]),
                "ledger": point_number(row["ledger"]),
                "gap": round(point_number(row["balance"]) - point_number(row["ledger"]), 2),
            }
            for row in rows
        ],
    }


def missing_billing(
    db: Session,
    *,
    since: datetime,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """有助手回复、却没有对应任务行的回合（漏计费，RF-040 的反向残缺）。

    判定方式：助手消息往前找最近一条用户消息，该消息 id 就是这一回合的
    `turn_id`；再去 `ai_tasks` 里查有没有这个 turn_id 的任务。
    """
    candidates = [
        _row_dict(row)
        for row in db.execute(MISSING_TASK_SQL, {"since": since, "limit": limit * 4})
    ]
    candidates = [row for row in candidates if row["turn_id"] is not None]
    if not candidates:
        return []
    turn_ids = {int(row["turn_id"]) for row in candidates}
    charged = {
        int(turn_id)
        for turn_id in db.scalars(
            select(AiTask.turn_id).where(AiTask.turn_id.in_(turn_ids)).distinct()
        )
    }
    return [
        {
            "turn_id": int(row["turn_id"]),
            "message_id": int(row["message_id"]),
            "session_id": int(row["session_id"]),
            "user_id": int(row["user_id"]),
            "time": _iso(row["create_time"]),
        }
        for row in candidates
        if int(row["turn_id"]) not in charged
    ][:limit]


def ghost_tasks(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    """任务指向了不存在的消息：这笔账没有归属，谁也算不清。"""
    return [
        {
            "task_id": int(row["task_id"]),
            "turn_id": int(row["turn_id"]),
            "user_id": int(row["user_id"]),
            "task_type": row["task_type"],
            "time": _iso(row["create_time"]),
        }
        for row in (
            _row_dict(item) for item in db.execute(GHOST_TASK_SQL, {"limit": limit})
        )
    ]


def pending_age(db: Session, *, now: datetime | None = None, limit: int = 500) -> dict[str, Any]:
    """在途任务的年龄分布与「租约过期但还没被收走」的数量。

    租约过期 ≠ 已经死了：清扫器每 5 分钟一轮，最多差一轮。持续不为 0 说明
    清扫器没在跑（`maintenance_sweep_enabled=false` 或进程没起来）。
    """
    moment = now or datetime.now()
    buckets = {"lt_1m": 0, "lt_5m": 0, "lt_15m": 0, "gte_15m": 0}
    overdue = 0
    no_lease = 0
    long_running = 0
    longest = 0
    rows = [_row_dict(row) for row in db.execute(PENDING_TASK_SQL, {"limit": limit})]
    for row in rows:
        created = _as_datetime(row["create_time"])
        age = int((moment - created).total_seconds()) if created else 0
        longest = max(longest, age)
        if age >= LONG_RUNNING_SECONDS:
            long_running += 1
        if age < 60:
            buckets["lt_1m"] += 1
        elif age < 300:
            buckets["lt_5m"] += 1
        elif age < 900:
            buckets["lt_15m"] += 1
        else:
            buckets["gte_15m"] += 1
        expires_at = _as_datetime(row["expires_at"])
        if expires_at is None:
            no_lease += 1
        elif expires_at < moment:
            overdue += 1
    return {
        "total": len(rows),
        "buckets": buckets,
        "overdue": overdue,
        "no_lease": no_lease,
        # 单回合最长 300s（Node 侧硬超时）；超过它的 pending 只可能是「人已经不在了」。
        "long_running": long_running,
        "longest_seconds": longest,
    }


def turn_success(db: Session, *, since: datetime) -> dict[str, Any]:
    """按交付口径统计回合成功率（RF-017/018）。

    分母是**去重后的 turn_id**，不是任务行数：一个回合命中两个模型时有两行，
    按行统计会把成功率算成「模型成功率」，而且分母被多模型回合放大。
    """
    row = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT turn_id) AS turns,
                   COUNT(DISTINCT CASE WHEN status = 'success' THEN turn_id END) AS delivered,
                   COUNT(DISTINCT CASE WHEN status = 'failed'  THEN turn_id END) AS failed
            FROM ai_tasks
            WHERE turn_id IS NOT NULL AND create_time >= :since
            """
        ),
        {"since": since},
    ).one()
    turns = int(row[0] or 0)
    delivered = int(row[1] or 0)
    return {
        "turns": turns,
        "delivered": delivered,
        "failed": int(row[2] or 0),
        "success_rate": round(delivered * 100 / turns, 1) if turns else 0.0,
    }


def delivered_unbilled(db: Session, *, since: datetime, limit: int = 50) -> dict[str, Any]:
    """已交付但没结算的任务：用户拿到了结果，平台没记上账（RF-017）。"""
    base = (
        AiTask.status == TASK_SUCCESS,
        AiTask.billing_status.notin_((BILLING_BILLED, BILLING_NONE)),
        AiTask.create_time >= since,
    )
    total = int(db.scalar(select(func.count()).select_from(AiTask).where(*base)) or 0)
    rows = db.execute(
        select(AiTask).where(*base).order_by(AiTask.id.desc()).limit(limit)
    ).scalars().all()
    return {
        "total": total,
        "items": [
            {
                "task_id": item.id,
                "turn_id": item.turn_id,
                "user_id": item.user_id,
                "task_type": item.task_type,
                "billing_status": item.billing_status,
                "points_used": point_number(item.points_used),
                "time": _iso(item.create_time),
            }
            for item in rows
        ],
    }


def reconcile(
    db: Session,
    *,
    hours: int = 24,
    limit: int = 200,
    now: datetime | None = None,
) -> dict[str, Any]:
    """一次对账的全部结论。管理端只调这一个函数，口径不会在各处漂。"""
    moment = now or datetime.now()
    since = moment - timedelta(hours=hours)
    ledger = turn_ledger(db, since=since, limit=limit)
    missing = missing_billing(db, since=since)
    ghosts = ghost_tasks(db)
    balance = balance_mismatches(db)
    pending = pending_age(db, now=moment)
    delivered = delivered_unbilled(db, since=since)
    summary = ledger["summary"]
    # 一句话结论：任何一项不为 0 都需要人看。前端据此决定是否标红。
    unhealthy = {
        "missing_reply": summary.get("missing_reply", 0),
        "missing_task": summary.get("missing_task", 0),
        "unbilled": summary.get("unbilled", 0),
        "billing_failed": summary.get("billing_failed", 0),
        "delivered_unbilled": delivered["total"],
        "missing_billing": len(missing),
        "ghost_tasks": len(ghosts),
        "balance_mismatch": balance["mismatched"],
        "overdue_pending": pending["overdue"],
        "no_lease_pending": pending["no_lease"],
        "long_running_pending": pending["long_running"],
    }
    return {
        "window": {
            "hours": hours,
            "since": since.isoformat(timespec="seconds"),
            "until": moment.isoformat(timespec="seconds"),
            "limit": limit,
        },
        "turns": ledger,
        "missing_billing": {"total": len(missing), "items": missing},
        "delivered_unbilled": delivered,
        "ghost_tasks": {"total": len(ghosts), "items": ghosts},
        "balance": balance,
        "pending_tasks": pending,
        "unhealthy": unhealthy,
        "healthy": not any(unhealthy.values()),
    }
