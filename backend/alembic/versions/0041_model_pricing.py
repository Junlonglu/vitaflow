"""一个目录模型只有一份定价。

以前「模型策略」允许同一个 ``model_ref`` 建多条（每条一套价格），但计费侧
``get_ai_config_by_model_ref`` 只会命中其中一条（按 sort_order 取第一条），
其余几套价格静默失效——运营改了价、账单没变。定价表化之后每个模型一行，
这里先把历史重复行并成一行，再加唯一索引把这个约束落到库里。

合并规则：默认的那行优先，其次排序值小、id 小的；被并掉的行只补空缺
（价格没填的补上、可选开关取或），不覆盖保留行的既有值。

Revision ID: 0041_model_pricing
Revises: 0040_agent_traces
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0041_model_pricing"
down_revision = "0040_agent_traces"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_ai_model_configs_model_ref"


def _merge_duplicate_refs(bind) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, model_ref, is_active, sort_order, is_chat_selectable, "
            "context_messages, chat_points_per_call, "
            "chat_points_per_million_input_tokens, chat_points_per_million_output_tokens "
            "FROM ai_model_configs WHERE model_ref IS NOT NULL AND model_ref <> '' "
            "ORDER BY model_ref, is_active DESC, sort_order ASC, id ASC"
        )
    ).fetchall()

    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row[1], []).append(row)

    for model_ref, items in groups.items():
        keeper = items[0]
        drop_ids: list[int] = []
        patch: dict[str, object] = {}
        selectable = bool(keeper[4])
        for other in items[1:]:
            drop_ids.append(int(other[0]))
            selectable = selectable or bool(other[4])
            for index, column in ((6, "chat_points_per_call"), (7, "chat_points_per_million_input_tokens"), (8, "chat_points_per_million_output_tokens")):
                if keeper[index] is None and other[index] is not None and column not in patch:
                    patch[column] = other[index]
            if not int(keeper[5] or 0) and int(other[5] or 0):
                patch["context_messages"] = int(other[5])
        if not selectable:
            patch["is_chat_selectable"] = False
        if patch:
            assignments = ", ".join(f"{key} = :{key}" for key in patch)
            bind.execute(
                sa.text(f"UPDATE ai_model_configs SET {assignments} WHERE id = :id"),
                {**patch, "id": int(keeper[0])},
            )
        if drop_ids:
            bind.execute(
                sa.text("DELETE FROM ai_model_configs WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": drop_ids},
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("ai_model_configs"):
        return
    # 空串是历史遗留的「没选模型」，统一成 NULL：唯一索引不能把一串空串当成同一个人。
    bind.execute(sa.text("UPDATE ai_model_configs SET model_ref = NULL WHERE model_ref = ''"))
    _merge_duplicate_refs(bind)

    existing_indexes = {index["name"] for index in inspector.get_indexes("ai_model_configs")}
    if INDEX_NAME not in existing_indexes:
        op.create_index(INDEX_NAME, "ai_model_configs", ["model_ref"], unique=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("ai_model_configs"):
        return
    if INDEX_NAME in {index["name"] for index in inspector.get_indexes("ai_model_configs")}:
        op.drop_index(INDEX_NAME, table_name="ai_model_configs")
    # 合并掉的重复行不还原：数据已经按保留行的口径继续计费，回滚索引即可。
