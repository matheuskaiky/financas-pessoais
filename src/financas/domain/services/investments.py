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


@dataclass(frozen=True)
class SnapshotPoint:
    """One dated balance snapshot with the profit measured since the beginning (not since the
    first snapshot): what was contributed up to that day against what the balance says."""

    on_date: dt.date
    balance_cents: int  # the snapshot value: gross when informed, else net (the caller decides)
    net_contributions_cents: int  # contributions - withdrawals dated up to and including the day
    profit_cents: int  # balance - net contributions ("lucro nominal")
    return_rate: float | None  # profit / net contributions; ``None`` when nothing net is invested
    period_contributions_cents: int | None  # net flows since the previous snapshot (none: first)
    balance_change_cents: int | None  # balance - previous balance (none at the first)
    period_profit_cents: int | None  # balance change - period contributions (none at the first)
    change_rate: float | None = None  # balance change / previous balance (none at the first)


def profit_timeline(
    snapshots: Iterable[AnchorPoint],
    flows: Iterable[tuple[dt.date, int]],
    *,
    base_cents: int = 0,
    base_date: dt.date | None = None,
) -> list[SnapshotPoint]:
    """The profit at each snapshot, oldest first (CLAUDE.md 9.6).

    ``profit = balance - net contributions`` where net contributions are every flow dated on or
    before the snapshot (a snapshot already contains the movements of its own day), and
    ``period profit = (balance - previous balance) - net flows since the previous snapshot``.
    A withdrawal lowers the net contributions, so the profit it leaves behind stays the same.

    A holding starts from its cost basis: ``base_cents`` (the principal applied on ``base_date``)
    plus the flows dated after ``base_date``; flows on or before it are already inside the base.
    """
    ordered = sorted(snapshots, key=lambda s: s.on_date)
    moves = sorted(f for f in flows if base_date is None or f[0] > base_date)
    points: list[SnapshotPoint] = []
    previous: AnchorPoint | None = None
    for snap in ordered:
        net = base_cents + sum(c for d, c in moves if d <= snap.on_date)
        profit = snap.balance_cents - net
        if previous is None:
            period = change = period_profit = None
        else:
            period = sum(c for d, c in moves if previous.on_date < d <= snap.on_date)
            change = snap.balance_cents - previous.balance_cents
            period_profit = change - period
        points.append(
            SnapshotPoint(
                snap.on_date,
                snap.balance_cents,
                net,
                profit,
                profit / net if net > 0 else None,
                period,
                change,
                period_profit,
                change / previous.balance_cents
                if previous is not None and previous.balance_cents > 0 and change is not None
                else None,
            )
        )
        previous = snap
    return points


@dataclass(frozen=True)
class HoldingCapital:
    """One note as the capital view sees it."""

    committed_cents: int  # cost basis: principal + net flows tagged to it after it was applied
    value_cents: int | None  # market value of an ACTIVE note; ``None`` without a valuation
    active: bool


@dataclass(frozen=True)
class CapitalPosition:
    """Where the money that reached an investment account sits (CLAUDE.md 9.6)."""

    inflows_cents: int  # transfers received (contributions)
    outflows_cents: int  # transfers sent (redemptions)
    net_inflow_cents: int
    allocated_cost_cents: int  # cost basis of the active notes
    free_cash_cents: int  # net inflow not committed to any note
    applied_value_cents: int  # market value of the active notes that have a valuation
    total_cents: int  # free cash + applied value
    profit_cents: int  # total - net inflow
    return_rate: float | None
    partial: bool  # an active note has no valuation yet: its value is missing from the total

    @property
    def unfunded_cents(self) -> int:
        """Notes whose cost no recorded transfer paid for (the free cash would be negative)."""
        return max(0, -self.free_cash_cents)


def capital_position(flows: Iterable[int], holdings: Sequence[HoldingCapital]) -> CapitalPosition:
    """Free cash, applied value and profit of an account tracked by notes.

    ``free cash = net inflow - cost basis of every note`` (redeemed ones included: a note that
    left with a withdrawal carries that withdrawal as a negative cost, so redeeming never moves
    the free cash) and ``profit = total - net inflow``, which therefore counts the unrealized gain
    of the active notes and the realized gain of the redeemed ones. Notes without a valuation
    are left out of the applied value and the result is flagged ``partial``.
    """
    moves = list(flows)
    inflows = sum(c for c in moves if c > 0)
    outflows = -sum(c for c in moves if c < 0)
    net = inflows - outflows
    committed = sum(h.committed_cents for h in holdings)
    free = net - committed
    applied = sum(h.value_cents or 0 for h in holdings if h.active)
    total = free + applied
    profit = total - net
    return CapitalPosition(
        inflows,
        outflows,
        net,
        sum(h.committed_cents for h in holdings if h.active),
        free,
        applied,
        total,
        profit,
        profit / net if net > 0 else None,
        any(h.active and h.value_cents is None for h in holdings),
    )
