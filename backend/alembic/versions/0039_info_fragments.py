"""Atomic info fragments + pending fragment proposals.

Adds the user-level information library (one row per reusable fact) and the
column that carries an agent-proposed batch of fragments until the user
confirms it on the chat message.

Revision ID: 0039_info_fragments
Revises: 0038_user_ai_audit_logs
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_info_fragments"
down_revision = "0038_user_ai_audit_logs"
branch_labels = None
depends_on = None

_TABLE = "info_fragments"
_CHAT_TABLE = "ai_chat_messages"


def _has_table(bind, name: str) -> bool:
    return name in set(sa.inspect(bind).get_table_names())


def _has_column(bind, table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False, server_default="custom"),
            sa.Column("title", sa.String(120), nullable=False, server_default=""),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("tags", sa.JSON(), nullable=False),
            sa.Column("search_text", sa.Text(), nullable=True),
            sa.Column("fingerprint", sa.String(40), nullable=False, server_default=""),
            sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_used_time", sa.DateTime(), nullable=True),
            sa.Column("create_time", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("update_time", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_info_fragments_user_id", _TABLE, ["user_id"])
        op.create_index("ix_info_fragments_kind", _TABLE, ["kind"])
        op.create_index("ix_info_fragments_fingerprint", _TABLE, ["fingerprint"])
        op.create_index("ix_info_fragments_user_kind", _TABLE, ["user_id", "kind"])
        op.create_index("ix_info_fragments_user_updated", _TABLE, ["user_id", "update_time"])

    if _has_table(bind, _CHAT_TABLE) and not _has_column(bind, _CHAT_TABLE, "pending_info_fragments"):
        op.add_column(_CHAT_TABLE, sa.Column("pending_info_fragments", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _CHAT_TABLE) and _has_column(bind, _CHAT_TABLE, "pending_info_fragments"):
        op.drop_column(_CHAT_TABLE, "pending_info_fragments")
    if _has_table(bind, _TABLE):
        for index in (
            "ix_info_fragments_user_updated",
            "ix_info_fragments_user_kind",
            "ix_info_fragments_fingerprint",
            "ix_info_fragments_kind",
            "ix_info_fragments_user_id",
        ):
            op.drop_index(index, table_name=_TABLE)
        op.drop_table(_TABLE)
