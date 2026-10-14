"""Apply database migrations and grant the application role its least privileges."""

import importlib.util
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"


def upgrade(admin_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", admin_url)
    command.upgrade(config, "head")


def create_application_role(admin_url: str, role: str, password: str) -> None:
    """Create the least-privileged application role and apply every migration's grants."""
    with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        for path in sorted((MIGRATIONS / "versions").glob("*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load migration {path.name}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            grants = getattr(module, "grant_application_role", None)
            for statement in grants(role) if grants else []:
                connection.execute(
                    sql.SQL(statement)
                )  # statements are constants from the migrations
