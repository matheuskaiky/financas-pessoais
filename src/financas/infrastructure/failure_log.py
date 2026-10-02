"""Local log of failures (server, browser and CLI), to find features that do not work.

One JSON object per line in ``data/logs/failures.jsonl``, rotated by size. CLAUDE.md rule 1 holds
here too: the log never carries descriptions, amounts or exception *messages* (a database error
message can embed the values being written). It records what, where and when: the kind of failure,
the request path, the status, the exception class and the code locations of its traceback.
"""

import json
import logging
import threading
import traceback
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from uuid import uuid4

MAX_BYTES = 1_000_000
BACKUPS = 4
MAX_FRAMES = 8
_FIELD_LIMITS = {"message": 300, "file": 200, "target": 200, "path": 200, "ua": 120, "where": 200}
# What a client (the browser) may report: nothing else is accepted into the log.
CLIENT_KINDS = frozenset({"js_error", "unhandled_rejection", "htmx_error"})


def new_failure_id() -> str:
    """A short code the user can read off an error page and look up in the log."""
    return uuid4().hex[:8]


def code_locations(tb: TracebackType | None) -> list[str]:
    """``package/module.py:123 in function`` for the innermost frames, without any values."""
    frames = traceback.extract_tb(tb)[-MAX_FRAMES:]
    return [
        f"{'/'.join(Path(f.filename).parts[-2:])}:{f.lineno} in {f.name}"[: _FIELD_LIMITS["where"]]
        for f in frames
    ]


def describe_exception(exc: BaseException) -> dict[str, object]:
    """Class, domain error code (when there is one) and code locations; never the message."""
    info: dict[str, object] = {
        "error": type(exc).__qualname__,
        "where": code_locations(exc.__traceback__),
    }
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        info["code"] = code[:60]
    if exc.__cause__ is not None:
        info["cause"] = type(exc.__cause__).__qualname__
    return info


def _clip(value: object, limit: int) -> str:
    return str(value).replace("\n", " ")[:limit]


class FailureLog:
    """Append-only failure log. Logging never raises: a broken log must not break the app."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / "failures.jsonl"
        self._lock = threading.Lock()
        self._logger: logging.Logger | None = None
        self._recent_client: deque[float] = deque()

    @staticmethod
    def new_id() -> str:
        return new_failure_id()

    @property
    def path(self) -> Path:
        return self._path

    def _log(self) -> logging.Logger:
        if self._logger is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                self._path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger = logging.Logger(f"financas.failures.{id(self)}")  # private: not in the registry
            logger.addHandler(handler)
            self._logger = logger
        return self._logger

    def record(self, source: str, kind: str, **fields: object) -> str:
        """Write one failure and return its code (also shown to the user on the error page)."""
        failure_id = str(fields.pop("id", None) or new_failure_id())
        entry: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "id": failure_id,
            "source": source,
            "kind": kind,
        }
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, list):
                entry[key] = [_clip(v, _FIELD_LIMITS["where"]) for v in value][:MAX_FRAMES]
            elif isinstance(value, int):
                entry[key] = value
            else:
                entry[key] = _clip(value, _FIELD_LIMITS.get(key, 80))
        try:
            with self._lock:
                self._log().info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            pass
        return failure_id

    def record_exception(self, source: str, kind: str, exc: BaseException, **fields: object) -> str:
        return self.record(source, kind, **describe_exception(exc), **fields)

    def accept_client_report(self, payload: Mapping[str, object], user_agent: str) -> bool:
        """Store a report sent by the browser; ``False`` when it is rejected or rate limited.

        Only whitelisted fields are kept and clipped, and at most 30 reports a minute are
        written, so a page stuck in a loop cannot fill the disk.
        """
        kind = payload.get("kind")
        if kind not in CLIENT_KINDS:
            return False
        now = datetime.now(UTC).timestamp()
        with self._lock:
            while self._recent_client and now - self._recent_client[0] > 60:
                self._recent_client.popleft()
            if len(self._recent_client) >= 30:
                return False
            self._recent_client.append(now)
        status = payload.get("status")
        line = payload.get("line")
        self.record(
            "client",
            str(kind),
            path=payload.get("path"),
            method=payload.get("method"),
            status=status if isinstance(status, int) and 0 <= status < 1000 else None,
            message=payload.get("message"),
            file=payload.get("file"),
            target=payload.get("target"),
            line=line if isinstance(line, int) and 0 <= line < 10**7 else None,
            ua=user_agent,
        )
        return True

    def recent(self, limit: int = 50) -> list[dict[str, object]]:
        """The newest entries first (reads the current file and, if needed, the previous one)."""
        entries: list[dict[str, object]] = []
        for path in (self._path, self._path.with_name(self._path.name + ".1")):
            if not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)  # pyright: ignore[reportUnknownArgumentType]
                if len(entries) >= limit:
                    return entries
        return entries
