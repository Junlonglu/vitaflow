"""简历写入协议：版本号 + 条件 UPDATE。

M1 的不变量：每一次写入都声明「我看到的是第几版」，不匹配就拒绝并告诉调用方
最新版，而不是谁后到谁赢。这里用真实 SQLite 跑 `persist_resume_data`，因为要
验证的正是那条 UPDATE ... WHERE revision = ? 的语义——用 stub 模拟等于把要测的
东西自己实现一遍。

覆盖：
1. 首次写入推进版本号；带正确版本号再次写入成功；
2. 带着已经被别人推进过的版本号写入 → 409 + latest_revision，库里内容不变；
3. 缺省 expect_revision（关页兜底）仍推进版本，但不做冲突判断；
4. 回读与写入不一致 → 409 + 回滚（JSON 静默截断只有回读看得见）；
5. 恢复路径用 expected_revision 取代秒级 update_time，且备份快照记恢复前的版本。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.exceptions import AppException
from app.models.resume import Resume, ResumeVersion
from app.schemas.resume import ResumeUpdate
from app.services import resume_service


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Resume.__table__, ResumeVersion.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed(db, name="旧名"):
    # BigInteger 主键在 SQLite 不自增，显式给 id。
    resume = Resume(
        id=1,
        user_id=1,
        title="我的简历",
        language="zh-CN",
        resume_data={"basics": {"name": name}},
        template_id="tech",
        template_config={"template_id": "tech", "theme_color": "#2563eb"},
        revision=0,
    )
    db.add(resume)
    db.commit()
    return resume


def _seed_version(db, name="旧版"):
    version = ResumeVersion(
        id=5,
        resume_id=1,
        resume_data={"basics": {"name": name}},
        template_config={"template_id": "classic", "theme_color": "#111111"},
        reason="投递前存档",
        source=resume_service.VERSION_SOURCE_MANUAL,
        size_bytes=10,
        revision=0,
    )
    db.add(version)
    db.commit()
    return version


class PersistResumeDataTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_write_advances_revision(self):
        resume = _seed(self.db)

        updated = resume_service.persist_resume_data(
            self.db,
            resume,
            {"basics": {"name": "新名"}},
            source=resume_service.VERSION_SOURCE_MANUAL,
            expect_revision=0,
        )

        self.assertEqual(updated.revision, 1)
        self.assertEqual(updated.resume_data["basics"]["name"], "新名")

    def test_stale_revision_is_rejected_and_leaves_data_untouched(self):
        resume = _seed(self.db)
        # 另一个标签页先写了一次：库里的版本推进到 1。
        resume_service.persist_resume_data(
            self.db,
            resume,
            {"basics": {"name": "别人的修改"}},
            source=resume_service.VERSION_SOURCE_MANUAL,
            expect_revision=0,
        )
        self.db.commit()

        with self.assertRaises(AppException) as ctx:
            resume_service.persist_resume_data(
                self.db,
                resume,
                {"basics": {"name": "我的修改"}},
                source=resume_service.VERSION_SOURCE_MANUAL,
                expect_revision=0,
            )

        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(ctx.exception.data["latest_revision"], 1)
        fresh = self.db.get(Resume, 1)
        self.assertEqual(fresh.resume_data["basics"]["name"], "别人的修改", "失败方不能覆盖赢家")
        self.assertEqual(fresh.revision, 1)

    def test_missing_expect_revision_still_advances_but_never_conflicts(self):
        """关页兜底没有界面可以提示冲突，只能尽力保存；但它仍然消耗一个版本号。"""
        resume = _seed(self.db)

        first = resume_service.persist_resume_data(
            self.db, resume, {"basics": {"name": "A"}}, source=resume_service.VERSION_SOURCE_MANUAL
        )
        second = resume_service.persist_resume_data(
            self.db, first, {"basics": {"name": "B"}}, source=resume_service.VERSION_SOURCE_MANUAL
        )

        self.assertEqual(second.revision, 2)
        self.assertEqual(second.resume_data["basics"]["name"], "B")

    def test_readback_mismatch_rolls_back(self):
        resume = _seed(self.db)
        # 正常数据库不会截断 JSON；替换读回来复现「写进去的被截掉」。
        with patch.object(
            resume_service,
            "_reload_resume_data",
            return_value={"basics": {"name": "被截断的名字"}},
        ):
            with self.assertRaises(AppException) as ctx:
                resume_service.persist_resume_data(
                    self.db,
                    resume,
                    {"basics": {"name": "新名"}},
                    source=resume_service.VERSION_SOURCE_MANUAL,
                )

        self.assertEqual(ctx.exception.code, 409)
        fresh = self.db.get(Resume, 1)
        self.assertEqual(fresh.resume_data["basics"]["name"], "旧名")
        self.assertEqual(fresh.revision, 0, "失败的写入不能消耗版本号")

    def test_unknown_source_is_refused(self):
        resume = _seed(self.db)
        with self.assertRaises(AppException) as ctx:
            resume_service.persist_resume_data(
                self.db, resume, {"basics": {"name": "x"}}, source="whatever"
            )
        self.assertEqual(ctx.exception.code, 500)


class UpdateResumeProtocolTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_title_only_update_advances_revision(self):
        """改标题也是写：否则「改别名」能绕开冲突检测。"""
        _seed(self.db)

        updated = resume_service.update_resume(self.db, 1, 1, ResumeUpdate(title="新标题"))

        self.assertEqual(updated.title, "新标题")
        self.assertEqual(updated.revision, 1)

    def test_payload_expect_revision_is_enforced(self):
        _seed(self.db)
        resume_service.update_resume(self.db, 1, 1, ResumeUpdate(title="第一改", expect_revision=0))

        with self.assertRaises(AppException) as ctx:
            resume_service.update_resume(self.db, 1, 1, ResumeUpdate(title="第二改", expect_revision=0))

        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(self.db.get(Resume, 1).title, "第一改")

    def test_payload_expect_revision_is_not_persisted_as_a_column(self):
        _seed(self.db)
        updated = resume_service.update_resume(
            self.db, 1, 1, ResumeUpdate(resume_data={"basics": {"name": "新"}}, expect_revision=0)
        )
        self.assertFalse(hasattr(updated, "expect_revision") and "expect_revision" in updated.__dict__)


class RestoreVersionProtocolTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_restore_uses_revision_and_backs_up_the_previous_version(self):
        _seed(self.db, name="当前内容")
        _seed_version(self.db, name="旧版内容")

        restored = resume_service.restore_version(self.db, 1, 1, 5, expected_revision=0)

        self.assertEqual(restored.resume_data["basics"]["name"], "旧版内容")
        self.assertEqual(restored.template_id, "classic")
        self.assertEqual(restored.revision, 1)
        backups = [item for item in self.db.query(ResumeVersion).all() if item.source == resume_service.VERSION_SOURCE_RESTORE]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].resume_data["basics"]["name"], "当前内容")
        self.assertEqual(backups[0].revision, 0, "备份快照记录的是恢复前的版本号")

    def test_stale_expected_revision_is_rejected(self):
        _seed(self.db)
        _seed_version(self.db)
        resume_service.update_resume(
            self.db, 1, 1, ResumeUpdate(title="别人的修改", expect_revision=0)
        )

        with self.assertRaises(AppException) as ctx:
            resume_service.restore_version(self.db, 1, 1, 5, expected_revision=0)

        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(ctx.exception.data["latest_revision"], 1)
        self.assertEqual(self.db.get(Resume, 1).resume_data["basics"]["name"], "旧名")
        # 恢复失败时那份「恢复前备份」不能留在库里。
        self.assertEqual(
            [item for item in self.db.query(ResumeVersion).all() if item.source == resume_service.VERSION_SOURCE_RESTORE],
            [],
        )

    def test_legacy_expected_update_time_still_guards(self):
        """老客户端只传秒级时间戳：仍要拒绝，只是精度不够。"""
        resume = _seed(self.db)
        _seed_version(self.db)
        stale = resume.update_time.replace(year=2025)

        with self.assertRaises(AppException) as ctx:
            resume_service.restore_version(self.db, 1, 1, 5, expected_update_time=stale)

        self.assertEqual(ctx.exception.code, 409)


class SnapshotRevisionTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def tearDown(self):
        self.db.close()

    def test_snapshot_records_the_resume_revision(self):
        resume = _seed(self.db)
        resume_service.persist_resume_data(
            self.db, resume, {"basics": {"name": "改过一次"}}, source=resume_service.VERSION_SOURCE_MANUAL
        )
        self.db.commit()

        version, created = resume_service.create_version(self.db, 1, 1, "存档")

        self.assertTrue(created)
        self.assertEqual(version.revision, 1)
        self.assertEqual(version.resume_data["basics"]["name"], "改过一次")


if __name__ == "__main__":
    unittest.main()
