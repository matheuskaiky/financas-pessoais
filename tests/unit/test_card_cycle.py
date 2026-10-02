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
