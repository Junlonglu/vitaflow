"""add pi_model_ref mapping to ai_model_configs

Revision ID: 0028_pi_model_ref
Revises: 0027_starter_tpl_backfill
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_pi_model_ref"
down_revision = "0027_starter_tpl_backfill"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("ai_model_configs")
    with op.batch_alter_table("ai_model_configs") as batch_op:
        if "pi_model_ref" not in columns:
            batch_op.add_column(sa.Column("pi_model_ref", sa.String(200), nullable=True))


def downgrade() -> None:
    columns = _existing_columns("ai_model_configs")
    with op.batch_alter_table("ai_model_configs") as batch_op:
        if "pi_model_ref" in columns:
            batch_op.drop_column("pi_model_ref")
