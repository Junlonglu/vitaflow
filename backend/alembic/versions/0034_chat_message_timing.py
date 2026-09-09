"""add turn timing column to ai_chat_messages

Per-turn latency breakdown (first token / tool execution / total) reported by
the Node AI service on the result event and persisted for the admin dashboard's
per-phase timing stats.

Revision ID: 0034_chat_message_timing
Revises: 0033_chat_context_summary
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_chat_message_timing"
down_revision = "0033_chat_context_summary"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "timing" not in _existing_columns("ai_chat_messages"):
        op.add_column("ai_chat_messages", sa.Column("timing", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "timing" in _existing_columns("ai_chat_messages"):
        op.drop_column("ai_chat_messages", "timing")
