"""resume version history: source, fingerprint, size, composite index

Version snapshots existed since 0001 but were write-only (no list / restore /
diff). The read side needs to distinguish where a snapshot came from, skip
duplicates produced by repeated autosave, and list a resume's versions without
sorting the whole table.

Revision ID: 0042_resume_version_history
Revises: 0041_model_pricing
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0042_resume_version_history"
down_revision = "0041_model_pricing"
branch_labels = None
depends_on = None

TABLE = "resume_versions"
INDEX = "ix_resume_versions_resume_created"


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _existing_indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    columns = _existing_columns(TABLE)
    if "source" not in columns:
        # 老数据都是「手动/AI 写入的快照」，统一标 manual 不会误导清理策略。
        op.add_column(TABLE, sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"))
    if "content_hash" not in columns:
        op.add_column(TABLE, sa.Column("content_hash", sa.String(length=64), nullable=True))
    if "size_bytes" not in columns:
        op.add_column(TABLE, sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
    if INDEX not in _existing_indexes(TABLE):
        op.create_index(INDEX, TABLE, ["resume_id", "create_time"], unique=False)


def downgrade() -> None:
    if INDEX in _existing_indexes(TABLE):
        op.drop_index(INDEX, table_name=TABLE)
    columns = _existing_columns(TABLE)
    if "size_bytes" in columns:
        op.drop_column(TABLE, "size_bytes")
    if "content_hash" in columns:
        op.drop_column(TABLE, "content_hash")
    if "source" in columns:
        op.drop_column(TABLE, "source")
