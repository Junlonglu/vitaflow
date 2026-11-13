"""BYOK credentials and user skills.

Adds two independent tables. Both are user-scoped and purely additive; existing
rows are untouched. Credentials store only the encrypted key plus a display mask.

Revision ID: 0036_user_ai_byok_skills
Revises: 0035_chat_session_pin
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0036_user_ai_byok_skills"
down_revision = "0035_chat_session_pin"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    if "user_ai_credentials" not in tables:
        op.create_table(
            "user_ai_credentials",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, index=True),
            sa.Column("provider_label", sa.String(60), nullable=False, server_default="自定义"),
            sa.Column("base_url", sa.String(255), nullable=False),
            sa.Column("model", sa.String(120), nullable=False),
            sa.Column("api_key_encrypted", sa.Text(), nullable=False),
            sa.Column("api_key_masked", sa.String(40), nullable=False, server_default=""),
            sa.Column("context_window", sa.Integer(), nullable=False, server_default="32000"),
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="4096"),
            sa.Column("supports_image", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"), index=True),
            sa.Column("last_verified_time", sa.DateTime(), nullable=True),
            sa.Column("create_time", sa.DateTime(), nullable=False),
            sa.Column("update_time", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_user_ai_credentials_user"),
        )

    if "user_skills" not in tables:
        op.create_table(
            "user_skills",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, index=True),
            sa.Column("slug", sa.String(80), nullable=False),
            sa.Column("name", sa.String(60), nullable=False),
            sa.Column("description", sa.String(255), nullable=False, server_default=""),
            sa.Column("instructions", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"), index=True),
            sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("create_time", sa.DateTime(), nullable=False),
            sa.Column("update_time", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "slug", name="uq_user_skills_user_slug"),
        )


def downgrade() -> None:
    tables = _existing_tables()
    if "user_skills" in tables:
        op.drop_table("user_skills")
    if "user_ai_credentials" in tables:
        op.drop_table("user_ai_credentials")
