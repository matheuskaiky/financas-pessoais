"""Small helpers shared by ``app.py`` and the route modules (``routes/``).

Kept apart from ``app.py`` so route modules never import the application factory (no cycles).
"""

from enum import StrEnum
from typing import TypedDict

from financas.domain.errors import DomainError
from financas.domain.models import Account, Category, Institution
from financas.interfaces import appearance


def enum_of[E: StrEnum](cls: type[E], value: str) -> E:
    """A form choice as an enum; tampered data becomes a pt-BR error, never a 500."""
    try:
        return cls(value)
    except ValueError:
        raise DomainError("INVALID_CHOICE") from None


def int_of(value: str, code: str = "INVALID_NUMBER") -> int:
    """A required integer from a form; bad text raises ``DomainError(code)``."""
    try:
        return int(value.strip())
    except ValueError:
        raise DomainError(code) from None


def opt_int(value: str | None) -> int | None:
    """A lenient optional integer (query parameters): empty or bad text is ``None``."""
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


class Lookups(TypedDict):
    """The catalogue every page needs: institutions, accounts and categories with their looks."""

    institutions: dict[str, Institution]
    accounts: list[Account]
    categories: list[Category]
    account_looks: dict[str, appearance.Look]
    institution_looks: dict[str, appearance.Look]
    category_colors: dict[str, str]
