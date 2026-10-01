"""Consistent backups: SQLite's backup API plus ``data/images/`` (CLAUDE.md section 14)."""

import datetime as dt
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

from financas.domain.errors import DomainError

_STAMP = "%Y%m%d-%H%M%S"
_PREFIX = "financas-"


def sqlite_path(db_url: str) -> Path:
    url = make_url(db_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        raise DomainError("BACKUP_NEEDS_SQLITE_FILE")
    return Path(url.database)


def make_backup(db_url: str, images_dir: Path, backups_dir: Path, now: dt.datetime) -> Path:
    """Copy the database (and the images) into ``backups_dir/financas-<timestamp>/``."""
    source = sqlite_path(db_url)
    target = backups_dir / f"{_PREFIX}{now.strftime(_STAMP)}"
    target.mkdir(parents=True, exist_ok=False)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target / "financas.db")
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if images_dir.is_dir():
        shutil.copytree(images_dir, target / "images")
    return target


def last_backup_date(backups_dir: Path) -> dt.date | None:
    """Date of the newest backup folder, or ``None`` if there is none."""
    dates: list[dt.date] = []
    for folder in backups_dir.glob(f"{_PREFIX}*") if backups_dir.is_dir() else []:
        try:
            dates.append(dt.datetime.strptime(folder.name[len(_PREFIX) :], _STAMP).date())
        except ValueError:
            continue
    return max(dates, default=None)
