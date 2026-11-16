"""Flow Points 扣减的并发正确性。

原来的实现是「读余额 → 加减 → 写回」：两个并发回合都读到 100、各扣 5，最后
只扣了一次。这里锁定新的语义——加减在数据库里以当前值为基准完成，余额不足时
由 UPDATE 的条件把扣费拒掉，而不是靠内存里的旧数字判断。
"""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.dialects import mysql

from app.models.user import User
from app.services.flow_points_service import add_point_transaction


class _AtomicDb:
    """模拟一条 users 行，并实现「原子 UPDATE」的语义。

    ``rowcount=0`` 表示条件更新命中 0 行（余额不足）；否则按 DB 当前值结算，
    这正是修复的关键——结果不依赖调用方内存里的旧余额。
    """

    def __init__(self, stored_balance, delta, *, rowcount: int = 1, clamp_zero: bool = False):
        self.stored = Decimal(str(stored_balance))
        self.delta = Decimal(str(delta))
        self.rowcount = rowcount
        self.clamp_zero = clamp_zero
        self.executed = False
        self.stmt = None
        self.added: list = []
        self.flushes = 0

    def execute(self, stmt):
        self.executed = True
        self.stmt = stmt
        if self.rowcount:
            self.stored = self.stored + self.delta
            if self.clamp_zero and self.stored < 0:
                self.stored = Decimal("0")
        return SimpleNamespace(rowcount=self.rowcount)

    def scalar(self, _stmt):
        return self.stored

    def add(self, obj):
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1


def _user(balance="100.00") -> User:
    return User(id=7, flow_points=Decimal(balance))


class ConcurrentChargeTest(unittest.TestCase):
    def test_charge_uses_the_database_balance_not_the_stale_object(self) -> None:
        # 内存里的 user 还是 100，但另一个回合已经把它扣到 95。
        user = _user("100.00")
        db = _AtomicDb("95.00", "-5")

        txn = add_point_transaction(db, user, "ai_chat", -5, "AI 对话扣费")

        self.assertTrue(db.executed, "余额变更必须落成一条 UPDATE")
        self.assertEqual(txn.balance_after, Decimal("90.00"), "应以 DB 当前值 95 为基准，而不是 100")
        self.assertEqual(txn.points_delta, Decimal("-5"))

    def test_insufficient_balance_is_rejected_by_the_update_condition(self) -> None:
        user = _user("3.00")
        db = _AtomicDb("3.00", "-5", rowcount=0)

        with self.assertRaises(Exception) as ctx:
            add_point_transaction(db, user, "ai_chat", -5, "AI 对话扣费")

        self.assertEqual(ctx.exception.code, 402)
        self.assertIn("当前余额 3.00 点", ctx.exception.message)
        self.assertEqual(db.added, [], "扣费失败时不该写入账本记录")

    def test_overdraft_to_zero_clamps_without_raising(self) -> None:
        user = _user("3.00")
        db = _AtomicDb("3.00", "-5", clamp_zero=True)

        txn = add_point_transaction(
            db, user, "ai_chat", -5, "AI 对话扣费", allow_overdraft_to_zero=True
        )

        self.assertEqual(txn.balance_after, Decimal("0"), "允许透支时钳到 0，不报余额不足")

    def test_charge_statement_compares_against_the_column(self) -> None:
        db = _AtomicDb("100.00", "-5")
        add_point_transaction(db, _user("100.00"), "ai_chat", -5, "扣费")

        sql = str(db.stmt.compile(dialect=mysql.dialect()))

        self.assertIn("UPDATE users SET flow_points=", sql)
        self.assertIn("users.flow_points + ", sql, "新值必须由列值算出，不能是固定数字")
        self.assertIn("users.flow_points + ", sql.split("WHERE", 1)[1], "余额不足的判定也要落在 WHERE 上")

    def test_zero_delta_does_not_touch_the_row(self) -> None:
        db = _AtomicDb("100.00", "0")

        txn = add_point_transaction(db, _user("100.00"), "ai_chat", 0, "无变化")

        self.assertFalse(db.executed, "0 点差额不该产生一条 UPDATE")
        self.assertEqual(txn.balance_after, Decimal("100.00"))


class TopUpTest(unittest.TestCase):
    def test_top_up_adds_to_the_database_balance(self) -> None:
        user = _user("10.00")
        db = _AtomicDb("40.00", "25")

        txn = add_point_transaction(db, user, "redeem", 25, "兑换码充值")

        self.assertEqual(txn.balance_after, Decimal("65.00"))


if __name__ == "__main__":
    unittest.main()
