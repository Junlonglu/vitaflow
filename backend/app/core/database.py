from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.metrics import MeasuredQueuePool


def _pool_options() -> dict:
    """连接池参数，只在真数据库上给。

    SQLite 单测走 ``StaticPool``/``NullPool``，pymysql 之外的方言收到
    ``pool_size`` 会直接抛 TypeError；这里按 URL 前缀分流，避免测试环境必须
    跟着生产配置走。

    ``poolclass`` 换成带采样的一层：池等待时间没有别的观测点，而它正是
    「偶发变慢但日志干净」的头号嫌疑。SQLite 不换，它的池语义不同。
    """
    if settings.database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "poolclass": MeasuredQueuePool,
    }


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    **_pool_options(),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """请求级会话。**不要**把它传进 ``StreamingResponse``。

    FastAPI 在响应体开始发送之前就会执行依赖清理，所以生成器真正跑起来时这个
    会话已经 close 了；里面再 add/commit 等于复用一个已关闭的 Session——能跑，
    但事务边界和连接占用都不可预期，而且完全依赖 ``Session.close()`` 之后还能
    重新借用连接的实现细节。

    流式接口请让生成器自己持有会话：接收 ``db_factory=SessionLocal``，
    在生成器内 open、在 ``finally`` 里 close（见 ``ai_chat_service``）。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
