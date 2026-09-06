"""add feature column to ai_tasks for chat turn breakdown

Chat is now the only AI entry point, so the per-feature dashboard needs the
quick command a turn used (polish/translate/optimize/generate/greeting) or
"chat" for free-form messages. Stored as a real column (not JSON) so the
breakdown group-by stays indexable; legacy task rows keep NULL and are still
grouped by task_type.

Revision ID: 0032_ai_task_feature
Revises: 0031_chat_pending_questions
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_ai_task_feature"
down_revision = "0031_chat_pending_questions"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("ai_tasks")
    if "feature" not in columns:
        with op.batch_alter_table("ai_tasks") as batch_op:
            batch_op.add_column(sa.Column("feature", sa.String(30), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("ai_tasks")}
    if "ix_ai_tasks_feature" not in indexes:
        op.create_index("ix_ai_tasks_feature", "ai_tasks", ["feature"])


def downgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("ai_tasks")}
    if "ix_ai_tasks_feature" in indexes:
        op.drop_index("ix_ai_tasks_feature", table_name="ai_tasks")
    columns = _existing_columns("ai_tasks")
    with op.batch_alter_table("ai_tasks") as batch_op:
        if "feature" in columns:
            batch_op.drop_column("feature")
