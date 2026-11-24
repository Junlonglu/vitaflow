"""ai_tasks: turn_id + turn_slot for per-turn billing idempotency

The billing callback (``POST /api/ai/service/usage``) is a best-effort POST with
bounded retries. Without a turn key, a lost response (or a client retry) creates
a second task row and charges the same turn twice. ``turn_id`` is the id of the
user message being answered — already carried to the AI service for traces — so
every task row can now be tied back to the turn that produced it.

One turn may produce several usage entries (routing groups / mid-turn model
switch), so the uniqueness is on (turn_id, turn_slot), not on turn_id alone.
Historical rows keep NULL and are unaffected (NULL never collides in a unique
index on both MySQL and SQLite).

Revision ID: 0044_ai_task_turn_id
Revises: 0043_version_source_backfill
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0044_ai_task_turn_id"
down_revision = "0043_version_source_backfill"
branch_labels = None
depends_on = None

TABLE = "ai_tasks"
INDEX = "ix_ai_tasks_turn_id"
UNIQUE = "uq_ai_tasks_turn_slot"


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _existing_indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _existing_unique_constraints(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    }


def upgrade() -> None:
    columns = _existing_columns(TABLE)
    if "turn_id" not in columns:
        op.add_column(TABLE, sa.Column("turn_id", sa.BigInteger(), nullable=True))
    if "turn_slot" not in columns:
        op.add_column(TABLE, sa.Column("turn_slot", sa.Integer(), nullable=True))
    if INDEX not in _existing_indexes(TABLE):
        op.create_index(INDEX, TABLE, ["turn_id"], unique=False)
    if UNIQUE not in _existing_unique_constraints(TABLE):
        op.create_unique_constraint(UNIQUE, TABLE, ["turn_id", "turn_slot"])


def downgrade() -> None:
    if UNIQUE in _existing_unique_constraints(TABLE):
        op.drop_constraint(UNIQUE, TABLE, type_="unique")
    if INDEX in _existing_indexes(TABLE):
        op.drop_index(INDEX, table_name=TABLE)
    columns = _existing_columns(TABLE)
    if "turn_slot" in columns:
        op.drop_column(TABLE, "turn_slot")
    if "turn_id" in columns:
        op.drop_column(TABLE, "turn_id")
