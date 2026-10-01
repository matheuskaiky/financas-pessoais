"""Text helpers: NFC for stored text, an accent-free key for searching and sorting."""

import unicodedata


def clean_text(text: str) -> str:
    """Trim and NFC-normalize text that is stored as data (names, descriptions, notes)."""
    return unicodedata.normalize("NFC", text).strip()


def normalize_search(text: str) -> str:
    """Casefolded, accent-free, single-spaced key (``description_search``, sorting)."""
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(unicodedata.normalize("NFC", without_marks).split())


def slugify(text: str) -> str:
    """ASCII slug (``a-z0-9_``) from a display name: ``"Conta Salário"`` → ``conta_salario``."""
    key = normalize_search(text)
    return "_".join("".join(c if c.isascii() and c.isalnum() else " " for c in key).split())
