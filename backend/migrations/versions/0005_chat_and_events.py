"""Chat history (append-only), run events, and the durable stream attachment (A§5, A§8.5, A§20).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"

_SCOPE = "(string_to_array(current_setting('agentify.engagement_ids', true), ','))"
_PROCESS_IN_SCOPE = f"process_id IN (SELECT id FROM process WHERE engagement_id = ANY {_SCOPE})"  # noqa: S608 - constants only


def upgrade() -> None:
    op.execute("""
    ALTER TABLE run ADD COLUMN stream_attached_until TIMESTAMPTZ;
    CREATE TABLE chat_message (
        process_id TEXT NOT NULL REFERENCES process(id),
        seq INTEGER NOT NULL,
        author_kind TEXT NOT NULL CHECK (author_kind IN ('user', 'agent_run')),
        author_id TEXT NOT NULL,
        run_id TEXT,
        content_scrubbed TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (process_id, seq)
    );
    CREATE TABLE run_event (
        run_id TEXT NOT NULL REFERENCES run(id),
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload_scrubbed JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (run_id, seq)
    );
    """)
    op.execute("ALTER TABLE chat_message ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY engagement_scope ON chat_message USING ({_PROCESS_IN_SCOPE}) "
        f"WITH CHECK ({_PROCESS_IN_SCOPE})"
    )
    op.execute("ALTER TABLE run_event ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY engagement_scope ON run_event USING (run_id IN (SELECT id FROM run)) "
        "WITH CHECK (run_id IN (SELECT id FROM run))"
    )


def grant_application_role(role: str) -> list[str]:
    return [
        f"GRANT SELECT, INSERT ON chat_message, run_event TO {role}",
        f"GRANT UPDATE (stream_attached_until) ON run TO {role}",
    ]
