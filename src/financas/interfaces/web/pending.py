"""Uploaded files waiting between the analysis and the apply (``data/import/pending/``).

The name of a file is its SHA-256 (64 hex characters) and nothing else: paths are never built
from what the browser sent. A file lives until it is applied, or until it is a day old.
"""

import hashlib
import os
import re
from pathlib import Path

TOKEN = re.compile(r"[0-9a-f]{64}")
MAX_AGE_SECONDS = 24 * 60 * 60


def is_token(text: str) -> bool:
    return TOKEN.fullmatch(text) is not None


class PendingFiles:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def _path(self, token: str) -> Path | None:
        return self._dir / f"{token}.csv" if is_token(token) else None

    def save(self, data: bytes) -> str:
        """Store the bytes (atomically) and return the token (their SHA-256)."""
        token = hashlib.sha256(data).hexdigest()
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{token}.csv"
        partial = self._dir / f"{token}.part"
        partial.write_bytes(data)
        os.replace(partial, target)
        return token

    def load(self, token: str) -> bytes | None:
        """The stored bytes, or ``None`` (bad token, missing file or content that changed)."""
        path = self._path(token)
        if path is None or not path.is_file():
            return None
        data = path.read_bytes()
        return data if hashlib.sha256(data).hexdigest() == token else None

    def delete(self, token: str) -> None:
        path = self._path(token)
        if path is not None:
            path.unlink(missing_ok=True)

    def purge(self, now_timestamp: float, max_age: float = MAX_AGE_SECONDS) -> int:
        """Remove the files older than ``max_age`` seconds; return how many."""
        removed = 0
        if not self._dir.is_dir():
            return 0
        for path in self._dir.iterdir():
            if path.suffix not in {".csv", ".part"} or not path.is_file():
                continue
            if now_timestamp - path.stat().st_mtime > max_age:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
