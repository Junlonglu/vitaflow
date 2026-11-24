"""resumes: revision counter for optimistic writes

Every writer (manual save / AI apply / library import / version restore) used to
send a whole JSON snapshot with no version precondition, so "last write wins"
and the loser never learns it lost. ``revision`` turns that into an explicit
protocol: a writer declares which version it read, the write is a conditional
UPDATE on that value, and a mismatch returns 409 with the latest revision
instead of silently overwriting someone else's edit.

``resume_versions.revision`` records the counter the snapshot belongs to, so a
restore can be aligned with the editor's view.

Revision ID: 0045_resume_revision
Revises: 0044_ai_task_turn_id
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0045_resume_revision"
down_revision = "0044_ai_task_turn_id"
branch_labels = None
depends_on = None

RESUMES = "resumes"
VERSIONS = "resume_versions"


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    # server_default 必须留：老行要就地补 0，且没带 revision 的写入（如未来的
    # 数据修复脚本）不该因此撞 NOT NULL。
    if "revision" not in _existing_columns(RESUMES):
        op.add_column(
            RESUMES,
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    if "revision" not in _existing_columns(VERSIONS):
        op.add_column(
            VERSIONS,
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    if "revision" in _existing_columns(VERSIONS):
        op.drop_column(VERSIONS, "revision")
    if "revision" in _existing_columns(RESUMES):
        op.drop_column(RESUMES, "revision")
