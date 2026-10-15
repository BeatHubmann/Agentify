"""Single use of an intent confirmation, recorded append-only (A§5.2, A§8.2, invariant 7).

`intent_confirmation` is a history table: A§5.2 gives the application role no UPDATE on it, yet
0001 granted `UPDATE (consumed_by_run)`. Consumption is now its own row, unique per confirmation
and per run; the old column keeps its existing values but nothing writes it, and its grant is
revoked. It stays because earlier migrations' grants name it.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"

_SCOPE = "(string_to_array(current_setting('agentify.engagement_ids', true), ','))"
_PROCESS_IN_SCOPE = f"process_id IN (SELECT id FROM process WHERE engagement_id = ANY {_SCOPE})"  # noqa: S608 - built from constants only


def upgrade() -> None:
    op.execute("""
    CREATE TABLE intent_use (
        confirmation_id TEXT PRIMARY KEY REFERENCES intent_confirmation(id),
        process_id TEXT NOT NULL REFERENCES process(id),
        run_id TEXT NOT NULL UNIQUE REFERENCES run(id) DEFERRABLE INITIALLY DEFERRED,  -- admitted next
        used_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    INSERT INTO intent_use (confirmation_id, process_id, run_id)
        SELECT id, process_id, consumed_by_run FROM intent_confirmation
        WHERE consumed_by_run IS NOT NULL;
    """)
    op.execute("ALTER TABLE intent_use ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY engagement_scope ON intent_use USING ({_PROCESS_IN_SCOPE}) "
        f"WITH CHECK ({_PROCESS_IN_SCOPE})"
    )


def grant_application_role(role: str) -> list[str]:
    return [
        f"REVOKE UPDATE (consumed_by_run) ON intent_confirmation FROM {role}",
        f"GRANT SELECT, INSERT ON intent_use TO {role}",
    ]
