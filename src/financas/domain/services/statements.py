"""Statement status, reconciliation and card limit (CLAUDE.md 9.5). Pure functions."""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.models import StatementStatus, TransactionKind


def statement_status(
    today: dt.date,
    closing_date: dt.date,
    previous_closing_date: dt.date,
    total_cents: int,
    paid_cents: int,
) -> StatementStatus:
    """future: not open yet; open: takes purchases; closed: unpaid; paid: payments >= total."""
    if today <= previous_closing_date:
        return StatementStatus.FUTURE
    if today <= closing_date:
        return StatementStatus.OPEN
    return StatementStatus.PAID if paid_cents >= total_cents else StatementStatus.CLOSED


@dataclass(frozen=True)
class Reconciliation:
    entered_cents: int
    informed_cents: int | None
    difference_cents: int | None  # informed - entered; ``None`` until the bank total is informed


def reconcile(entered_cents: int, informed_cents: int | None) -> Reconciliation:
    difference = None if informed_cents is None else informed_cents - entered_cents
    return Reconciliation(entered_cents, informed_cents, difference)


class LimitAlert(StrEnum):
    NONE = "none"
    WARNING = "warning"  # 80% or more
    EXCEEDED = "exceeded"  # 100% or more
    NOT_INFORMED = "not_informed"  # no limit set: never shown as 0%


@dataclass(frozen=True)
class LimitUsage:
    committed_cents: int
    limit_cents: int | None
    available_cents: int | None
    percent: float | None
    alert: LimitAlert


def limit_usage(committed_cents: int, limit_cents: int | None) -> LimitUsage:
    """``committed`` is everything not yet paid, future installments included (a credit is 0)."""
    committed = max(committed_cents, 0)
    if limit_cents is None:
        return LimitUsage(committed, None, None, None, LimitAlert.NOT_INFORMED)
    percent = committed / limit_cents * 100 if limit_cents else 100.0
    if percent >= 100:
        alert = LimitAlert.EXCEEDED
    elif percent >= 80:
        alert = LimitAlert.WARNING
    else:
        alert = LimitAlert.NONE
    return LimitUsage(committed, limit_cents, limit_cents - committed, percent, alert)


@dataclass(frozen=True)
class StatementAmounts:
    total_cents: int  # purchases and charges minus refunds
    paid_cents: int  # payments (transfer legs on the card)

    @property
    def outstanding_cents(self) -> int:
        return self.total_cents - self.paid_cents


def statement_amounts(entries: Iterable[tuple[TransactionKind, int]]) -> StatementAmounts:
    """Total and paid from ``(kind, amount_cents)`` pairs of one statement's entries."""
    total = paid = 0
    for kind, amount in entries:
        if kind in (TransactionKind.EXPENSE, TransactionKind.REFUND):
            total -= amount
        elif kind is TransactionKind.TRANSFER:
            paid += amount
    return StatementAmounts(total, paid)
