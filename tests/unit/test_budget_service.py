"""Phase 4 acceptance tests: budget matrix, recurring alerts, daily flow (CLAUDE.md 9.7-9.8)."""

import datetime as dt

import pytest

from financas.domain.money import YearMonth
from financas.domain.services.budget import build_budget, last_closed_months, months_of_year_until
from financas.domain.services.daily_flow import daily_flow
from financas.domain.services.recurring import AlertKind, recurring_alerts

D = dt.date
YM = YearMonth


# --- months ---


def test_last_closed_months_excludes_the_current_month() -> None:
    assert last_closed_months(D(2026, 10, 1), 3) == [YM(2026, 7), YM(2026, 8), YM(2026, 9)]
    assert last_closed_months(D(2026, 1, 15), 3) == [YM(2025, 10), YM(2025, 11), YM(2025, 12)]


def test_months_of_year_until_the_last_closed_month() -> None:
    assert months_of_year_until(2026, D(2026, 10, 1)) == [YM(2026, m) for m in range(1, 10)]
    assert months_of_year_until(2025, D(2026, 10, 1)) == [YM(2025, m) for m in range(1, 13)]
    assert months_of_year_until(2026, D(2026, 1, 20)) == []  # nothing closed yet this year


# --- the matrix ---


MONTHS = [YM(2026, 7), YM(2026, 8), YM(2026, 9)]


def test_rows_with_goal_have_average_difference_and_over_months() -> None:
    spending = {
        "casa": {YM(2026, 7): 165_000, YM(2026, 8): 165_000, YM(2026, 9): 165_000},
        "food": {YM(2026, 7): 91_000, YM(2026, 8): 78_820, YM(2026, 9): 84_230},
    }
    budget = build_budget(MONTHS, spending, {"casa": 170_000, "food": 70_000})
    by_id = {r.category_id: r for r in budget.with_goal}
    casa, food = by_id["casa"], by_id["food"]
    assert (casa.average_cents, casa.diff_cents, casa.over_months) == (
        165_000,
        -5_000,
        (False,) * 3,
    )
    assert food.average_cents == 84_683  # 254.050 / 3 rounded to the cent
    assert food.diff_cents == 14_683 and food.over_months == (True, True, True)
    assert [r.category_id for r in budget.with_goal] == ["food", "casa"]  # most over the goal first


def test_only_a_month_above_the_goal_is_flagged() -> None:
    budget = build_budget(
        MONTHS, {"x": {YM(2026, 7): 100, YM(2026, 8): 130, YM(2026, 9): 90}}, {"x": 100}
    )
    (row,) = budget.with_goal
    assert row.over_months == (False, True, False)  # equal to the goal is not over
    assert row.average_cents == 107 and row.diff_cents == 7


def test_missing_months_count_as_zero_spending() -> None:
    (row,) = build_budget(MONTHS, {"x": {YM(2026, 9): 300}}, {"x": 100}).with_goal
    assert row.months == (0, 0, 300) and row.average_cents == 100 and row.diff_cents == 0


def test_summary_kpis_cover_only_the_categories_with_a_goal() -> None:
    spending = {
        "a": {YM(2026, 7): 1_000, YM(2026, 8): 1_000, YM(2026, 9): 1_000},
        "b": {YM(2026, 7): 3_000, YM(2026, 8): 3_000, YM(2026, 9): 3_000},
        "nogoal": {YM(2026, 7): 500, YM(2026, 8): 700, YM(2026, 9): 900},
    }
    budget = build_budget(MONTHS, spending, {"a": 1_500, "b": 2_000, "nogoal": None})
    assert budget.goal_total_cents == 3_500
    assert budget.average_with_goal_cents == 4_000
    assert budget.diff_total_cents == 500
    assert budget.diff_percent == pytest.approx(500 / 3_500)
    assert budget.over_count == 1 and [r.category_id for r in budget.over] == ["b"]
    assert budget.with_goal_count == 2
    assert [r.category_id for r in budget.without_goal] == ["nogoal"]
    assert budget.month_totals_all == (4_500, 4_700, 4_900)
    assert budget.month_totals_with_goal == (4_000, 4_000, 4_000)
    assert budget.month_totals_without_goal == (500, 700, 900)
    assert budget.average_all_cents == 4_700


def test_no_goals_means_no_percent_never_zero_or_division_by_zero() -> None:
    budget = build_budget(MONTHS, {"x": {YM(2026, 7): 100}}, {})
    assert budget.goal_total_cents == 0 and budget.with_goal == []
    assert budget.diff_percent is None and budget.over_count == 0
    assert [r.category_id for r in budget.without_goal] == ["x"]


