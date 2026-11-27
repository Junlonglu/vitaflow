"""info_fragments.is_default：给「默认个人信息」一个显式标记

basic 条目天然可能不止一条（不同求职方向的 title 不同），而 Agent 自动补
基本信息时需要一条确定的「这就是我」。默认用显式标记，而不是「最近使用」：
后者会被一次无关的导入改掉，用户无从预期。

迁移不做历史合并（合并要删用户数据，且可能被待确认修改引用），只回填：
每个用户挑使用次数最多、最近更新的那条 basic 作为默认。

Revision ID: 0049_basic_default
Revises: 0048_token_version
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0049_basic_default"
down_revision = "0048_token_version"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    # 幂等：单测/新装路径的 create_all 可能已经建过这一列。
    if _has_column("info_fragments", "is_default"):
        return
    op.add_column(
        "info_fragments",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_info_fragments_user_default", "info_fragments", ["user_id", "kind", "is_default"]
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, user_id FROM info_fragments "
            "WHERE kind = 'basic' AND enabled = 1 "
            "ORDER BY usage_count DESC, update_time DESC, id DESC"
        )
    ).fetchall()
    seen: set[int] = set()
    for fragment_id, user_id in rows:
        if user_id in seen:
            continue
        seen.add(user_id)
        bind.execute(
            sa.text("UPDATE info_fragments SET is_default = 1 WHERE id = :fragment_id"),
            {"fragment_id": fragment_id},
        )


def downgrade() -> None:
    if not _has_column("info_fragments", "is_default"):
        return
    for index_name in ("ix_info_fragments_user_default",):
        try:
            op.drop_index(index_name, table_name="info_fragments")
        except Exception:  # 索引可能本来不在（create_all 路径）
            pass
    op.drop_column("info_fragments", "is_default")
