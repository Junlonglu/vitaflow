"""lifecycle: leases, billing status and client request ids

Three things that all answer "who closes this out?":

* ``ai_tasks.expires_at`` — every non-terminal task gets a lease. A task whose
  lease expired was abandoned mid-flight (process killed, Node timed out, client
  disconnected); a sweeper fails it and returns the pre-charged points.
* ``ai_tasks.billing_status`` — execution result (``status``) and billing result
  are now separate columns. "Delivered but never charged" used to be
  indistinguishable from "failed", which made the dashboard success rate lie.
* ``resumes.request_id`` / ``user_feedbacks.request_id`` — idempotency keys for
  the two flows where a double submit silently duplicates a row.

Revision ID: 0046_lifecycle_leases
Revises: 0045_resume_revision
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0046_lifecycle_leases"
down_revision = "0045_resume_revision"
branch_labels = None
depends_on = None

AI_TASKS = "ai_tasks"
RESUMES = "resumes"
FEEDBACKS = "user_feedbacks"

TASK_LEASE_MINUTES = 15


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _uniques(table_name: str) -> set[str]:
    return {item["name"] for item in _inspector().get_unique_constraints(table_name)}


def upgrade() -> None:
    columns = _columns(AI_TASKS)
    if "billing_status" not in columns:
        op.add_column(
            AI_TASKS,
            sa.Column("billing_status", sa.String(20), nullable=False, server_default="unbilled"),
        )
    if "expires_at" not in columns:
        op.add_column(AI_TASKS, sa.Column("expires_at", sa.DateTime(), nullable=True))

    # 历史行回填：终态不进清扫器，pending 行补上「创建时间 + 租约」，
    # 否则它们在迁移后永远等不到收尾（这是本次迁移的核心目的）。
    op.execute(
        "UPDATE ai_tasks SET billing_status = 'billed' "
        "WHERE status = 'success' AND billing_status = 'unbilled'"
    )
    op.execute(
        "UPDATE ai_tasks SET billing_status = CASE WHEN points_used = 0 THEN 'refunded' "
        "ELSE 'failed' END WHERE status = 'failed' AND billing_status = 'unbilled'"
    )
    op.execute(
        f"UPDATE ai_tasks SET expires_at = DATE_ADD(create_time, INTERVAL {TASK_LEASE_MINUTES} MINUTE) "
        "WHERE status = 'pending' AND expires_at IS NULL"
    )

    indexes = _indexes(AI_TASKS)
    if "ix_ai_tasks_billing_status" not in indexes:
        op.create_index("ix_ai_tasks_billing_status", AI_TASKS, ["billing_status"])
    if "ix_ai_tasks_expires_at" not in indexes:
        op.create_index("ix_ai_tasks_expires_at", AI_TASKS, ["expires_at"])

    if "request_id" not in _columns(RESUMES):
        op.add_column(RESUMES, sa.Column("request_id", sa.String(64), nullable=True))
    if "uq_resumes_user_request" not in _uniques(RESUMES):
        op.create_unique_constraint("uq_resumes_user_request", RESUMES, ["user_id", "request_id"])

    if "request_id" not in _columns(FEEDBACKS):
        op.add_column(FEEDBACKS, sa.Column("request_id", sa.String(64), nullable=True))
    if "uq_user_feedbacks_user_request" not in _uniques(FEEDBACKS):
        op.create_unique_constraint(
            "uq_user_feedbacks_user_request", FEEDBACKS, ["user_id", "request_id"]
        )


def downgrade() -> None:
    if "uq_user_feedbacks_user_request" in _uniques(FEEDBACKS):
        op.drop_constraint("uq_user_feedbacks_user_request", FEEDBACKS, type_="unique")
    if "request_id" in _columns(FEEDBACKS):
        op.drop_column(FEEDBACKS, "request_id")

    if "uq_resumes_user_request" in _uniques(RESUMES):
        op.drop_constraint("uq_resumes_user_request", RESUMES, type_="unique")
    if "request_id" in _columns(RESUMES):
        op.drop_column(RESUMES, "request_id")

    indexes = _indexes(AI_TASKS)
    if "ix_ai_tasks_expires_at" in indexes:
        op.drop_index("ix_ai_tasks_expires_at", table_name=AI_TASKS)
    if "ix_ai_tasks_billing_status" in indexes:
        op.drop_index("ix_ai_tasks_billing_status", table_name=AI_TASKS)

    columns = _columns(AI_TASKS)
    if "expires_at" in columns:
        op.drop_column(AI_TASKS, "expires_at")
    if "billing_status" in columns:
        op.drop_column(AI_TASKS, "billing_status")
