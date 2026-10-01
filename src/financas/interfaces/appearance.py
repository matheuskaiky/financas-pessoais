"""Presentation of colors and images (9.9): defaults, contrast and inheritance."""

import zlib
from dataclasses import dataclass

from financas.domain.models import Account, Institution

# Fixed palette for items without a chosen color; picked by a stable hash of the slug/id.
DEFAULT_PALETTE = (
    "#1E395F", "#0E6151", "#8A4B08", "#6B3FA0", "#B3261E",
    "#00677F", "#5C6B00", "#9A3B72", "#37474F", "#7A5C00",
)  # fmt: skip


@dataclass(frozen=True)
class Look:
    color: str
    text_color: str
    image_id: str | None
    initials: str


def default_color(key: str) -> str:
    return DEFAULT_PALETTE[zlib.crc32(key.encode("utf-8")) % len(DEFAULT_PALETTE)]


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def readable_text_color(background: str) -> str:
    """Black or white, whichever has the better WCAG contrast on ``background``."""
    return (
        "#FFFFFF"
        if contrast_ratio(background, "#FFFFFF") >= contrast_ratio(background, "#000000")
        else "#000000"
    )


def initials(name: str) -> str:
    words = [w for w in name.split() if w[:1].isalnum()]
    letters = "".join(w[0] for w in words[:2]) or name.strip()[:1]
    return letters.upper()


def look(name: str, key: str, color: str | None, image_id: str | None) -> Look:
    shown = color or default_color(key)
    return Look(shown, readable_text_color(shown), image_id, initials(name))


def institution_look(institution: Institution) -> Look:
    return look(institution.name, institution.slug, institution.color, institution.image_id)


def account_look(account: Account, institution: Institution) -> Look:
    """An account without its own color or image uses its institution's (never stored)."""
    parent = institution_look(institution)
    if account.color or account.image_id:
        own = look(
            account.nickname, account.id, account.color or institution.color, account.image_id
        )
        return Look(own.color, own.text_color, account.image_id or parent.image_id, own.initials)
    return Look(parent.color, parent.text_color, parent.image_id, initials(account.nickname))
