"""Credit card closing, statement assignment and due dates (CLAUDE.md 9.3). Pure functions.

The card closes ``days_before_due`` days before it is due. A statement is identified by its
closing month; its dates are derived from the due date and stored once, when it is created.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth

MAX_DAYS_BEFORE_DUE = 27  # with at most 27 days each month has exactly one closing date


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
    best_purchase_date: dt.date  # the day after the closing date
    own_closing_date: dt.date | None = None  # closing of the purchase's own month (to explain)


def _check(due_day: int, days_before_due: int) -> None:
    if not 1 <= due_day <= 31:
        raise DomainError("INVALID_CARD_DAY", day=due_day)
    if not 1 <= days_before_due <= MAX_DAYS_BEFORE_DUE:
        raise DomainError("INVALID_DAYS_BEFORE_DUE", days=days_before_due)


def statement_dates(
    month: YearMonth, due_day: int, days_before_due: int
) -> tuple[dt.date, dt.date]:
    """``(closing date, due date)`` of the statement that closes in ``month``.

    Due dates fall on ``due_day`` of a month (the last day of shorter months); the closing date is
    that many days earlier. Among the due dates of ``month``, the next one and the one after,
    exactly one has its closing date inside ``month``.
    """
    _check(due_day, days_before_due)
    for offset in (0, 1, 2):
        due = month.add_months(offset).day(due_day)
        closing = due - dt.timedelta(days=days_before_due)
        if YearMonth.from_date(closing) == month:
            return closing, due
    raise DomainError("NO_STATEMENT_FOR_MONTH")  # unreachable while days_before_due <= 27


def closing_date(month: YearMonth, due_day: int, days_before_due: int) -> dt.date:
    return statement_dates(month, due_day, days_before_due)[0]


def due_date(month: YearMonth, due_day: int, days_before_due: int) -> dt.date:
    return statement_dates(month, due_day, days_before_due)[1]


KnownDates = Mapping[YearMonth, tuple[dt.date, dt.date]]


def _dates(
    month: YearMonth, due_day: int, days_before_due: int, known: KnownDates | None
) -> tuple[dt.date, dt.date]:
    """The stored dates of a statement that already exists, else the ones the settings give.

    Statements are frozen (9.3): once created, neither their closing nor their due date follows
    later changes of the card's settings, and neither does the assignment of new purchases.
    """
    if known and month in known:
        return known[month]
    return statement_dates(month, due_day, days_before_due)


def assign_statement(
    purchase: dt.date, due_day: int, days_before_due: int, known: KnownDates | None = None
) -> StatementAssignment:
    """A purchase on or before the closing date of its own month's statement goes in it.

    ``known`` holds the stored dates of the card's existing statements.
    """
    own = YearMonth.from_date(purchase)
    closes, _ = _dates(own, due_day, days_before_due, known)
    if purchase < closes:
        month, reason = own, AssignmentReason.BEFORE_CLOSING
    elif purchase == closes:
        month, reason = own, AssignmentReason.ON_CLOSING_DAY
    else:
        month, reason = own.add_months(1), AssignmentReason.AFTER_CLOSING
    closing, due = _dates(month, due_day, days_before_due, known)
    return StatementAssignment(
        month, reason, closing, due, purchase, closing + dt.timedelta(days=1), closes
    )


def explicit_assignment(
    month: YearMonth,
    due_day: int,
    days_before_due: int,
    purchase: dt.date | None = None,
    known: KnownDates | None = None,
) -> StatementAssignment:
    """The user chose the statement: same data, reason ``EXPLICIT``."""
    closing, due = _dates(month, due_day, days_before_due, known)
    return StatementAssignment(
        month, AssignmentReason.EXPLICIT, closing, due, purchase, closing + dt.timedelta(days=1)
    )
