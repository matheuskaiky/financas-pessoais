"""Invariants of the domain model."""

import re

from financas.domain.errors import DomainError
from financas.domain.models import CategoryGroup, CategoryKind, TransactionKind

_CATEGORY_FOR_KIND = {
    TransactionKind.EXPENSE: CategoryKind.EXPENSE,
    TransactionKind.INCOME: CategoryKind.INCOME,
    TransactionKind.REFUND: CategoryKind.NEUTRAL,
    TransactionKind.TRANSFER: CategoryKind.NEUTRAL,
}


def validate_sign(kind: TransactionKind, amount_cents: int) -> None:
    """expense < 0, income > 0, refund > 0, transfer either sign but never zero."""
    ok = {
        TransactionKind.EXPENSE: amount_cents < 0,
        TransactionKind.INCOME: amount_cents > 0,
        TransactionKind.REFUND: amount_cents > 0,
        TransactionKind.TRANSFER: amount_cents != 0,
    }[kind]
    if not ok:
        raise DomainError("SIGN_KIND_MISMATCH", kind=kind.value)


def validate_category_kind(kind: TransactionKind, category_kind: CategoryKind) -> None:
    """A transaction's kind must match its category's kind (the spreadsheet violated this)."""
    if _CATEGORY_FOR_KIND[kind] is not category_kind:
        raise DomainError(
            "CATEGORY_KIND_MISMATCH", kind=kind.value, category_kind=category_kind.value
        )


_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def normalize_color(text: str | None) -> str | None:
    """``#RRGGBB`` uppercase, or ``None`` when no color was chosen (9.9)."""
    if text is None or not text.strip():
        return None
    candidate = text.strip()
    if _COLOR.fullmatch(candidate) is None:
        raise DomainError("INVALID_COLOR")
    return candidate.upper()


def validate_transfer_accounts(from_account_id: str | None, to_account_id: str | None) -> None:
    """A transfer has 1 or 2 tracked legs, and never the same account on both sides (9.1)."""
    if from_account_id is None and to_account_id is None:
        raise DomainError("TRANSFER_NEEDS_ACCOUNT")
    if from_account_id == to_account_id:
        raise DomainError("TRANSFER_SAME_ACCOUNT")


def validate_group_kind(group: CategoryGroup, kind: CategoryKind) -> None:
    """A category's group must agree with its kind (income, movement, expense groups)."""
    expected = {
        CategoryGroup.ESSENTIAL: CategoryKind.EXPENSE,
        CategoryGroup.NON_ESSENTIAL: CategoryKind.EXPENSE,
        CategoryGroup.CHARGES: CategoryKind.EXPENSE,
        CategoryGroup.REVIEW: CategoryKind.EXPENSE,
        CategoryGroup.INCOME: CategoryKind.INCOME,
        CategoryGroup.MOVEMENT: CategoryKind.NEUTRAL,
    }[group]
    if kind is not expected:
        raise DomainError("CATEGORY_GROUP_KIND_MISMATCH", group=group.value, kind=kind.value)
