"""Presentation of colors and images (9.9): defaults, contrast and inheritance."""

import zlib
from dataclasses import dataclass

from financas.domain.models import Account, Institution

# Fixed palette for items without a chosen color; picked by a stable hash of the slug/id.
DEFAULT_PALETTE = (
    "#0F5C45", "#2F5D8C", "#8A5A00", "#6B3FA0", "#B3283C",
    "#00677F", "#4F6B1F", "#9A3B72", "#0A2E24", "#7C4A2D",
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


CARD_INK = "#0F172A"  # the dark text of a light card: deep slate, softer than pure black
_AA = 4.5


def card_text_color(background: str) -> str:
    """Text colour for a card face: deep slate on light/bright colours, white on rich/dark ones.

    Picks whichever of white and slate has the better WCAG contrast, and only falls back to pure
    black when neither reaches AA (4.5:1), which happens in a narrow band of mid-luminance colours.
    """
    white = contrast_ratio(background, "#FFFFFF")
    ink = contrast_ratio(background, CARD_INK)
    best, color = (white, "#FFFFFF") if white >= ink else (ink, CARD_INK)
    if best >= _AA:
        return color
    return readable_text_color(background)


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
