"""Run Alembic migrations from code (``financas init``, tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from financas.infrastructure.db.engine import make_engine

_MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    config.attributes["url"] = url
    config.attributes["configure_logging"] = False
    return config


def upgrade_to_head(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


def head_revision(url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def current_revision(url: str) -> str | None:
    """The revision the database is at; ``None`` for a new, empty database."""
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def needs_upgrade(url: str) -> bool:
    """True when a database that already has data is behind the code (a backup is due)."""
    current = current_revision(url)
    return current is not None and current != head_revision(url)
