"""CLAUDE.md 9.3: the card closes N days before it is due; dates are derived from the due date."""

import datetime as dt

import pytest

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import (
    AssignmentReason,
    assign_statement,
    closing_date,
    due_date,
    explicit_assignment,
    is_frozen,
    last_day_in_statement,
    statement_dates,
)
from financas.domain.services.countdown import Countdown, CountdownKind, countdown

D = dt.date
YM = YearMonth
DUE, N = 5, 11  # the spec example: due on day 5, closing 11 days before (BB closes on the 25th)


@pytest.mark.parametrize(
    ("purchase", "month", "reason", "closes", "due"),
    [
        (
            D(2026, 7, 20),
            YM(2026, 7),
            AssignmentReason.BEFORE_CLOSING,
            D(2026, 7, 25),
            D(2026, 8, 5),
        ),
        (
            D(2026, 7, 24),
            YM(2026, 7),
            AssignmentReason.BEFORE_CLOSING,
            D(2026, 7, 25),
            D(2026, 8, 5),
        ),
        # on the closing date the purchase already goes to the next statement
        (
            D(2026, 7, 25),
            YM(2026, 8),
            AssignmentReason.ON_CLOSING_DAY,
            D(2026, 8, 25),
            D(2026, 9, 5),
        ),
        (
            D(2026, 7, 26),
            YM(2026, 8),
            AssignmentReason.AFTER_CLOSING,
            D(2026, 8, 25),
            D(2026, 9, 5),
        ),
        # September has 30 days, so the closing date is the 24th, not the 25th
        (
            D(2026, 9, 23),
            YM(2026, 9),
            AssignmentReason.BEFORE_CLOSING,
            D(2026, 9, 24),
            D(2026, 10, 5),
        ),
        (
            D(2026, 9, 24),
            YM(2026, 10),
            AssignmentReason.ON_CLOSING_DAY,
            D(2026, 10, 25),
            D(2026, 11, 5),
        ),
        (
            D(2026, 12, 24),
            YM(2026, 12),
            AssignmentReason.BEFORE_CLOSING,
            D(2026, 12, 25),
            D(2027, 1, 5),
        ),
        (
            D(2026, 12, 25),
            YM(2027, 1),
            AssignmentReason.ON_CLOSING_DAY,
            D(2027, 1, 25),
            D(2027, 2, 5),
        ),
        (
            D(2026, 12, 26),
            YM(2027, 1),
            AssignmentReason.AFTER_CLOSING,
            D(2027, 1, 25),
            D(2027, 2, 5),
        ),
        # in February the closing date follows the due date: the 22nd, not the 25th
        (
            D(2027, 2, 21),
            YM(2027, 2),
            AssignmentReason.BEFORE_CLOSING,
            D(2027, 2, 22),
            D(2027, 3, 5),
        ),
        (
            D(2027, 2, 22),
            YM(2027, 3),
            AssignmentReason.ON_CLOSING_DAY,
            D(2027, 3, 25),
            D(2027, 4, 5),
        ),
    ],
)
def test_statement_assignment_table(
    purchase: dt.date, month: YearMonth, reason: AssignmentReason, closes: dt.date, due: dt.date
) -> None:
    result = assign_statement(purchase, due_day=DUE, days_before_due=N)
    assert result.month == month and result.reason is reason
    assert (result.closing_date, result.due_date) == (closes, due)
    assert result.purchase_date == purchase
    assert result.best_purchase_date == closes


@pytest.mark.parametrize(
    ("card", "due", "days", "closing_day", "month"),
    [
        ("BB", 5, 11, 24, YM(2026, 9)),  # due 05/10; closes on the 24th in a 30-day month
        ("BB", 5, 11, 25, YM(2026, 8)),  # due 05/09; closes on the 25th in a 31-day month
        ("Nubank", 26, 7, 19, YM(2026, 8)),  # due 26/08... closes 19/08
        ("Inter", 20, 6, 14, YM(2026, 8)),  # due 20/08... closes 14/08
    ],
)
def test_the_real_cards_of_the_owner(
    card: str, due: int, days: int, closing_day: int, month: YearMonth
) -> None:
    closes, _ = statement_dates(month, due, days)
    assert closes == dt.date(month.year, month.month, closing_day), card
    day_before = assign_statement(closes - dt.timedelta(days=1), due, days)
    on_the_day = assign_statement(closes, due, days)
    assert day_before.month == month  # still on this statement
    assert on_the_day.month == month.add_months(1)  # from the closing day on: the next one


@pytest.mark.parametrize(
    ("month", "closes", "due"),
    [
        (YM(2026, 7), D(2026, 7, 25), D(2026, 8, 5)),
        (YM(2026, 9), D(2026, 9, 24), D(2026, 10, 5)),  # 30-day month: the 24th
        (YM(2026, 11), D(2026, 11, 24), D(2026, 12, 5)),
        (YM(2026, 12), D(2026, 12, 25), D(2027, 1, 5)),  # year rollover
        (YM(2027, 2), D(2027, 2, 22), D(2027, 3, 5)),
        (YM(2028, 2), D(2028, 2, 23), D(2028, 3, 5)),  # leap year
    ],
)
def test_closing_follows_the_due_date_through_the_year(
    month: YearMonth, closes: dt.date, due: dt.date
) -> None:
    assert statement_dates(month, DUE, N) == (closes, due)
    assert closing_date(month, DUE, N) == closes and due_date(month, DUE, N) == due


