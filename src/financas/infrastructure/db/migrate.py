"""Run Alembic migrations from code (``financas init``, tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

_MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    config.attributes["url"] = url
    config.attributes["configure_logging"] = False
    return config


def upgrade_to_head(url: str) -> None:
    command.upgrade(alembic_config(url), "head")
