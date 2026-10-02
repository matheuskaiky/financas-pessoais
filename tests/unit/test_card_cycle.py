import datetime as dt

import pytest

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import (
    AssignmentReason,
    assign_statement,
    best_purchase_day,
    closing_date,
    due_date,
    statement_dates,
)

D = dt.date
YM = YearMonth


@pytest.mark.parametrize(
    ("purchase", "month", "reason", "closes", "due"),
    [
        # CLAUDE.md 9.3: card closing on day 25, due on day 5
        (
            D(2026, 7, 20),
            YM(2026, 7),
            AssignmentReason.BEFORE_CLOSING,
            D(2026, 7, 25),
            D(2026, 8, 5),
        ),
        (
            D(2026, 7, 25),
            YM(2026, 7),
            AssignmentReason.ON_CLOSING_DAY,
            D(2026, 7, 25),
            D(2026, 8, 5),
        ),
        (
            D(2026, 7, 26),
            YM(2026, 8),
            AssignmentReason.AFTER_CLOSING,
            D(2026, 8, 25),
            D(2026, 9, 5),
        ),
        (
            D(2026, 12, 26),
            YM(2027, 1),
            AssignmentReason.AFTER_CLOSING,
            D(2027, 1, 25),
            D(2027, 2, 5),
        ),
    ],
)
def test_statement_assignment_table(
    purchase: dt.date, month: YearMonth, reason: AssignmentReason, closes: dt.date, due: dt.date
) -> None:
    result = assign_statement(purchase, closing_day=25, due_day=5)
    assert result.month == month
    assert result.reason is reason
    assert (result.closing_date, result.due_date) == (closes, due)
    assert result.purchase_date == purchase
    assert result.closing_day == 25


def test_closing_day_31_uses_the_last_day_of_short_months() -> None:
    assert closing_date(YM(2026, 2), 31) == D(2026, 2, 28)
    assert closing_date(YM(2028, 2), 31) == D(2028, 2, 29)
    assert closing_date(YM(2026, 4), 31) == D(2026, 4, 30)
    # a purchase on Feb 28 is still on the closing day
    result = assign_statement(D(2026, 2, 28), closing_day=31, due_day=10)
    assert result.month == YM(2026, 2) and result.reason is AssignmentReason.ON_CLOSING_DAY
    # March 1 already belongs to March's statement
    assert assign_statement(D(2026, 3, 1), 31, 10).month == YM(2026, 3)


def test_due_date_in_the_same_month_when_due_day_is_after_closing() -> None:
    assert due_date(YM(2026, 7), closing_day=10, due_day=17) == D(2026, 7, 17)
    assert statement_dates(YM(2026, 7), 10, 17) == (D(2026, 7, 10), D(2026, 7, 17))


def test_due_date_next_month_when_due_day_is_not_after_closing() -> None:
    assert due_date(YM(2026, 7), closing_day=25, due_day=5) == D(2026, 8, 5)
    assert due_date(YM(2026, 7), closing_day=10, due_day=10) == D(2026, 8, 10)
    assert due_date(YM(2026, 12), closing_day=25, due_day=5) == D(2027, 1, 5)
    assert due_date(YM(2026, 1), closing_day=20, due_day=31) == D(2026, 1, 31)
    assert due_date(YM(2026, 1), closing_day=31, due_day=30) == D(2026, 2, 28)


def test_year_rollover() -> None:
    result = assign_statement(D(2026, 12, 31), closing_day=15, due_day=22)
    assert result.month == YM(2027, 1)
    assert (result.closing_date, result.due_date) == (D(2027, 1, 15), D(2027, 1, 22))


@pytest.mark.parametrize(("closing", "best"), [(25, 26), (1, 2), (30, 31), (31, 1)])
def test_best_purchase_day_is_the_day_after_closing(closing: int, best: int) -> None:
    assert best_purchase_day(closing) == best
    assert assign_statement(D(2026, 7, 1), closing, 5).best_purchase_day == best


@pytest.mark.parametrize("day", [0, 32, -1])
def test_invalid_days_are_rejected(day: int) -> None:
    with pytest.raises(DomainError) as exc:
        closing_date(YM(2026, 7), day)
    assert exc.value.code == "INVALID_CARD_DAY"
    with pytest.raises(DomainError):
        assign_statement(D(2026, 7, 1), closing_day=25, due_day=day)


@pytest.mark.parametrize(
    ("month", "closing_day", "due_day", "due"),
    [
        # February 2027 has 28 days: a due day that clamps to the 28th is not after closing
        (YM(2027, 2), 28, 29, D(2027, 3, 29)),
        (YM(2027, 2), 28, 30, D(2027, 3, 30)),
        (YM(2027, 2), 28, 31, D(2027, 3, 31)),
        (YM(2027, 2), 27, 30, D(2027, 2, 28)),  # still after closing (27th) once clamped
        (YM(2027, 1), 28, 30, D(2027, 1, 30)),
        (YM(2028, 2), 28, 29, D(2028, 2, 29)),  # leap year: the 29th exists
        (YM(2027, 4), 30, 31, D(2027, 5, 31)),
    ],
)
def test_due_date_is_always_after_the_closing_date(
    month: YearMonth, closing_day: int, due_day: int, due: dt.date
) -> None:
    closes, got = statement_dates(month, closing_day, due_day)
    assert got == due and got > closes


def test_due_after_closing_for_every_day_pair_in_a_leap_and_a_common_year() -> None:
    for year in (2027, 2028):
        for month in range(1, 13):
            for closing in range(1, 32):
                for due in (1, 5, 10, 15, 20, 25, 28, 29, 30, 31):
                    closes, got = statement_dates(YM(year, month), closing, due)
                    assert got > closes, (year, month, closing, due)
