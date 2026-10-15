"""The budget ledger (A§17.3, OP-13): per-call reservations and per-scope limits, in micro-USD.

A reservation holds a call's worst-case cost until it settles to the actual cost, or is released
when the call never completes. A row names every scope it counts against (run, process,
engagement, deployment), so one query sums any scope.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"

_SCOPE = "(string_to_array(current_setting('agentify.engagement_ids', true), ','))"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE budget_limit (
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('process', 'engagement', 'deployment')),
        scope_id TEXT NOT NULL,
        period TEXT NOT NULL DEFAULT 'total' CHECK (period = 'total'),
        limit_microusd BIGINT NOT NULL CHECK (limit_microusd >= 0),
        set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (scope_kind, scope_id, period)
    );
    CREATE TABLE budget_reservation (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES run(id),
        process_id TEXT NOT NULL REFERENCES process(id),
        engagement_id TEXT NOT NULL REFERENCES engagement(id),
        deployment TEXT NOT NULL,
        model_id TEXT NOT NULL,
        reserved_microusd BIGINT NOT NULL CHECK (reserved_microusd >= 0),
        settled_microusd BIGINT CHECK (settled_microusd >= 0),
        state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'settled', 'released')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        closed_at TIMESTAMPTZ,
        CHECK ((state = 'settled') = (settled_microusd IS NOT NULL))
    );
    CREATE INDEX budget_reservation_open ON budget_reservation (run_id) WHERE state = 'open';
    CREATE INDEX budget_reservation_by_run ON budget_reservation (run_id);
    CREATE INDEX budget_reservation_by_process ON budget_reservation (process_id);
    CREATE INDEX budget_reservation_by_engagement ON budget_reservation (engagement_id);
    CREATE INDEX budget_reservation_by_deployment ON budget_reservation (deployment);
    """)
    op.execute("ALTER TABLE budget_reservation ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY engagement_scope ON budget_reservation USING (engagement_id = ANY {_SCOPE}) "
        f"WITH CHECK (engagement_id = ANY {_SCOPE})"
    )


def grant_application_role(role: str) -> list[str]:
    return [
        f"GRANT SELECT ON budget_limit TO {role}",
        f"GRANT SELECT, INSERT ON budget_reservation TO {role}",
        f"GRANT UPDATE (state, settled_microusd, closed_at) ON budget_reservation TO {role}",
    ]
