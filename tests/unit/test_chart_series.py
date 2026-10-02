"""Pure maths behind the dashboard charts (sampling dates, month pace, long tail)."""

import datetime as dt

from financas.domain.services.chart_series import (
    RankedRow,
    average_curve,
    cumulative,
    first_crossing,
    series_dates,
    top_with_rest,
)

D = dt.date


def test_series_dates_has_month_ends_weeks_first_date_and_today() -> None:
    dates = series_dates(D(2026, 1, 15), D(2026, 10, 2))
    assert dates[0] == D(2026, 1, 15) and dates[-1] == D(2026, 10, 2)
    for month_end in (D(2026, 1, 31), D(2026, 2, 28), D(2026, 6, 30), D(2026, 9, 30)):
        assert month_end in dates
    assert D(2026, 10, 31) not in dates  # never after today
    weekly = [d for d in dates if d >= D(2026, 7, 2)]
    assert D(2026, 9, 25) in weekly and D(2026, 9, 18) in weekly  # one a week back from today
    assert dates == sorted(set(dates))


def test_series_dates_clamps_to_the_first_date_and_handles_a_single_day() -> None:
    assert series_dates(D(2026, 10, 2), D(2026, 10, 2)) == [D(2026, 10, 2)]
    short = series_dates(D(2026, 9, 20), D(2026, 10, 2))
    assert short[0] == D(2026, 9, 20) and all(d >= D(2026, 9, 20) for d in short)
    assert series_dates(D(2026, 12, 1), D(2026, 10, 2)) == [D(2026, 10, 2)]  # first in the future


def test_series_dates_year_rollover() -> None:
    dates = series_dates(D(2025, 11, 10), D(2026, 2, 3))
    assert D(2025, 11, 30) in dates and D(2025, 12, 31) in dates and D(2026, 1, 31) in dates
    assert D(2026, 2, 28) not in dates


def test_cumulative_and_average_curve_round_to_the_cent_and_carry_the_last_value() -> None:
    assert cumulative([0, 5, 0, 7]) == [0, 5, 5, 12]
    assert cumulative([]) == []
    short = [10, 20, 30]  # a month with 3 days, for the sake of the example
    long = [1, 2, 4, 8]
    assert average_curve([short, long], 4) == [6, 11, 17, 19]  # (30+8)/2 = 19: short keeps 30
    assert average_curve([[1], [2]], 2) == [2, 2]  # 1.5 rounds half up
    assert average_curve([], 3) == []


def test_first_crossing_is_strictly_above_the_ceiling() -> None:
    curve = [100, 200, 300, 300]
    assert first_crossing(curve, 300) is None  # touching is not crossing
    assert first_crossing(curve, 299) == 3
    assert first_crossing(curve, 99) == 1
    assert first_crossing([], 0) is None


def test_top_with_rest_folds_the_long_tail_but_never_a_single_row() -> None:
    rows = [RankedRow(k, v, 1) for k, v in [("a", 50), ("b", 40), ("c", 30), ("d", 20), ("e", 10)]]
    assert top_with_rest(rows, 5).rest == []
    six = [*rows, RankedRow("f", 5, 2)]
    kept = top_with_rest(six, 5)
    assert [r.key for r in kept.top] == ["a", "b", "c", "d", "e", "f"] and kept.rest == []
    seven = [*six, RankedRow("g", 4, 3)]
    folded = top_with_rest(seven, 5)
    assert [r.key for r in folded.top] == ["a", "b", "c", "d", "e"]
    assert [r.key for r in folded.rest] == ["f", "g"]
    assert folded.rest_total_cents == 9 and folded.rest_count == 5
    assert top_with_rest([], 5).top == []
