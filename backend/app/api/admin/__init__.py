"""后台管理接口，按资源域拆分。

拆分前这里是单个 2157 行的 ``api/admin.py``，所有域共用一个文件，改任何一处
都要在 2000 行里上下翻。现在每个子模块自带 ``router``（已挂
``get_current_admin`` 依赖），本文件只负责聚合成一个 ``/admin`` 前缀的 router，
``app.main`` 的注册方式不变。

聚合顺序沿用拆分前的声明顺序：``/users/flow-points/grant-all``（points）必须排在
``/users/{user_id}/...``（users）之前，否则会被后者更宽泛的路径先匹配。
"""

from fastapi import APIRouter

from app.api.admin import (
    ai_configs,
    announcements,
    dashboard,
    feedbacks,
    maintenance,
    observability,
    points,
    records,
    redeem_codes,
    settings,
    templates,
    users,
)

router = APIRouter(prefix="/admin", tags=["admin"])

for _module in (
    announcements,
    dashboard,
    settings,
    feedbacks,
    points,
    users,
    records,
    ai_configs,
    redeem_codes,
    templates,
    maintenance,
    observability,
):
    router.include_router(_module.router)
