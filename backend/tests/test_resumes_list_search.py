"""简历库列表：关键词搜索与排序。

两层覆盖：
1. service 层跑真实 SQLite 内存库，验证 LIKE 转义、排序、分页和用户隔离；
2. 路由层断言查询参数会被清洗后传给 service（非法 sort 回落默认值）。
"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.resumes import router as resumes_router
from app.core.database import Base, get_db
from app.core.exceptions import register_exception_handlers
from app.core.security import get_current_user
from app.models.resume import Resume
from app.services.resume_service import list_resumes


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Resume.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _resume(resume_id: int, title: str, user_id: int = 1, minutes: int = 0) -> Resume:
    stamp = datetime(2026, 1, 1, 12, 0, 0) + timedelta(minutes=minutes)
    return Resume(
        id=resume_id,
        user_id=user_id,
        title=title,
        language="zh-CN",
        resume_data={"basics": {"name": "张三"}},
        template_id="tech",
        template_config={"template_id": "tech"},
        create_time=stamp,
        update_time=stamp,
    )


class ListResumesSearchTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        self.db.add_all(
            [
                _resume(1, "后端工程师简历", minutes=0),
                _resume(2, "前端工程师简历", minutes=10),
                _resume(3, "Backend Engineer Resume", minutes=20),
                _resume(4, "产品经理简历", minutes=30),
                _resume(5, "别人的后端简历", user_id=2, minutes=40),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_keyword_filters_by_title(self):
        items, total = list_resumes(self.db, 1, keyword="后端")
        self.assertEqual(total, 1)
        self.assertEqual([item.id for item in items], [1])

    def test_keyword_is_case_insensitive_for_ascii(self):
        items, total = list_resumes(self.db, 1, keyword="backend")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].id, 3)

    def test_keyword_ignores_other_users_resumes(self):
        _, total = list_resumes(self.db, 1, keyword="简历")
        self.assertEqual(total, 3)

    def test_blank_keyword_lists_everything(self):
        _, total = list_resumes(self.db, 1, keyword="   ")
        self.assertEqual(total, 4)

    def test_percent_sign_is_escaped(self):
        # 用户输入 % 时不能退化成「匹配全部」
        _, total = list_resumes(self.db, 1, keyword="%")
        self.assertEqual(total, 0)

    def test_underscore_is_escaped(self):
        _, total = list_resumes(self.db, 1, keyword="_")
        self.assertEqual(total, 0)

    def test_default_sort_is_recently_updated(self):
        items, _ = list_resumes(self.db, 1)
        self.assertEqual([item.id for item in items], [4, 3, 2, 1])

    def test_sort_by_created(self):
        self.db.get(Resume, 1).create_time = datetime(2026, 5, 1, 0, 0, 0)
        self.db.commit()
        items, _ = list_resumes(self.db, 1, sort="created")
        self.assertEqual(items[0].id, 1)

    def test_sort_by_title_is_stable(self):
        items, _ = list_resumes(self.db, 1, sort="title")
        titles = [item.title for item in items]
        self.assertEqual(titles, sorted(titles))

    def test_unknown_sort_falls_back_to_default(self):
        items, _ = list_resumes(self.db, 1, sort="whatever")
        self.assertEqual([item.id for item in items], [4, 3, 2, 1])

    def test_pagination_with_keyword(self):
        self.db.add_all([_resume(6, "后端简历 二", minutes=50), _resume(7, "后端简历 三", minutes=60)])
        self.db.commit()
        first, total = list_resumes(self.db, 1, page=1, page_size=2, keyword="后端")
        second, _ = list_resumes(self.db, 1, page=2, page_size=2, keyword="后端")
        self.assertEqual(total, 3)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)
        self.assertEqual({item.id for item in first} & {item.id for item in second}, set())


def _client():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resumes_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


class ListResumesEndpointTest(unittest.TestCase):
    def test_forwards_cleaned_keyword_and_sort(self):
        with patch("app.api.resumes.list_resumes", return_value=([], 0)) as mock_list:
            resp = _client().get("/api/resumes", params={"keyword": "  后端  ", "sort": "title", "page": 2, "page_size": 8})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_list.call_args.kwargs["keyword"], "  后端  ")
        self.assertEqual(mock_list.call_args.kwargs["sort"], "title")
        self.assertEqual(resp.json()["data"]["keyword"], "后端")
        self.assertEqual(resp.json()["data"]["sort"], "title")

    def test_invalid_sort_falls_back(self):
        with patch("app.api.resumes.list_resumes", return_value=([], 0)) as mock_list:
            resp = _client().get("/api/resumes", params={"sort": "drop table"})
        self.assertEqual(mock_list.call_args.kwargs["sort"], "updated")
        self.assertEqual(resp.json()["data"]["sort"], "updated")

    def test_keyword_over_limit_is_rejected(self):
        resp = _client().get("/api/resumes", params={"keyword": "x" * 60})
        self.assertEqual(resp.status_code, 422)

    def test_empty_keyword_returns_none(self):
        with patch("app.api.resumes.list_resumes", return_value=([], 0)):
            resp = _client().get("/api/resumes", params={"keyword": "   "})
        self.assertIsNone(resp.json()["data"]["keyword"])


if __name__ == "__main__":
    unittest.main()
