"""Inbox, router follow-up turns, parent runs and run results (A§6.6, A§9.1, A§20).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE run ADD COLUMN parent_run_id TEXT REFERENCES run(id);
    ALTER TABLE run ADD COLUMN result_text_scrubbed TEXT;
    CREATE TABLE inbox_item (
        item_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        process_id TEXT NOT NULL REFERENCES process(id),
        parent_item_id TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('agent_question','user_answer','user_steering','confirmation')),
        category TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open','answered','acknowledged')),
        options JSONB NOT NULL DEFAULT '[]',
        body_scrubbed TEXT NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        author_kind TEXT NOT NULL CHECK (author_kind IN ('agent_run','user','system')),
        author_id TEXT NOT NULL,
        run_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (item_id, version)
    );
    CREATE TABLE router_continuation (
        id TEXT PRIMARY KEY,
        process_id TEXT NOT NULL REFERENCES process(id),
        child_run_id TEXT NOT NULL UNIQUE REFERENCES run(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        consumed_by_run TEXT
    );
    """)


def grant_application_role(role: str) -> list[str]:
    return [
        f"GRANT SELECT, INSERT ON inbox_item TO {role}",
        f"GRANT SELECT, INSERT ON router_continuation TO {role}",
        f"GRANT UPDATE (consumed_by_run) ON router_continuation TO {role}",
        f"GRANT UPDATE (result_text_scrubbed) ON run TO {role}",
    ]