def test_a_closing_in_the_month_before_the_due_month() -> None:
    # due on the 5th, closing 7 days before: the statement closes in the previous month
    assert statement_dates(YM(2026, 7), 5, 7) == (D(2026, 7, 29), D(2026, 8, 5))


def test_due_and_closing_in_the_same_month() -> None:
    # due on the 17th, closing 7 days before: closes on the 10th of the same month
    assert statement_dates(YM(2026, 7), 17, 7) == (D(2026, 7, 10), D(2026, 7, 17))
    result = assign_statement(D(2026, 7, 11), due_day=17, days_before_due=7)
    assert result.month == YM(2026, 8) and result.closing_date == D(2026, 8, 10)


def test_due_day_31_uses_the_last_day_of_short_months() -> None:
    assert statement_dates(YM(2026, 2), 31, 7) == (D(2026, 2, 21), D(2026, 2, 28))
    assert statement_dates(YM(2026, 4), 31, 7) == (D(2026, 4, 23), D(2026, 4, 30))
    assert statement_dates(YM(2028, 2), 31, 7) == (D(2028, 2, 22), D(2028, 2, 29))
    assert statement_dates(YM(2026, 3), 31, 7) == (D(2026, 3, 24), D(2026, 3, 31))


def test_exactly_one_due_date_per_closing_month_for_every_setting() -> None:
    """The month of a statement identifies it: no month is skipped and none appears twice."""
    for year in (2027, 2028):  # a common and a leap year
        for due in range(1, 32):
            for days in range(1, 28):
                seen: list[YearMonth] = []
                for month in range(1, 13):
                    closes, due_on = statement_dates(YM(year, month), due, days)
                    assert YearMonth.from_date(closes) == YM(year, month), (year, month, due, days)
                    assert due_on - closes == dt.timedelta(days=days)
                    matches = [
                        offset
                        for offset in (0, 1, 2)
                        if YearMonth.from_date(
                            YM(year, month).add_months(offset).day(due) - dt.timedelta(days=days)
                        )
                        == YM(year, month)
                    ]
                    assert len(matches) == 1, (year, month, due, days, matches)
                    seen.append(YearMonth.from_date(due_on))
                assert len(set(seen)) == 12, (year, due, days)  # twelve different due dates


def test_the_settings_are_a_recipe_for_new_statements_only() -> None:
    old = statement_dates(YM(2026, 7), DUE, N)
    changed = statement_dates(YM(2026, 7), 10, 7)  # the user changes the card later
    assert (
        old == (D(2026, 7, 25), D(2026, 8, 5)) and changed != old
    )  # a stored statement is not touched


def test_best_purchase_date_is_the_closing_date_itself() -> None:
    result = assign_statement(D(2026, 7, 1), DUE, N)
    assert result.closing_date == D(2026, 7, 25) and result.best_purchase_date == D(2026, 7, 25)
    after = assign_statement(D(2026, 7, 26), DUE, N)
    assert after.best_purchase_date == D(2026, 8, 25)


def test_last_day_in_a_statement_and_freezing() -> None:
    assert last_day_in_statement(D(2026, 9, 24)) == D(2026, 9, 23)
    assert last_day_in_statement(D(2026, 10, 1)) == D(2026, 10, 1)  # never leaves the month
    assert not is_frozen(D(2026, 9, 24), D(2026, 9, 23))
    assert is_frozen(D(2026, 9, 24), D(2026, 9, 24))  # frozen on the closing date itself


def test_stored_dates_decide_where_a_purchase_goes() -> None:
    known = {YM(2026, 9): (D(2026, 9, 28), D(2026, 10, 9))}  # edited by hand
    assert assign_statement(D(2026, 9, 27), DUE, N, known).month == YM(2026, 9)
    assert assign_statement(D(2026, 9, 28), DUE, N, known).month == YM(2026, 10)


def test_explicit_assignment_keeps_the_dates_of_the_chosen_month() -> None:
    result = explicit_assignment(YM(2026, 9), DUE, N, purchase=D(2026, 7, 26))
    assert result.reason is AssignmentReason.EXPLICIT and result.month == YM(2026, 9)
    assert (result.closing_date, result.due_date) == (D(2026, 9, 24), D(2026, 10, 5))


@pytest.mark.parametrize("due", [0, 32, -1])
def test_invalid_due_day(due: int) -> None:
    with pytest.raises(DomainError) as exc:
        statement_dates(YM(2026, 7), due, 7)
    assert exc.value.code == "INVALID_CARD_DAY"


@pytest.mark.parametrize("days", [0, 28, 31, -3])
def test_invalid_days_before_due(days: int) -> None:
    with pytest.raises(DomainError) as exc:
        statement_dates(YM(2026, 7), 5, days)
    assert exc.value.code == "INVALID_DAYS_BEFORE_DUE"


# --- day counts (card face) ---


@pytest.mark.parametrize(
    ("target", "kind", "days"),
    [
        (dt.date(2026, 7, 25), CountdownKind.TODAY, 0),
        (dt.date(2026, 7, 26), CountdownKind.TOMORROW, 0),
        (dt.date(2026, 7, 27), CountdownKind.IN_DAYS, 2),
        (dt.date(2026, 7, 30), CountdownKind.IN_DAYS, 5),
        (dt.date(2026, 7, 24), CountdownKind.PAST, 1),
        (dt.date(2026, 7, 1), CountdownKind.PAST, 24),
        (dt.date(2027, 1, 5), CountdownKind.IN_DAYS, 164),  # across a year end
    ],
)
def test_countdown_codes(target: dt.date, kind: CountdownKind, days: int) -> None:
    assert countdown(target, dt.date(2026, 7, 25)) == Countdown(kind, days)
