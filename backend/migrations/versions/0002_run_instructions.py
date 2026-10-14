"""Runs carry the instructions they were admitted with (router or API).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("ALTER TABLE run ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
