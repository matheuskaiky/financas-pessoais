"""Composition root: the only place that knows the concrete classes."""

import datetime as dt
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine

from financas.application.imports.model import LegacyWorkbook
from financas.domain.ports import Clock, ImageStore, UnitOfWork
from financas.infrastructure.backup import (
    copy_database,
    last_backup_date,
    make_backup,
    replace_database,
    sqlite_path,
)
from financas.infrastructure.clock import SystemClock
from financas.infrastructure.db.engine import make_engine, make_session_factory
from financas.infrastructure.db.migrate import needs_upgrade, upgrade_to_head
from financas.infrastructure.db.repositories import SqlUnitOfWork
from financas.infrastructure.db.seed import INITIAL_CATEGORIES, seed_categories
from financas.infrastructure.failure_log import FailureLog
from financas.infrastructure.images import FileImageStore
from financas.infrastructure.settings import Settings
from financas.infrastructure.spreadsheet import read_legacy_workbook


@dataclass
class Container:
    settings: Settings

    @cached_property
    def engine(self) -> Engine:
        return make_engine(self.settings.db_url)

    @cached_property
    def uow(self) -> UnitOfWork:
        return SqlUnitOfWork(make_session_factory(self.engine))

    @cached_property
    def images(self) -> ImageStore:
        return FileImageStore(self.settings.images_dir)

    @cached_property
    def failures(self) -> FailureLog:
        return FailureLog(self.settings.logs_dir)

    @cached_property
    def clock(self) -> Clock:
        return SystemClock()

    def migrate(self) -> Path | None:
        """Bring the database to the latest schema.

        An existing database that is behind the code is backed up first (CLAUDE.md section 14);
        the backup folder is returned, or ``None`` when nothing needed to change.
        """
        backup = self.backup() if needs_upgrade(self.settings.db_url) else None
        upgrade_to_head(self.settings.db_url)
        return backup

    def database_exists(self) -> bool:
        """True when the SQLite file exists (an engine on a missing file would create it)."""
        return sqlite_path(self.settings.db_url).is_file()

    def needs_migration(self) -> bool:
        """True when an existing database is behind the code (nothing is written to find out)."""
        return needs_upgrade(self.settings.db_url)

    def working_copy(self, path: Path) -> "Container":
        """A container on a consistent copy of the database, for work that may be thrown away."""
        copy_database(self.settings.db_url, path)
        return Container(self.settings.model_copy(update={"db_url": f"sqlite:///{path}"}))

    def adopt(self, work: "Container") -> None:
        """Replace this database with the working copy's file (after the checks passed)."""
        self.engine.dispose()
        work.engine.dispose()
        replace_database(self.settings.db_url, sqlite_path(work.settings.db_url))

    def read_workbook(self, path: Path) -> LegacyWorkbook:
        """The source sheets of the old workbook (13.1), normalised."""
        return read_legacy_workbook(path)

    @staticmethod
    def initial_category_kinds() -> dict[str, str]:
        """``slug -> expense | income | neutral`` of the initial categories (for the import)."""
        return {slug: kind.value for slug, _, _, kind in INITIAL_CATEGORIES}

    @property
    def import_dir(self) -> Path:
        return self.settings.data_dir / "import"

    def seed(self) -> int:
        """Create the initial categories that are missing; return how many were created."""
        return seed_categories(self.uow)

    def backup(self, now: dt.datetime | None = None) -> Path:
        """Copy the database and the images into ``data/backups/``."""
        return make_backup(
            self.settings.db_url,
            self.settings.images_dir,
            self.settings.backups_dir,
            now or self.clock.now(),
        )

    def last_backup_date(self) -> dt.date | None:
        return last_backup_date(self.settings.backups_dir)


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings())
