"""Agent 轨迹表（docs/ai-trajectory.md 的 P0 落盘）。

一次对话回合一行：events 里是 turn_start / tool_call / tool_result / turn_end
事件流，prompt_versions 里是三段稳定文本的 hash。表不设外键 —— L1 轨迹不含
PII，删简历/注销用户后仍要保留，才能做跨版本的效果对比。

Revision ID: 0040_agent_traces
Revises: 0039_info_fragments
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_agent_traces"
down_revision = "0039_info_fragments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("ai_agent_traces"):
        return
    op.create_table(
        "ai_agent_traces",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.BigInteger(), nullable=True),
        sa.Column("session_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("prompt_versions", sa.JSON(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("create_time", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_agent_traces_trace_id", "ai_agent_traces", ["trace_id"], unique=True)
    op.create_index("ix_ai_agent_traces_turn_id", "ai_agent_traces", ["turn_id"])
    op.create_index("ix_ai_agent_traces_session_id", "ai_agent_traces", ["session_id"])
    op.create_index("ix_ai_agent_traces_user_id", "ai_agent_traces", ["user_id"])
    op.create_index("ix_ai_agent_traces_resume_id", "ai_agent_traces", ["resume_id"])
    op.create_index("ix_ai_agent_traces_outcome", "ai_agent_traces", ["outcome"])
    op.create_index("ix_ai_agent_traces_model", "ai_agent_traces", ["model"])
    op.create_index("ix_ai_agent_traces_create_time", "ai_agent_traces", ["create_time"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("ai_agent_traces"):
        return
    for index_name in (
        "ix_ai_agent_traces_trace_id",
        "ix_ai_agent_traces_turn_id",
        "ix_ai_agent_traces_session_id",
        "ix_ai_agent_traces_user_id",
        "ix_ai_agent_traces_resume_id",
        "ix_ai_agent_traces_outcome",
        "ix_ai_agent_traces_model",
        "ix_ai_agent_traces_create_time",
    ):
        op.drop_index(index_name, table_name="ai_agent_traces")
    op.drop_table("ai_agent_traces")
