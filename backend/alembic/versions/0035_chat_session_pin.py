"""pin support for ai_chat_sessions

Sidebar conversations can be pinned so they stay above the activity-ordered
list. Purely additive: existing rows default to not pinned.

Revision ID: 0035_chat_session_pin
Revises: 0034_chat_message_timing
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_chat_session_pin"
down_revision = "0034_chat_message_timing"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "is_pinned" not in _existing_columns("ai_chat_sessions"):
        op.add_column(
            "ai_chat_sessions",
            sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    if "is_pinned" in _existing_columns("ai_chat_sessions"):
        op.drop_column("ai_chat_sessions", "is_pinned")
