"""Investment valuations, yield and allocation (CLAUDE.md 9.6). Pure functions.

Everything here uses the **net** value as the institution reports it. Taxes are never computed.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from financas.domain.models import AssetClass
from financas.domain.services.balances import AnchorPoint


def current_value(
    valuations: Iterable[AnchorPoint],
    flows: Iterable[tuple[dt.date, int]],
    today: dt.date,
) -> int | None:
    """Last valuation up to ``today`` + net flows after it; ``None`` without a valuation.

    A valuation already contains the movements dated on or before it (9.6).
    """
    known = [v for v in valuations if v.on_date <= today]
    if not known:
        return None
    last = max(known, key=lambda v: v.on_date)
    return last.balance_cents + sum(c for d, c in flows if last.on_date < d <= today)


@dataclass(frozen=True)
class PeriodYield:
    start_value_cents: int
    end_value_cents: int
    net_contributions_cents: int  # contributions - withdrawals
    yield_cents: int
    simple_return: float | None  # yield / (start value + net contributions); not time-weighted
    baseline_date: dt.date
    end_date: dt.date


def period_yield(
    valuations: Sequence[AnchorPoint],
    flows: Iterable[tuple[dt.date, int]],
    start: dt.date,
    end: dt.date,
) -> PeriodYield | None:
    """Yield = end value - start value - net contributions, measured from the first valuation.

    The baseline is the last valuation on or before ``start``; when there is none, the first
    valuation inside the period. ``None`` when there are not two valuations to compare.
    """
    ordered = sorted(valuations, key=lambda v: v.on_date)
    before = [v for v in ordered if v.on_date <= start]
    baseline = (
        before[-1] if before else next((v for v in ordered if start < v.on_date <= end), None)
    )
    ends = [v for v in ordered if v.on_date <= end]
    if baseline is None or not ends or ends[-1].on_date <= baseline.on_date:
        return None
    final = ends[-1]
    net = sum(c for d, c in flows if baseline.on_date < d <= final.on_date)
    gained = final.balance_cents - baseline.balance_cents - net
    base = baseline.balance_cents + net
    return PeriodYield(
        baseline.balance_cents,
        final.balance_cents,
        net,
        gained,
        gained / base if base > 0 else None,
        baseline.on_date,
        final.on_date,
    )


def valuation_age_days(last_valuation: dt.date, today: dt.date) -> int:
    return (today - last_valuation).days


def is_stale(age_days: int, stale_after_days: int) -> bool:
    """A valuation older than ``FINANCAS_VALUATION_STALE_DAYS`` is flagged 'desatualizada'."""
    return age_days > stale_after_days


@dataclass(frozen=True)
class AllocationRow:
    asset_class: AssetClass
    value_cents: int
    share: float  # of the total with a value


@dataclass(frozen=True)
class Allocation:
    rows: list[AllocationRow]  # biggest first
    total_cents: int
    pending_count: int  # items without a valuation: left out and listed as pending


def allocate(items: Iterable[tuple[AssetClass, int | None]]) -> Allocation:
    totals: dict[AssetClass, int] = {}
    pending = 0
    for asset_class, value in items:
        if value is None:
            pending += 1
        else:
            totals[asset_class] = totals.get(asset_class, 0) + value
    total = sum(totals.values())
    rows = [
        AllocationRow(asset_class, value, value / total if total else 0.0)
        for asset_class, value in sorted(totals.items(), key=lambda i: (-i[1], i[0].value))
    ]
    return Allocation(rows, total, pending)
