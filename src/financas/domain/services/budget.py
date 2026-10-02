"""Budget matrix: category x month, average, goal and ``average - goal`` (CLAUDE.md 11.8).

Pure functions. Spending is gross expenses per category and month (refunds stay apart, open
decision 3); card expenses count in their statement month. A month without spending is zero.
Only closed months are used, so a month in progress never looks like savings.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from financas.domain.money import YearMonth


def last_closed_months(today: dt.date, count: int) -> list[YearMonth]:
    """The ``count`` months before the current one, oldest first."""
    current = YearMonth.from_date(today)
    return [current.add_months(-n) for n in range(count, 0, -1)]


def months_of_year_until(year: int, today: dt.date) -> list[YearMonth]:
    """January of ``year`` up to the last closed month (all twelve for a past year)."""
    current = YearMonth.from_date(today)
    return [m for m in (YearMonth(year, n) for n in range(1, 13)) if m < current]


@dataclass(frozen=True)
class BudgetRow:
    category_id: str
    months: tuple[int, ...]  # spending per month, in the order of ``Budget.months``
    average_cents: int
    goal_cents: int | None
    diff_cents: int | None  # average - goal (positive: above the goal)
    over_months: tuple[bool, ...]  # month above the goal (equal is not over)


@dataclass(frozen=True)
class Budget:
    months: list[YearMonth]
    with_goal: list[BudgetRow]  # most above the goal first
    without_goal: list[BudgetRow]  # biggest spending first
    goal_total_cents: int
    average_with_goal_cents: int  # of the same categories that have a goal
    diff_total_cents: int
    diff_percent: float | None  # diff / goal total; ``None`` without goals
    over: list[BudgetRow]
    month_totals_with_goal: tuple[int, ...]
    month_totals_without_goal: tuple[int, ...]
    month_totals_all: tuple[int, ...]
    average_all_cents: int

    @property
    def over_count(self) -> int:
        return len(self.over)

    @property
    def with_goal_count(self) -> int:
        return len(self.with_goal)


def _average(values: Sequence[int]) -> int:
    n = len(values)
    return (sum(values) * 2 + n) // (2 * n) if n else 0  # rounded to the nearest cent


def build_budget(
    months: Sequence[YearMonth],
    spending: Mapping[str, Mapping[YearMonth, int]],
    goals: Mapping[str, int | None],
) -> Budget:
    ids = set(spending) | {c for c, g in goals.items() if g is not None}
    rows: list[BudgetRow] = []
    for category_id in ids:
        series = tuple(spending.get(category_id, {}).get(m, 0) for m in months)
        goal = goals.get(category_id)
        average = _average(series)
        rows.append(
            BudgetRow(
                category_id,
                series,
                average,
                goal,
                None if goal is None else average - goal,
                tuple(goal is not None and v > goal for v in series),
            )
        )
    with_goal = sorted(
        (r for r in rows if r.goal_cents is not None),
        key=lambda r: (-(r.diff_cents or 0), r.category_id),
    )
    without = sorted(
        (r for r in rows if r.goal_cents is None), key=lambda r: (-sum(r.months), r.category_id)
    )

    def totals(items: Sequence[BudgetRow]) -> tuple[int, ...]:
        return tuple(sum(r.months[i] for r in items) for i in range(len(months)))

    goal_total = sum(r.goal_cents or 0 for r in with_goal)
    average_with_goal = sum(r.average_cents for r in with_goal)
    all_totals = totals(rows)
    return Budget(
        list(months),
        with_goal,
        without,
        goal_total,
        average_with_goal,
        average_with_goal - goal_total,
        (average_with_goal - goal_total) / goal_total if goal_total else None,
        [r for r in with_goal if (r.diff_cents or 0) > 0],
        totals(with_goal),
        totals(without),
        all_totals,
        _average(all_totals),
    )
