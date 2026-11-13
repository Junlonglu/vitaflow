"""BYOK key audit log.

Records who changed or used which model configuration and when, so users can
verify the server-side key handling. No key material is stored.

Revision ID: 0038_user_ai_audit_logs
Revises: 0037_user_ai_multi_models
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_user_ai_audit_logs"
down_revision = "0037_user_ai_multi_models"
branch_labels = None
depends_on = None

_TABLE = "user_ai_audit_logs"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("credential_id", sa.BigInteger(), nullable=True),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("detail", sa.String(255), nullable=False, server_default=""),
        sa.Column("create_time", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_user_ai_audit_user_id", _TABLE, ["user_id", "id"])
    op.create_index("ix_user_ai_audit_create_time", _TABLE, ["create_time"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index("ix_user_ai_audit_create_time", table_name=_TABLE)
    op.drop_index("ix_user_ai_audit_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
