"""Users, memberships and row-level security (A§19, OD-7, OD-8).

The API connects as a role without BYPASSRLS and sets `agentify.engagement_ids` per transaction;
row-level security is the second wall behind the service-layer membership checks. The worker role
is created with BYPASSRLS: it executes runs for every engagement by design.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"

_SCOPE = "(string_to_array(current_setting('agentify.engagement_ids', true), ','))"
_PROCESS_IN_SCOPE = f"process_id IN (SELECT id FROM process WHERE engagement_id = ANY {_SCOPE})"  # noqa: S608 - built from constants only

_POLICIES = {
    "engagement": f"id = ANY {_SCOPE}",
    "process": f"engagement_id = ANY {_SCOPE}",
    "workspace_head": _PROCESS_IN_SCOPE,
    "revision": _PROCESS_IN_SCOPE,
    "revision_path": "revision_id IN (SELECT id FROM revision)",
    "run": _PROCESS_IN_SCOPE,
    "run_staging": "run_id IN (SELECT id FROM run)",
    "inbox_item": _PROCESS_IN_SCOPE,
    "intent_confirmation": _PROCESS_IN_SCOPE,
    "router_continuation": _PROCESS_IN_SCOPE,
}


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app_user (
        id TEXT PRIMARY KEY,
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        is_admin BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (issuer, subject)
    );
    CREATE TABLE membership (
        engagement_id TEXT NOT NULL REFERENCES engagement(id),
        user_id TEXT NOT NULL REFERENCES app_user(id),
        role TEXT NOT NULL CHECK (role IN ('consultant', 'waiver_approver', 'reviewer')),
        granted_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (engagement_id, user_id, role)
    );
    """)
    for table, predicate in _POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY engagement_scope ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )


def grant_application_role(role: str) -> list[str]:
    return [
        f"GRANT SELECT, INSERT ON app_user TO {role}",
        f"GRANT UPDATE (display_name) ON app_user TO {role}",
        f"GRANT SELECT, INSERT, DELETE ON membership TO {role}",
    ]
