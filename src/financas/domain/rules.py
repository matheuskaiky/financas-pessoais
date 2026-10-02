"""Invariants of the domain model."""

import re

from financas.domain.errors import DomainError
from financas.domain.models import (
    CategoryGroup,
    CategoryKind,
    Indexer,
    InvestmentHolding,
    Liquidity,
    RateMode,
    TransactionKind,
)
from financas.domain.services.card_cycle import MAX_DAYS_BEFORE_DUE

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


def validate_card_settings(
    closing_days_before_due: int | None, due_day: int | None, credit_limit_cents: int | None
) -> None:
    """A card needs a due day (1-31) and how many days before it the card closes (1-27, 9.3);
    the limit is optional and not negative."""
    if closing_days_before_due is None or due_day is None:
        raise DomainError("CARD_DAYS_REQUIRED")
    if not 1 <= due_day <= 31:
        raise DomainError("INVALID_CARD_DAY", day=due_day)
    if not 1 <= closing_days_before_due <= MAX_DAYS_BEFORE_DUE:
        raise DomainError("INVALID_DAYS_BEFORE_DUE", days=closing_days_before_due)
    if credit_limit_cents is not None and credit_limit_cents < 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")


def validate_gross_balance(net_cents: int, gross_cents: int | None) -> None:
    """The gross value (before estimated tax) cannot be below the net value."""
    if gross_cents is not None and gross_cents < net_cents:
        raise DomainError("INVALID_GROSS_BALANCE")


def validate_holding(h: InvestmentHolding) -> None:
    """Contract data of a holding. Display only: nothing here computes yield or tax."""
    if not h.name.strip():
        raise DomainError("EMPTY_NAME")
    if h.principal_cents <= 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")
    if (h.rate_mode is None) != (h.rate_bps is None) or (h.rate_bps is not None and h.rate_bps < 0):
        raise DomainError("INVALID_RATE")
    if h.rate_mode in (RateMode.PERCENT_OF_INDEX, RateMode.SPREAD_OVER_INDEX) and (
        h.indexer is None or h.indexer is Indexer.PREFIXED
    ):
        raise DomainError("INVALID_RATE")
    if h.rate_mode is RateMode.FIXED_ANNUAL and h.indexer not in (None, Indexer.PREFIXED):
        raise DomainError("INVALID_RATE")
    if h.liquidity is Liquidity.AT_MATURITY:
        if h.maturity_on is None:
            raise DomainError("MATURITY_REQUIRED")
        if h.liquid_from is not None:
            raise DomainError("INVALID_LIQUIDITY")
    if h.maturity_on is not None and h.maturity_on <= h.applied_on:
        raise DomainError("INVALID_MATURITY")
    if h.liquid_from is not None and h.liquid_from < h.applied_on:
        raise DomainError("INVALID_LIQUIDITY")
