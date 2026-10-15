"""Security events and the secret-gate allow-list, both append-only (A§18.3).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"

_SCOPE = "(string_to_array(current_setting('agentify.engagement_ids', true), ','))"
_FINGERPRINT = "fingerprint ~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE security_event (
        seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        engagement_id TEXT REFERENCES engagement(id),
        process_id TEXT REFERENCES process(id),
        run_id TEXT REFERENCES run(id),
        exit TEXT NOT NULL,
        rule TEXT NOT NULL,
        fingerprint TEXT NOT NULL CHECK ({_FINGERPRINT}),
        location TEXT NOT NULL
    );
    CREATE INDEX security_event_by_run ON security_event (run_id, rule, fingerprint);
    CREATE TABLE secret_allowlist_entry (
        seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        engagement_id TEXT NOT NULL REFERENCES engagement(id),
        rule TEXT NOT NULL CHECK (rule <> 'known-value'),
        fingerprint TEXT NOT NULL CHECK ({_FINGERPRINT}),
        action TEXT NOT NULL CHECK (action IN ('allow', 'revoke')),
        justification TEXT NOT NULL CHECK (length(justification) BETWEEN 1 AND 2000),
        admin_id TEXT NOT NULL REFERENCES app_user(id)
    );
    """)
    op.execute("ALTER TABLE security_event ENABLE ROW LEVEL SECURITY")
    # An exit with no engagement (an early HTTP error) may still record; only scoped rows are read.
    op.execute(
        f"CREATE POLICY engagement_scope ON security_event USING (engagement_id = ANY {_SCOPE}) "
        f"WITH CHECK (engagement_id IS NULL OR engagement_id = ANY {_SCOPE})"
    )
    op.execute("ALTER TABLE secret_allowlist_entry ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY engagement_scope ON secret_allowlist_entry "
        f"USING (engagement_id = ANY {_SCOPE}) WITH CHECK (engagement_id = ANY {_SCOPE})"
    )


def grant_application_role(role: str) -> list[str]:
    return [f"GRANT SELECT, INSERT ON security_event, secret_allowlist_entry TO {role}"]
