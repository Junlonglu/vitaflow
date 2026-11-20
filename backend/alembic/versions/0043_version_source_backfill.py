"""backfill resume version source for legacy AI snapshots

0042 给老数据统一填了 source='manual'。但库里的「采纳 AI 助手修改」这一批不是
用户手动存的，而是 AI 应用前的自动快照（`_persist_resume_change` 写入，存的是
改动前的内容）。标成 manual 会有两个后果：列表里显示成「手动存档 / 采纳 AI
助手修改」，并且永远躲过 50 条保留策略，只增不减。

只回填完全匹配的旧 reason，避免误伤真正的手动存档。

Revision ID: 0043_version_source_backfill
Revises: 0042_resume_version_history
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0043_version_source_backfill"
down_revision = "0042_resume_version_history"
branch_labels = None
depends_on = None

LEGACY_AI_REASON = "采纳 AI 助手修改"
AI_REASON = "AI 修改前"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE resume_versions SET source = 'ai', reason = :new_reason "
            "WHERE source = 'manual' AND reason = :old_reason"
        ).bindparams(new_reason=AI_REASON, old_reason=LEGACY_AI_REASON)
    )


def downgrade() -> None:
    # 数据回填不可逆：迁移之后新产生的 AI 快照同样是 source='ai' + 'AI 修改前'，
    # 按条件回退会把它们一起改回 manual，反而破坏正确数据。
    pass
