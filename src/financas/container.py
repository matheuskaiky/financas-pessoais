"""Composition root: the only place that knows the concrete classes."""

import datetime as dt
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine

from financas.application.csvfeed.service import FeedWork, FeedWorkspace
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
from financas.infrastructure.db.demo_seed import (
    DemoSummary,
    demo_is_empty,
    seed_demo,
    wipe_demo_files,
)
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

    def close(self) -> None:
        """Release the database connections now, not at garbage collection.

        The ``-wal`` and ``-shm`` files of a SQLite database stay on disk while one is open.
        """
        if "engine" in self.__dict__:
            self.engine.dispose()

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
        return self.settings.import_dir

    def seed(self) -> int:
        """Create the initial categories that are missing; return how many were created."""
        return seed_categories(self.uow)

    def ensure_demo(self) -> bool:
        """Demo mode: migrate, seed the categories and, on an empty database, the demo household.

        Idempotent; returns ``True`` when the demo data was created now. Only demo settings.
        """
        self._require_demo()
        self.migrate()
        self.seed()
        if demo_is_empty(self.uow):
            seed_demo(self.uow, self.clock)
            return True
        return False

    def reset_demo(self) -> DemoSummary:
        """Demo mode: delete ``data/demo.db`` and ``data/demo_images/`` and build the data again."""
        self._require_demo()
        self.close()
        wipe_demo_files(self.settings)
        self.migrate()
        self.seed()
        summary = seed_demo(self.uow, self.clock)  # the same clock as ``ensure_demo``
        self.close()
        return summary

    def _require_demo(self) -> None:
        if not self.settings.demo:
            raise ValueError("NOT_DEMO_SETTINGS")

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

    def feed_workspace(self) -> FeedWorkspace:
        """The database side of a CSV feed run (13.3), for the script and the web page."""
        return _FeedWorkspace(self)


class _FeedWork:
    """A working copy of the database: a container on a copy, discarded unless adopted."""

    def __init__(self, work: Container, path: Path) -> None:
        self.container = work
        self._path = path

    @property
    def uow(self) -> UnitOfWork:
        return self.container.uow

    @property
    def clock(self) -> Clock:
        return self.container.clock

    def migrate(self) -> None:
        self.container.migrate()

    def discard(self) -> None:
        self.container.engine.dispose()
        for suffix in ("", "-wal", "-shm", "-journal"):
            self._path.with_name(self._path.name + suffix).unlink(missing_ok=True)


class _FeedWorkspace:
    """Adapts the container to ``FeedWorkspace`` (working copy: ``data/import/csv_work.db``)."""

    def __init__(self, c: Container) -> None:
        self._c = c

    @property
    def uow(self) -> UnitOfWork:
        return self._c.uow

    @property
    def clock(self) -> Clock:
        return self._c.clock

    def database_exists(self) -> bool:
        return self._c.database_exists()

    def needs_migration(self) -> bool:
        return self._c.needs_migration()

    def backup(self) -> Path:
        return self._c.backup()

    def open_work_copy(self) -> FeedWork:
        path = self._c.import_dir / "csv_work.db"
        return _FeedWork(self._c.working_copy(path), path)

    def adopt(self, work: FeedWork) -> None:
        assert isinstance(work, _FeedWork)
        self._c.adopt(work.container)

    def record_failure(self, error: Exception, origin: str) -> str:
        kind = "server_error" if origin.startswith("/") else "cli_error"
        return self._c.failures.record_exception("csvfeed", kind, error, path=origin)


def build_container(settings: Settings | None = None, *, demo: bool = False) -> Container:
    """The container of the real data, or (``demo=True``) of the demo database and its folders."""
    settings = settings or Settings()
    return Container(settings.for_demo() if demo else settings)
