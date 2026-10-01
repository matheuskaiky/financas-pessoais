"""Image validation shared by every ``ImageStore``: magic bytes and size, never extension."""

from financas.domain.errors import DomainError

MAX_IMAGE_BYTES = 512 * 1024


def detect_image_type(data: bytes) -> str:
    """Return ``png``, ``jpeg`` or ``webp``; raise ``DomainError`` for anything else (no SVG)."""
    if len(data) > MAX_IMAGE_BYTES:
        raise DomainError("IMAGE_TOO_LARGE", max_bytes=MAX_IMAGE_BYTES)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise DomainError("IMAGE_TYPE_NOT_ALLOWED")
