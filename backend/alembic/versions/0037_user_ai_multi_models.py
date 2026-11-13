"""Multi-model BYOK configs + reasoning effort.

Previously ``user_ai_credentials`` had a UNIQUE(user_id) constraint (one config
per user). Multiple model configs require dropping that constraint, marking one
row as the default, and storing the per-model reasoning effort.

Revision ID: 0037_user_ai_multi_models
Revises: 0036_user_ai_byok_skills
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_user_ai_multi_models"
down_revision = "0036_user_ai_byok_skills"
branch_labels = None
depends_on = None

_TABLE = "user_ai_credentials"
_UQ = "uq_user_ai_credentials_user"
_INDEX = "ix_user_ai_credentials_user_id"


def _columns(bind) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return

    columns = _columns(bind)
    if "reasoning_effort" not in columns:
        op.add_column(_TABLE, sa.Column("reasoning_effort", sa.String(20), nullable=False, server_default="auto"))
    if "is_default" not in columns:
        op.add_column(_TABLE, sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")))

    # Old rows (exactly one per user by the previous unique constraint) become
    # the default so existing users keep their behaviour without re-configuring.
    op.execute(f"UPDATE {_TABLE} SET is_default = 1 WHERE is_default = 0")

    inspector = sa.inspect(bind)
    unique_names = {item["name"] for item in inspector.get_unique_constraints(_TABLE)}
    index_names = {item["name"] for item in inspector.get_indexes(_TABLE)}
    if _UQ in unique_names or _UQ in index_names:
        if bind.dialect.name == "mysql":
            # MySQL exposes a named UNIQUE constraint as a droppable index.
            op.execute(f"ALTER TABLE {_TABLE} DROP INDEX `{_UQ}`")
        else:
            with op.batch_alter_table(_TABLE) as batch_op:
                batch_op.drop_constraint(_UQ, type_="unique")

    if _INDEX not in {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    # Collapse back to one config per user: keep the oldest row (derived table
    # avoids MySQL's "can't specify target table for update in FROM clause").
    op.execute(
        f"DELETE FROM {_TABLE} WHERE id NOT IN "
        f"(SELECT keep_id FROM (SELECT MIN(id) AS keep_id FROM {_TABLE} GROUP BY user_id) AS kept)"
    )
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("is_default")
        batch_op.drop_column("reasoning_effort")
        batch_op.create_unique_constraint(_UQ, ["user_id"])