def test_goal_without_spending_still_has_a_row() -> None:
    budget = build_budget(MONTHS, {}, {"empty": 5_000})
    (row,) = budget.with_goal
    assert row.months == (0, 0, 0) and row.average_cents == 0 and row.diff_cents == -5_000


def test_no_months_gives_an_empty_budget() -> None:
    budget = build_budget([], {"x": {YM(2026, 7): 100}}, {"x": 50})
    assert budget.months == [] and budget.with_goal[0].average_cents == 0


# --- recurring alerts ---


def amounts(**series: dict[YearMonth, int]) -> dict[str, dict[YearMonth, int]]:
    return dict(series)


LAST = YM(2026, 9)
PREV = YM(2026, 8)


def test_changed_amount() -> None:
    alerts = recurring_alerts(amounts(internet={PREV: 11_990, LAST: 12_990}), LAST)
    assert [(a.key, a.kind, a.previous_cents, a.current_cents) for a in alerts] == [
        ("internet", AlertKind.CHANGED, 11_990, 12_990)
    ]


def test_disappeared_charge() -> None:
    alerts = recurring_alerts(amounts(gym={YM(2026, 7): 9_000, PREV: 9_000}), LAST)
    assert [(a.key, a.kind, a.previous_cents, a.current_cents) for a in alerts] == [
        ("gym", AlertKind.DISAPPEARED, 9_000, None)
    ]


def test_appeared_charge_only_when_never_seen_before() -> None:
    baseline = {PREV: 165_000, LAST: 165_000}  # the user already has history
    new = recurring_alerts(amounts(rent=baseline, streaming={LAST: 3_990}), LAST)
    assert [(a.kind, a.previous_cents, a.current_cents) for a in new] == [
        (AlertKind.APPEARED, None, 3_990)
    ]
    back = recurring_alerts(
        amounts(rent=baseline, streaming={YM(2026, 6): 3_990, LAST: 3_990}), LAST
    )
    assert back == []  # skipped a month and came back: not new


def test_the_first_month_of_use_raises_no_appeared_alerts() -> None:
    first_month = amounts(rent={LAST: 165_000}, internet={LAST: 11_990})
    assert recurring_alerts(first_month, LAST) == []  # nothing before it to compare with
    second_month = amounts(rent={PREV: 165_000, LAST: 165_000}, streaming={LAST: 3_990})
    assert [(a.key, a.kind) for a in recurring_alerts(second_month, LAST)] == [
        ("streaming", AlertKind.APPEARED)
    ]


def test_stable_charges_raise_nothing() -> None:
    stable = amounts(
        rent={PREV: 165_000, LAST: 165_000}, phone={YM(2026, 7): 6_990, PREV: 6_990, LAST: 6_990}
    )
    assert recurring_alerts(stable, LAST) == []


def test_charges_gone_before_the_previous_month_are_not_reported_again() -> None:
    assert recurring_alerts(amounts(old={YM(2026, 5): 100}), LAST) == []


def test_alerts_are_sorted_by_kind_then_key() -> None:
    series = amounts(
        zeta={PREV: 1, LAST: 2},
        alpha={PREV: 5},
        beta={LAST: 7},
        gamma={PREV: 3, LAST: 4},
    )
    assert [(a.kind, a.key) for a in recurring_alerts(series, LAST)] == [
        (AlertKind.DISAPPEARED, "alpha"),
        (AlertKind.CHANGED, "gamma"),
        (AlertKind.CHANGED, "zeta"),
        (AlertKind.APPEARED, "beta"),
    ]


# --- daily flow (9.7) ---


def test_daily_flow_with_running_balance() -> None:
    rows = daily_flow(
        [(D(2026, 7, 5), 500_000), (D(2026, 7, 5), -12_000), (D(2026, 7, 9), -30_000)],
        opening_balance_cents=100_000,
    )
    assert [
        (r.day, r.inflow_cents, r.outflow_cents, r.result_cents, r.balance_cents) for r in rows
    ] == [
        (D(2026, 7, 5), 500_000, 12_000, 488_000, 588_000),
        (D(2026, 7, 9), 0, 30_000, -30_000, 558_000),
    ]


def test_daily_flow_without_an_opening_balance_has_no_running_balance() -> None:
    rows = daily_flow([(D(2026, 7, 5), 100)], opening_balance_cents=None)
    assert rows[0].balance_cents is None and rows[0].result_cents == 100


def test_daily_flow_is_ordered_and_empty_without_movements() -> None:
    rows = daily_flow([(D(2026, 7, 9), -1), (D(2026, 7, 2), 1)], opening_balance_cents=0)
    assert [r.day for r in rows] == [D(2026, 7, 2), D(2026, 7, 9)]
    assert daily_flow([], opening_balance_cents=5) == []
