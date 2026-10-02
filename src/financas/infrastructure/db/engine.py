"""Engine and session factory. Every SQLite connection gets the same pragmas."""

import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)
_WAL_WARNED: set[bool] = set()  # the fallback is logged once, not on every connection


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, hide_parameters=True)  # errors must not print amounts or text
    if parsed.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def _configure_sqlite(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() not in ("wal", "memory"):
        # Some filesystems (e.g. /mnt/c) cannot do WAL; keep working in the default mode.
        cursor.execute("PRAGMA journal_mode=DELETE")
        if not _WAL_WARNED:
            _WAL_WARNED.add(True)
            log.warning("SQLite WAL is not available here (journal_mode=%s); using DELETE", mode)
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
