"""Foundation: workspaces, revisions, runs, staging, pins, jobs (A§5.2, slice 1 subset).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None

NON_TERMINAL = "state NOT IN ('committed','unchanged','rolled_back','rollback_failed')"


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE engagement (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        default_output_language TEXT NOT NULL DEFAULT 'en',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE process (
        id TEXT PRIMARY KEY,
        engagement_id TEXT NOT NULL REFERENCES engagement(id),
        name TEXT NOT NULL,
        output_language TEXT NOT NULL DEFAULT 'en',
        quarantined_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE revision (
        id TEXT PRIMARY KEY,
        process_id TEXT NOT NULL REFERENCES process(id),
        parent_id TEXT REFERENCES revision(id),
        run_id TEXT,
        author_kind TEXT NOT NULL CHECK (author_kind IN ('agent_run','user','system')),
        author_id TEXT NOT NULL,
        message TEXT NOT NULL,
        prompt_version TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    CREATE UNIQUE INDEX revision_one_per_run ON revision (run_id) WHERE run_id IS NOT NULL;
    CREATE TABLE revision_path (
        revision_id TEXT NOT NULL REFERENCES revision(id),
        path TEXT NOT NULL,
        blob_sha256 TEXT NOT NULL,
        PRIMARY KEY (revision_id, path)
    );
    CREATE TABLE workspace_head (
        process_id TEXT PRIMARY KEY REFERENCES process(id),
        revision_id TEXT REFERENCES revision(id),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE run (
        id TEXT PRIMARY KEY,
        process_id TEXT NOT NULL REFERENCES process(id),
        kind TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('from_scratch','incremental')),
        state TEXT NOT NULL CHECK (state IN ('admitted','running','gating','committing','rolling_back',
                                             'committed','unchanged','rolled_back','rollback_failed')),
        base_revision_id TEXT REFERENCES revision(id),
        initiated_by TEXT NOT NULL,
        entry_point TEXT NOT NULL,
        intent_confirmation_id TEXT,
        lease_owner TEXT,
        lease_epoch INTEGER NOT NULL DEFAULT 0,
        lease_expires_at TIMESTAMPTZ,
        cancel_requested_at TIMESTAMPTZ,
        staged_paths TEXT[] NOT NULL DEFAULT '{{}}',
        outcome TEXT,
        outcome_detail_scrubbed TEXT,
        error_budget_json JSONB NOT NULL DEFAULT '{{}}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ
    );
    CREATE UNIQUE INDEX run_one_active_per_kind ON run (process_id, kind) WHERE {NON_TERMINAL};
    CREATE TABLE run_staging (
        run_id TEXT NOT NULL REFERENCES run(id),
        path TEXT NOT NULL,
        blob_sha256 TEXT NOT NULL,
        written_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, path)
    );
    CREATE TABLE blob_pin (
        engagement_id TEXT NOT NULL,
        blob_sha256 TEXT NOT NULL,
        pinned_by_run TEXT NOT NULL,
        pinned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (engagement_id, blob_sha256, pinned_by_run)
    );
    CREATE TABLE job (
        id TEXT PRIMARY KEY,
        queue TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE REFERENCES run(id),
        attempts INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','claimed','done')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX job_claimable ON job (queue, created_at) WHERE state = 'queued';
    CREATE TABLE intent_confirmation (
        id TEXT PRIMARY KEY,
        process_id TEXT NOT NULL REFERENCES process(id),
        run_kind TEXT NOT NULL,
        head_revision_id TEXT REFERENCES revision(id),
        confirmed_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        consumed_by_run TEXT UNIQUE
    );
    """)


def grant_application_role(role: str) -> list[str]:
    """History tables are append-only for the application role (invariant 7)."""
    return [
        f"GRANT SELECT, INSERT ON engagement, process, revision, revision_path TO {role}",
        f"GRANT SELECT, INSERT, UPDATE ON workspace_head TO {role}",
        f"GRANT UPDATE (quarantined_at) ON process TO {role}",
        f"GRANT SELECT, INSERT, UPDATE ON run, job TO {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON run_staging TO {role}",
        f"GRANT SELECT, INSERT, DELETE ON blob_pin TO {role}",
        f"GRANT SELECT, INSERT ON intent_confirmation TO {role}",
        f"GRANT UPDATE (consumed_by_run) ON intent_confirmation TO {role}",
    ]
