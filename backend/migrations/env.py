"""Alembic environment. Migrations are plain SQL; the application itself uses psycopg directly."""

from alembic import context
from sqlalchemy import create_engine

url = context.config.get_main_option("sqlalchemy.url")
if url is None:
    raise RuntimeError("sqlalchemy.url is not configured")

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
