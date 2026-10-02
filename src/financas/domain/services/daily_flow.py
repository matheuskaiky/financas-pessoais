"""Daily flow of an account: inflows, outflows, result and running balance (CLAUDE.md 9.7)."""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DayFlow:
    day: dt.date
    inflow_cents: int
    outflow_cents: int  # positive number
    result_cents: int
    balance_cents: int | None  # end of the day; ``None`` without an opening balance


def daily_flow(
    movements: Iterable[tuple[dt.date, int]], opening_balance_cents: int | None
) -> list[DayFlow]:
    """One row per day that had movements, oldest first. Transfers are included (cash balance)."""
    per_day: dict[dt.date, list[int]] = {}
    for day, cents in movements:
        per_day.setdefault(day, []).append(cents)
    rows: list[DayFlow] = []
    balance = opening_balance_cents
    for day in sorted(per_day):
        inflow = sum(c for c in per_day[day] if c > 0)
        outflow = -sum(c for c in per_day[day] if c < 0)
        result = inflow - outflow
        balance = None if balance is None else balance + result
        rows.append(DayFlow(day, inflow, outflow, result, balance))
    return rows
