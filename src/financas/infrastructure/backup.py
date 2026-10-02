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
    """Copy the database (and the images) into ``backups_dir/financas-<timestamp>/``.

    The copy is built in a temporary folder and renamed only when it is complete, so a failed
    backup never looks like a fresh one. Two backups in the same second get a numeric suffix.
    """
    source = sqlite_path(db_url)
    if not source.is_file():
        raise DomainError("BACKUP_NEEDS_SQLITE_FILE")
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{_PREFIX}{now.strftime(_STAMP)}"
    target = backups_dir / stamp
    suffix = 1
    while target.exists():
        suffix += 1
        target = backups_dir / f"{stamp}-{suffix}"
    work = backups_dir / f".tmp-{target.name}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    try:
        src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        dst = sqlite3.connect(work / "financas.db")
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        if images_dir.is_dir():
            shutil.copytree(images_dir, work / "images")
        work.rename(target)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return target


def last_backup_date(backups_dir: Path) -> dt.date | None:
    """Date of the newest backup folder, or ``None`` if there is none."""
    dates: list[dt.date] = []
    for folder in backups_dir.glob(f"{_PREFIX}*") if backups_dir.is_dir() else []:
        try:
            stamp = folder.name[len(_PREFIX) :][: len("20260101-000000")]
            dates.append(dt.datetime.strptime(stamp, _STAMP).date())
        except ValueError:
            continue
    return max(dates, default=None)


def copy_database(db_url: str, target: Path) -> None:
    """A consistent copy of the SQLite database (SQLite's backup API) at ``target``."""
    source = sqlite_path(db_url)
    if not source.is_file():
        raise DomainError("BACKUP_NEEDS_SQLITE_FILE")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def replace_database(db_url: str, replacement: Path) -> None:
    """Swap ``replacement`` in as the database file (the engines must be disposed first)."""
    destination = sqlite_path(db_url)
    for suffix in ("-wal", "-shm", "-journal"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)
    shutil.move(str(replacement), destination)
    for suffix in ("-wal", "-shm", "-journal"):
        replacement.with_name(replacement.name + suffix).unlink(missing_ok=True)
