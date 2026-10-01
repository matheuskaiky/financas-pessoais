"""``ImageStore`` on the filesystem: ``data/images/<uuid>.<ext>`` (9.9)."""

import re
import uuid
from pathlib import Path

from financas.domain.services.images import detect_image_type

_ID = re.compile(r"[0-9a-f]{32}")
_EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}
_TYPES = {ext: image_type for image_type, ext in _EXTENSIONS.items()}


class FileImageStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, data: bytes) -> str:
        extension = _EXTENSIONS[detect_image_type(data)]
        image_id = uuid.uuid4().hex
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / f"{image_id}.{extension}").write_bytes(data)
        return image_id

    def _find(self, image_id: str) -> Path | None:
        if not _ID.fullmatch(image_id):  # never let a user-supplied id reach the filesystem
            return None
        return next(iter(self._root.glob(f"{image_id}.*")), None)

    def open(self, image_id: str) -> tuple[bytes, str] | None:
        path = self._find(image_id)
        if path is None:
            return None
        return path.read_bytes(), _TYPES[path.suffix.lstrip(".")]

    def delete(self, image_id: str) -> None:
        path = self._find(image_id)
        if path is not None:
            path.unlink(missing_ok=True)
