"""add context summary columns to ai_chat_sessions

Rolling summary compaction: messages beyond the injected history window are
LLM-summarized into context_summary (covering everything up to
summary_message_id) so long conversations keep their early intent.

Revision ID: 0033_chat_context_summary
Revises: 0032_ai_task_feature
Create Date: 2026-09-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_chat_context_summary"
down_revision = "0032_ai_task_feature"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("ai_chat_sessions")
    if "context_summary" not in columns:
        op.add_column("ai_chat_sessions", sa.Column("context_summary", sa.Text(), nullable=True))
    if "summary_message_id" not in columns:
        op.add_column("ai_chat_sessions", sa.Column("summary_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    columns = _existing_columns("ai_chat_sessions")
    if "summary_message_id" in columns:
        op.drop_column("ai_chat_sessions", "summary_message_id")
    if "context_summary" in columns:
        op.drop_column("ai_chat_sessions", "context_summary")
