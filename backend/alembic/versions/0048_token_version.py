"""auth: users.token_version, so a password change revokes old sessions

Before this, a JWT was valid until ``exp`` no matter what happened to the
account: changing the password after a suspected leak kept the attacker logged
in for up to 24 hours (RF-027).

``token_version`` is a monotonic counter, not a blacklist. Signing a token
stamps the account's current value into the ``tv`` claim; verification compares
it with the row and rejects a mismatch. That needs no Redis, survives process
restarts, is trivially correct across replicas, and cannot be defeated by clock
skew — which a "revoked-until" timestamp can.

Existing rows start at 0 and tokens minted before this migration carry no
``tv`` claim, which reads as 0: the upgrade itself logs nobody out, but the
first password change after it kills every older token.

Revision ID: 0048_token_version
Revises: 0047_reconcile_indexes
Create Date: 2026-11-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0048_token_version"
down_revision = "0047_reconcile_indexes"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    # 幂等：SQLAlchemy 的 create_all 可能已经建过这一列（单测/新装路径）。
    if _has_column("users", "token_version"):
        return
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    if not _has_column("users", "token_version"):
        return
    op.drop_column("users", "token_version")
