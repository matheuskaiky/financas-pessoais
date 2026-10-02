"""Credit card closing, statement assignment and due dates (CLAUDE.md 9.3). Pure functions."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth


class AssignmentReason(StrEnum):
    BEFORE_CLOSING = "before_closing"
    ON_CLOSING_DAY = "on_closing_day"
    AFTER_CLOSING = "after_closing"
    EXPLICIT = "explicit"  # the user chose the statement


@dataclass(frozen=True)
class StatementAssignment:
    """Which statement a purchase goes to, and why (the UI turns this into a sentence)."""

    month: YearMonth  # the closing month identifies the statement
    reason: AssignmentReason
    closing_date: dt.date
    due_date: dt.date
    purchase_date: dt.date | None
    closing_day: int
    best_purchase_day: int


def _check_day(day: int) -> None:
    if not 1 <= day <= 31:
        raise DomainError("INVALID_CARD_DAY", day=day)


def closing_date(month: YearMonth, closing_day: int) -> dt.date:
    """Closing day of ``month`` (the last day of shorter months)."""
    _check_day(closing_day)
    return month.day(closing_day)


def due_date(month: YearMonth, closing_day: int, due_day: int) -> dt.date:
    """First occurrence of ``due_day`` after the closing date (no business-day adjustment)."""
    _check_day(closing_day)
    _check_day(due_day)
    return month.day(due_day) if due_day > closing_day else month.add_months(1).day(due_day)


def statement_dates(month: YearMonth, closing_day: int, due_day: int) -> tuple[dt.date, dt.date]:
    return closing_date(month, closing_day), due_date(month, closing_day, due_day)


def best_purchase_day(closing_day: int) -> int:
    """The day after closing (day 1 when the card closes on the 31st)."""
    _check_day(closing_day)
    return 1 if closing_day >= 31 else closing_day + 1


def assign_statement(purchase: dt.date, closing_day: int, due_day: int) -> StatementAssignment:
    """A purchase on or before its own month's closing date goes to that month's statement."""
    own = YearMonth.from_date(purchase)
    closes = closing_date(own, closing_day)
    if purchase < closes:
        month, reason = own, AssignmentReason.BEFORE_CLOSING
    elif purchase == closes:
        month, reason = own, AssignmentReason.ON_CLOSING_DAY
    else:
        month, reason = own.add_months(1), AssignmentReason.AFTER_CLOSING
    closing, due = statement_dates(month, closing_day, due_day)
    return StatementAssignment(
        month, reason, closing, due, purchase, closing_day, best_purchase_day(closing_day)
    )


def explicit_assignment(
    month: YearMonth, closing_day: int, due_day: int, purchase: dt.date | None = None
) -> StatementAssignment:
    """The user chose the statement: same data, reason ``EXPLICIT``."""
    closing, due = statement_dates(month, closing_day, due_day)
    return StatementAssignment(
        month, AssignmentReason.EXPLICIT, closing, due, purchase, closing_day,
        best_purchase_day(closing_day),
    )  # fmt: skip
