"""把 ai_agent_traces 摊平成事件流 JSONL（docs/ai/ai-trajectory.md 的 P0 导出）。

表里一行是一个回合、events 是一个数组；训练/评估管道要的是一行一个事件。
这个脚本只做这一件事，不读简历、不读消息表 —— L1 轨迹本身不含 PII。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/export_traces.py --out traces.jsonl
    .venv/Scripts/python.exe scripts/export_traces.py --since 2026-11-01 --limit 1000
    .venv/Scripts/python.exe scripts/export_traces.py            # 打到 stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 允许 `python scripts/export_traces.py` 直接跑（把 backend 加进模块路径）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.ai_trace import AiAgentTrace  # noqa: E402


def _parse_since(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:  # 明确报错好过静默导出全部
        raise SystemExit(f"--since 需要 ISO 日期，例如 2026-11-01 或 2026-11-01T09:00:00（收到 {value!r}）") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 L1 agent 轨迹为事件流 JSONL")
    parser.add_argument("--since", help="只导出该时间之后创建的轨迹（ISO 日期/时间）")
    parser.add_argument("--limit", type=int, default=0, help="最多导出多少个回合（0 = 不限）")
    parser.add_argument("--out", help="输出文件；省略则写 stdout")
    args = parser.parse_args()

    since = _parse_since(args.since) if args.since else None

    with SessionLocal() as db:
        stmt = select(AiAgentTrace).order_by(AiAgentTrace.id)
        if since is not None:
            stmt = stmt.where(AiAgentTrace.create_time >= since)
        if args.limit > 0:
            stmt = stmt.limit(args.limit)
        rows = list(db.scalars(stmt))

    sink = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    turns = 0
    events_written = 0
    try:
        for row in rows:
            turns += 1
            for event in row.events or []:
                if not isinstance(event, dict):
                    continue
                # 早期行可能没在事件里重复 trace_id；导出层补上，消费端只认这一处。
                event.setdefault("trace_id", row.trace_id)
                # 回合关联键跟着每一行走，省得消费端再回表 join。
                event.setdefault("turn_id", row.turn_id)
                event.setdefault("session_id", row.session_id)
                event.setdefault("resume_id", row.resume_id)
                event.setdefault("outcome", row.outcome)
                sink.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                events_written += 1
    finally:
        if args.out:
            sink.close()

    # 统计只打到 stderr，保证 stdout 是干净的 JSONL（可直接管道给训练脚本）。
    print(f"[export-traces] turns={turns} events={events_written}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
