"""strategy table slimming: rename pi_model_ref to model_ref, drop legacy direct-connection columns (ADR-0005, ticket 11)

Revision ID: 0029_model_ref_slim
Revises: 0028_pi_model_ref
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_model_ref_slim"
down_revision = "0028_pi_model_ref"
branch_labels = None
depends_on = None


DROPPED_COLUMNS = (
    "provider",
    "base_url",
    "api_key",
    "model",
    "temperature",
    "timeout",
    "max_tokens",
    "supports_multimodal",
)


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("ai_model_configs")
    with op.batch_alter_table("ai_model_configs") as batch_op:
        if "pi_model_ref" in columns and "model_ref" not in columns:
            batch_op.alter_column("pi_model_ref", new_column_name="model_ref", existing_type=sa.String(200))
        for name in DROPPED_COLUMNS:
            if name in columns:
                batch_op.drop_column(name)


def downgrade() -> None:
    columns = _existing_columns("ai_model_configs")
    with op.batch_alter_table("ai_model_configs") as batch_op:
        if "model_ref" in columns and "pi_model_ref" not in columns:
            batch_op.alter_column("model_ref", new_column_name="pi_model_ref", existing_type=sa.String(200))
        batch_op.add_column(sa.Column("provider", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("base_url", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("api_key", sa.String(1000), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("temperature", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("timeout", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("max_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("supports_multimodal", sa.Boolean(), nullable=True))
