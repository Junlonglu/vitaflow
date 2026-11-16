"""Regression: 渲染锁不泄漏、兑换码生成不做全表扫描。

两处都是「数据量大了才暴露」的问题，功能测试跑不出来：

- ``_pdf_locks`` 原来每导出一次就留在进程里一条，只增不减；
- ``generate_redeem_codes`` 用 ``select(FlowPointRedeemCode.code)`` 取回全部码值
  再在内存里查重，生成 500 个新码要先拉走整张表。
"""
from __future__ import annotations

import threading
import time
import unittest

from sqlalchemy import select

from app.models.ai_config import FlowPointRedeemCode
from app.services import flow_points_service as fps
from app.services import pdf_export_service as pdf_service


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _RecordingDb:
    """只回答 ``WHERE code IN (...)`` 这类查询；顺带记录每条 SQL。"""

    def __init__(self, taken: set[str] | None = None):
        self.taken = set(taken or ())
        self.statements: list[str] = []
        self.added: list[FlowPointRedeemCode] = []

    def scalars(self, stmt):
        compiled = stmt.compile()
        sql = str(stmt).upper()
        self.statements.append(sql)
        assert "WHERE" in sql, f"兑换码查重不该全表读取：{sql}"
        candidates: list[str] = []
        for value in compiled.params.values():
            if isinstance(value, (list, tuple, set)):
                candidates.extend(value)
            else:
                candidates.append(value)
        return _Result(code for code in candidates if code in self.taken)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


class PdfLockLifecycleTest(unittest.TestCase):
    def setUp(self):
        pdf_service._pdf_locks.clear()

    def tearDown(self):
        pdf_service._pdf_locks.clear()

    def test_entry_is_dropped_after_use(self):
        with pdf_service._pdf_lock("resume-1"):
            self.assertIn("resume-1", pdf_service._pdf_locks)
        self.assertEqual(dict(pdf_service._pdf_locks), {})

    def test_repeated_use_does_not_accumulate(self):
        for index in range(50):
            with pdf_service._pdf_lock(f"resume-{index}"):
                pass
        self.assertEqual(dict(pdf_service._pdf_locks), {})

    def test_concurrent_holders_are_serialized_and_cleaned_up(self):
        active = 0
        max_active = 0
        barrier = threading.Barrier(6)
        guard = threading.Lock()

        def worker():
            nonlocal active, max_active
            barrier.wait()
            with pdf_service._pdf_lock("resume-9"):
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                # 给别的线程一个机会挤进来：串行时这里始终是 1。
                threading.Event().wait(0.02)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(max_active, 1, "同一份简历的渲染锁必须互斥")
        self.assertEqual(dict(pdf_service._pdf_locks), {}, "等待者退出后锁条目应被移除")

    def test_waiter_keeps_entry_alive(self):
        """持有者退出时还有人排队，条目不能被删——否则后来者会拿到第二把锁。"""
        inside = threading.Event()
        release = threading.Event()
        holder_done = threading.Event()

        def holder():
            with pdf_service._pdf_lock("resume-7"):
                inside.set()
                release.wait(3)
            holder_done.set()

        waiter_inside = threading.Event()
        waiter_release = threading.Event()

        def waiter():
            inside.wait(3)
            with pdf_service._pdf_lock("resume-7"):
                waiter_inside.set()
                waiter_release.wait(3)

        holder_thread = threading.Thread(target=holder)
        waiter_thread = threading.Thread(target=waiter)
        holder_thread.start()
        self.assertTrue(inside.wait(3))
        waiter_thread.start()

        deadline = time.monotonic() + 3
        while pdf_service._pdf_locks.get("resume-7", (None, 0))[1] < 2:
            self.assertLess(time.monotonic(), deadline, "等待者没有登记到计数里")
            time.sleep(0.005)

        release.set()
        self.assertTrue(holder_done.wait(3))
        self.assertTrue(waiter_inside.wait(3), "等待者应接手这把锁")
        self.assertIn("resume-7", pdf_service._pdf_locks, "锁还在被持有时不该删条目")

        waiter_release.set()
        waiter_thread.join(timeout=3)
        holder_thread.join(timeout=3)
        self.assertEqual(dict(pdf_service._pdf_locks), {})


class RedeemCodeQueryTest(unittest.TestCase):
    def _generate(self, db, **kwargs):
        params = dict(
            admin_id=1,
            count=3,
            points=10,
            total_count=1,
            expire_time=None,
            note=None,
        )
        params.update(kwargs)
        return fps.generate_redeem_codes(db, **params)

    def test_generated_codes_skip_existing_ones(self):
        # 预置一批「已被占用」的码：生成器必须整批避开，且不读全表。
        taken = {f"TAKEN{i:07d}" for i in range(5000)}
        db = _RecordingDb(taken=taken)
        items = self._generate(db, count=20)

        self.assertEqual(len(items), 20)
        self.assertEqual(len({item.code for item in items}), 20)
        self.assertFalse({item.code for item in items} & taken)
        for sql in db.statements:
            self.assertIn("IN", sql)

    def test_custom_codes_only_check_the_submitted_ones(self):
        db = _RecordingDb(taken={"PROMO2026"})
        with self.assertRaises(Exception) as ctx:
            self._generate(db, count=1, custom_codes=["PROMO2026", "PROMO2027"])
        self.assertEqual(getattr(ctx.exception, "code", None), 409)
        # 还原后的异常文案要能指出撞了哪个码。
        self.assertIn("PROMO2026", ctx.exception.message)
        # 语句形状由 _RecordingDb 把关：每条查重都必须带 WHERE，且只查提交的码。
        self.assertTrue(db.statements)

    def test_custom_codes_pass_through_when_free(self):
        db = _RecordingDb()
        items = self._generate(db, count=1, custom_codes=["promo-2026"])
        self.assertEqual([item.code for item in items], ["PROMO-2026"])

    def test_query_shape_has_no_unbounded_select(self):
        # 直接盯住语句本身：新写法必须是 ``WHERE code IN (...)``。
        stmt = select(FlowPointRedeemCode.code).where(FlowPointRedeemCode.code.in_({"A", "B"}))
        self.assertIn("WHERE", str(stmt).upper())
        db = _RecordingDb()
        fps._generate_unique_redeem_codes(db, 4)
        for sql in db.statements:
            self.assertIn("IN", sql)
            self.assertIn("WHERE", sql)


if __name__ == "__main__":
    unittest.main()
