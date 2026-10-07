"""Selection Mode rules (merge and batch delete): which rows a selection may include.

Pure functions: the clock reads ``today`` once and passes it in. Everything here speaks codes;
``interfaces/messages`` turns them into pt-BR sentences.
"""

import datetime as dt
from enum import StrEnum

MAX_SELECTION = 200  # rows per batch deletion request


class SelectionLock(StrEnum):
    """Why a row cannot be checked at all (no merge, no deletion)."""

    PAST_MONTH = "past_month"  # dated before the current calendar month
    FUTURE_MONTH = "future_month"  # dated after it
    STATEMENT_PAID = "statement_paid"  # a paid statement is history
    PLAN_ALL_PAID = "plan_all_paid"  # an installment plan with nothing left to delete


class MergeBlock(StrEnum):
    """Why a checked row still blocks "Mesclar" (it can be deleted)."""

    PLAN = "plan"
    REFUNDED = "refunded"
    ITEMIZED = "itemized"
    NOT_EXPENSE = "not_expense"


def in_current_month(day: dt.date, today: dt.date) -> bool:
    """Year **and** month: October 2025 is not October 2026."""
    return (day.year, day.month) == (today.year, today.month)


def month_lock(day: dt.date, today: dt.date) -> SelectionLock | None:
    """The window rule (R1): ``None`` inside the current calendar month."""
    if in_current_month(day, today):
        return None
    return (
        SelectionLock.PAST_MONTH
        if (day.year, day.month) < (today.year, today.month)
        else (SelectionLock.FUTURE_MONTH)
    )
