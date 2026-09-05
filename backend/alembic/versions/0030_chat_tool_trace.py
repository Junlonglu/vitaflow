"""add tool_trace to ai_chat_messages

Revision ID: 0030_chat_tool_trace
Revises: 0029_model_ref_slim
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_chat_tool_trace"
down_revision = "0029_model_ref_slim"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("ai_chat_messages")
    with op.batch_alter_table("ai_chat_messages") as batch_op:
        if "tool_trace" not in columns:
            batch_op.add_column(sa.Column("tool_trace", sa.JSON(), nullable=True))


def downgrade() -> None:
    columns = _existing_columns("ai_chat_messages")
    with op.batch_alter_table("ai_chat_messages") as batch_op:
        if "tool_trace" in columns:
            batch_op.drop_column("tool_trace")
