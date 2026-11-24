"""observability: composite indexes for user-scoped time-ordered lists

Every user-facing list that matters — "my AI records", "my point
transactions", "my resumes" — filters by ``user_id`` and orders by
``create_time``. With only single-column indexes MySQL satisfies the filter
from the index and then sorts the whole matching set for every page, which is
exactly the shape that gets slow first (RF-061).

The reconciliation view added in the same round reads the same shapes, so these
indexes pay for themselves twice.

Revision ID: 0047_reconcile_indexes
Revises: 0046_lifecycle_leases
Create Date: 2026-11-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0047_reconcile_indexes"
down_revision = "0046_lifecycle_leases"
branch_labels = None
depends_on = None

INDEXES = (
    ("ai_tasks", "ix_ai_tasks_user_created"),
    ("resumes", "ix_resumes_user_created"),
    ("flow_point_transactions", "ix_flow_point_tx_user_created"),
)


def _existing(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    # 幂等：本地库可能已经由 SQLAlchemy 的 create_all 建过同名索引
    # （单测路径），重复 create 会直接报 Duplicate key name。
    for table_name, index_name in INDEXES:
        if index_name in _existing(table_name):
            continue
        op.create_index(
            index_name,
            table_name,
            ["user_id", "create_time"],
            unique=False,
        )


def downgrade() -> None:
    for table_name, index_name in INDEXES:
        if index_name not in _existing(table_name):
            continue
        op.drop_index(index_name, table_name=table_name)
