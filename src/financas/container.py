"""Composition root: the only place that knows the concrete classes."""

import datetime as dt
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from financas.domain.ports import Clock, ImageStore, UnitOfWork
from financas.infrastructure.backup import last_backup_date, make_backup
from financas.infrastructure.clock import SystemClock
from financas.infrastructure.db.engine import make_engine, make_session_factory
from financas.infrastructure.db.migrate import needs_upgrade, upgrade_to_head
from financas.infrastructure.db.repositories import SqlUnitOfWork
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.images import FileImageStore
from financas.infrastructure.settings import Settings


@dataclass
class Container:
    settings: Settings

    @cached_property
    def uow(self) -> UnitOfWork:
        return SqlUnitOfWork(make_session_factory(make_engine(self.settings.db_url)))

    @cached_property
    def images(self) -> ImageStore:
        return FileImageStore(self.settings.images_dir)

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

    def seed(self) -> int:
        """Create the initial categories that are missing; return how many were created."""
        return seed_categories(self.uow)

    def backup(self, now: dt.datetime | None = None) -> Path:
        """Copy the database and the images into ``data/backups/``."""
        return make_backup(
            self.settings.db_url,
            self.settings.images_dir,
            self.settings.backups_dir,
            now or dt.datetime.now(),
        )

    def last_backup_date(self) -> dt.date | None:
        return last_backup_date(self.settings.backups_dir)


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings())
