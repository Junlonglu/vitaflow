"""In-memory, TTL-bounded store of web-search evidence per (user_id, resume_id).

Search results fetched by the ai-service's ``web_search`` tool are POSTed to
``/api/ai/service/tool/search-evidence`` and stored here so the numeric
anti-hallucination guard can treat them as a fourth evidence source (besides
resume data, chat history and the user message).

Memory only — no DB row, matching the ``_latest_pending_change`` precedent in
ai_chat_service. This assumes a single uvicorn worker (true today); scale-out
would need Redis. Sync ``def`` endpoints run in FastAPI's threadpool, hence the
lock around every read-modify-write.

Caps: TTL 30 分钟、每 (user, resume) 最多保留 50 条（每次搜索 ≤10 条，等效限 10 次
搜索）——约束「搜索摘要中的数字视为已验证」的暴露窗口。
"""
from __future__ import annotations

import threading
import time

_EVIDENCE_TTL_SECONDS = 30 * 60
_MAX_ENTRIES = 50
_SNIPPET_MAX_CHARS = 300

_lock = threading.Lock()
# (user_id, resume_id) -> [{"time": float, "text": str}, ...]（时间升序）
_store: dict[tuple[int, int], list[dict]] = {}


def _prune(now: float, entries: list[dict]) -> list[dict]:
    return [item for item in entries if now - item["time"] < _EVIDENCE_TTL_SECONDS]


def store_search_evidence(user_id: int, resume_id: int, query: str, results: list[dict]) -> int:
    """Store one search's results as evidence lines. Returns the stored count."""
    query = str(query or "").strip()
    lines = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()[:_SNIPPET_MAX_CHARS]
        if not (title or snippet):
            continue
        parts = [part for part in (query, title, snippet, url) if part]
        lines.append("｜".join(parts))
    if not lines:
        return 0
    now = time.time()
    with _lock:
        key = (user_id, resume_id)
        entries = _prune(now, _store.get(key, []))
        entries.extend({"time": now, "text": line} for line in lines)
        _store[key] = entries[-_MAX_ENTRIES:]
    return len(lines)


def get_recent_search_evidence(user_id: int, resume_id: int) -> list[str]:
    """Return non-expired evidence lines (oldest first); prunes expired entries."""
    now = time.time()
    with _lock:
        entries = _prune(now, _store.get((user_id, resume_id), []))
        if entries:
            _store[(user_id, resume_id)] = entries
        elif (user_id, resume_id) in _store:
            del _store[(user_id, resume_id)]
        return [item["text"] for item in entries]


def clear_search_evidence(user_id: int, resume_id: int) -> None:
    """Test/maintenance hook: drop all evidence for one (user_id, resume_id)."""
    with _lock:
        _store.pop((user_id, resume_id), None)
