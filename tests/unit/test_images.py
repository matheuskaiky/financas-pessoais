import pytest

from financas.domain.errors import DomainError
from financas.domain.services.images import MAX_IMAGE_BYTES, detect_image_type

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16


def test_detects_by_magic_bytes() -> None:
    assert detect_image_type(PNG) == "png"
    assert detect_image_type(JPEG) == "jpeg"
    assert detect_image_type(WEBP) == "webp"


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a....", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", b"RIFF....WAVE"],
)
def test_rejects_other_types(data: bytes) -> None:
    with pytest.raises(DomainError) as exc:
        detect_image_type(data)
    assert exc.value.code == "IMAGE_TYPE_NOT_ALLOWED"


def test_rejects_too_large_images() -> None:
    with pytest.raises(DomainError) as exc:
        detect_image_type(PNG + b"\x00" * MAX_IMAGE_BYTES)
    assert exc.value.code == "IMAGE_TOO_LARGE"
