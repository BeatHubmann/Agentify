"""Run completion (A§8.5, external review R2): `completed_at` marks a terminal run whose completion
effects (the `finished` event, a child's router continuation, the release of open reservations)
are written. They are written in one transaction with it, so a run is never marked complete with
an effect missing, and the sweeper finds every terminal run that is not.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE run ADD COLUMN completed_at TIMESTAMPTZ;
    UPDATE run SET completed_at = COALESCE(finished_at, now()) WHERE state IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed');
    CREATE INDEX run_incomplete ON run (finished_at) WHERE completed_at IS NULL AND state IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed');
    """)


def grant_application_role(role: str) -> list[str]:
    return [f"GRANT UPDATE (completed_at) ON run TO {role}"]
