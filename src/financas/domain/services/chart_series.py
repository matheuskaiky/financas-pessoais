"""Pure helpers behind the dashboard charts (CLAUDE.md 10, 11). Money is ``int`` cents.

Definitions (each one has a test):
- ``series_dates``: the dates a net-worth series is sampled at: the end of every month from the
  first date with data, one point a week over the last ``weekly_window`` days, the first date and
  today. Sorted, without repeats, never after today.
- ``cumulative``: running total of the amounts of each day of a month.
- ``average_curve``: the day-by-day mean of several cumulative curves, rounded to the cent; a curve
  shorter than the month (a month with fewer days) keeps its last value (its month total).
- ``first_crossing``: the first day (1-based) on which a cumulative curve is strictly above a
  ceiling; ``None`` if it never is.
- ``top_with_rest``: the biggest ``top_n`` rows and one "rest" row for the long tail.
"""

import calendar
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass


def series_dates(
    first: dt.date, today: dt.date, weekly_window: int = 92, weekly_step: int = 7
) -> list[dt.date]:
    """Sample dates for a net-worth series (see the module docstring)."""
    if first > today:
        return [today]
    found: set[dt.date] = {first, today}
    year, month = first.year, first.month
    while (year, month) <= (today.year, today.month):
        end = dt.date(year, month, calendar.monthrange(year, month)[1])
        if first <= end <= today:
            found.add(end)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    back = today
    limit = today - dt.timedelta(days=weekly_window)
    while back >= limit:
        if back >= first:
            found.add(back)
        back -= dt.timedelta(days=weekly_step)
    return sorted(found)


def cumulative(daily_cents: Sequence[int]) -> list[int]:
    """Running total: ``[0, 5, 0, 7]`` becomes ``[0, 5, 5, 12]``."""
    total = 0
    out: list[int] = []
    for amount in daily_cents:
        total += amount
        out.append(total)
    return out


def average_curve(curves: Sequence[Sequence[int]], days: int) -> list[int]:
    """Mean of the cumulative curves per day, ``days`` long; empty without curves."""
    if not curves:
        return []
    count = len(curves)
    out: list[int] = []
    for index in range(days):
        total = 0
        for curve in curves:
            if not curve:
                continue
            total += curve[min(index, len(curve) - 1)]
        out.append((total * 2 + count) // (2 * count))  # nearest cent, halves up
    return out


def first_crossing(curve: Sequence[int], ceiling_cents: int) -> int | None:
    """First day (1-based) with the curve strictly above the ceiling."""
    for index, value in enumerate(curve):
        if value > ceiling_cents:
            return index + 1
    return None


@dataclass(frozen=True)
class RankedRow:
    key: str
    total_cents: int
    count: int


@dataclass(frozen=True)
class TopWithRest:
    top: list[RankedRow]
    rest: list[RankedRow]  # the folded rows, biggest first (the long tail)

    @property
    def rest_total_cents(self) -> int:
        return sum(r.total_cents for r in self.rest)

    @property
    def rest_count(self) -> int:
        return sum(r.count for r in self.rest)


def top_with_rest(rows: Sequence[RankedRow], top_n: int = 5) -> TopWithRest:
    """The ``top_n`` biggest rows; the rest is folded only when it has two rows or more.

    A single leftover row stays in the top (a "rest" of one would hide a name for nothing).
    """
    ordered = sorted(rows, key=lambda r: (-r.total_cents, r.key))
    if len(ordered) <= top_n + 1:
        return TopWithRest(ordered, [])
    return TopWithRest(ordered[:top_n], ordered[top_n:])
