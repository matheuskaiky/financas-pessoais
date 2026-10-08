"""Series behind the dashboard charts (CLAUDE.md 10, 11). Everything is computed, never stored.

Definitions (each one has a test):
- **Net-worth series**: net worth (cash + investments - outstanding statements, the definition of
  ``GetNetWorth``) at the sampled dates of ``series_dates``; the last point is today's value
  exactly. It starts at the first informed balance or valuation; accounts and holdings without
  any anchor are left out and listed as ``pending`` (the series is ``partial``).
- **Month pace**: gross expenses accumulated day by day in the chosen month (card entries count
  in their statement month; an entry posted outside the month lands on its first or last day),
  the mean cumulative curve of the previous three months (from the first one that has expenses)
  and the ceiling = sum of the monthly goals of the expense categories (none without goals).
- **Cash flow**: income and net expenses (refunds netted, as the balance on the dashboard) for
  each month of a year, up to the current month.
- **Category breakdown**: expenses of a period by category, the five biggest plus one folded
  "rest" row.
"""

import datetime as dt
from dataclasses import dataclass, field

from financas.application.queries.investments import NetWorthView, net_worth_at
from financas.application.queries.summary import GetSummary, Period, Summary
from financas.domain.models import AccountKind, CategoryGroup, CategoryKind, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.chart_series import (
    RankedRow,
    average_curve,
    cumulative,
    first_crossing,
    series_dates,
    top_with_rest,
)

# --- net worth over time -----------------------------------------------------------------------


@dataclass(frozen=True)
class NetWorthPoint:
    on: dt.date
    cents: int


@dataclass(frozen=True)
class PendingItem:
    id: str
    name: str
    kind: str  # "account" or "holding"


@dataclass(frozen=True)
class NetWorthSeries:
    points: list[NetWorthPoint]  # oldest first; empty without any informed balance or valuation
    partial: bool
    pending: list[PendingItem]
    future_installments_cents: int  # shown apart, as commitments (today)
    first_date: dt.date | None


def pending_items(view: NetWorthView) -> list[PendingItem]:
    return [PendingItem(a.id, a.nickname, "account") for a in view.pending] + [
        PendingItem(h.id, h.name, "holding") for h in view.pending_holdings
    ]


def _first_anchor_date(uow: Work) -> dt.date | None:
    dates: list[dt.date] = []
    for account in uow.accounts.list_all():
        if account.kind is not AccountKind.CREDIT_CARD:
            dates.extend(a.on_date for a in uow.anchors.list_for_account(account.id))
    for holding in uow.holdings.list_all():
        dates.extend(a.on_date for a in uow.anchors.list_for_holding(holding.id))
    return min(dates, default=None)


class GetNetWorthSeries:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self) -> NetWorthSeries:
        today = self._clock.today()
        with self._uow as uow:
            first = _first_anchor_date(uow)
            now = net_worth_at(uow, today)
            points: list[NetWorthPoint] = []
            if first is not None:
                for day in series_dates(first, today):
                    value = (
                        now.net_worth_cents
                        if day == today
                        else net_worth_at(uow, day, historical=True).net_worth_cents
                    )
                    points.append(NetWorthPoint(day, value))
        return NetWorthSeries(
            points, now.is_partial, pending_items(now), now.future_installments_cents, first
        )


# --- month pace --------------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthPace:
    month: YearMonth
    days: int  # days in the month
    elapsed_days: int  # days with data: all for a past month, up to today for the current one
    daily_cents: list[int]  # ``elapsed_days`` long
    daily_counts: list[int]
    cumulative_cents: list[int]
    average_cents: list[int]  # ``days`` long; empty when there is no history to average
    average_months: list[YearMonth]
    ceiling_cents: int | None
    crossed_day: int | None
    total_cents: int
    entry_count: int


@dataclass(frozen=True)
class _Entry:
    posted_on: dt.date
    competence: YearMonth
    cents: int


def _daily(transactions: list[_Entry], month: YearMonth, days: int) -> tuple[list[int], list[int]]:
    amounts = [0] * days
    counts = [0] * days
    for entry in transactions:
        if entry.competence != month:
            continue
        posted = entry.posted_on
        index = (
            posted.day - 1
            if YearMonth.from_date(posted) == month
            else (0 if posted < month.day(1) else days - 1)
        )
        amounts[index] += entry.cents
        counts[index] += 1
    return amounts, counts


class GetMonthPace:
    PREVIOUS_MONTHS = 3

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, month: YearMonth) -> MonthPace:
        today = self._clock.today()
        current = YearMonth.from_date(today)
        days = month.last_day().day
        previous = [month.add_months(-n) for n in range(self.PREVIOUS_MONTHS, 0, -1)]
        with self._uow as uow:
            statement_months = {s.id: s.month for s in uow.statements.list_all()}
            raw = uow.transactions.list_for_competence(previous[0].day(1), month.last_day())
            categories = uow.categories.list_all()
        neutral = {c.id for c in categories if c.is_neutral}  # pass-through money is not spending
        entries = [
            _Entry(
                t.posted_on,
                statement_months.get(t.statement_id or "", YearMonth.from_date(t.posted_on)),
                -t.amount_cents,
            )
            for t in raw
            if t.kind is TransactionKind.EXPENSE and t.category_id not in neutral
        ]
        elapsed = days if month < current else (today.day if month == current else 0)
        daily, counts = _daily(entries, month, days)
        daily, counts = daily[:elapsed], counts[:elapsed]
        running = cumulative(daily)
        first_with_data = next(
            (m for m in previous if any(e.competence == m for e in entries)), None
        )
        history = [m for m in previous if first_with_data is not None and m >= first_with_data]
        curves = [cumulative(_daily(entries, m, m.last_day().day)[0]) for m in history]
        ceiling = (
            sum(
                c.monthly_budget_cents or 0
                for c in categories
                if c.kind is CategoryKind.EXPENSE and (c.monthly_budget_cents or 0) > 0
            )
            or None
        )
        return MonthPace(
            month,
            days,
            elapsed,
            daily,
            counts,
            running,
            average_curve(curves, days),
            history,
            ceiling,
            first_crossing(running, ceiling) if ceiling is not None else None,
            running[-1] if running else 0,
            sum(counts),
        )


# --- cash flow ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthFlow:
    month: YearMonth
    income_cents: int
    expenses_cents: int  # net of refunds, the same figure the balance uses
    refunds_cents: int
    balance_cents: int


@dataclass(frozen=True)
class CashFlow:
    year: int
    months: list[MonthFlow]  # January to the current month (twelve for a past year)


class GetCashFlow:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, year: int) -> CashFlow:
        today = self._clock.today()
        count = 12 if year < today.year else (today.month if year == today.year else 0)
        rows = GetSummary(self._uow).months_of_year(year)[:count]
        return CashFlow(
            year,
            [
                MonthFlow(
                    YearMonth.from_date(s.period.start),
                    s.income_cents,
                    s.net_expenses_cents,
                    s.refunds_cents,
                    s.balance_cents,
                )
                for s in rows
            ],
        )


# --- categories --------------------------------------------------------------------------------


@dataclass(frozen=True)
class CategorySlice:
    category_id: str
    group: CategoryGroup
    total_cents: int
    count: int


@dataclass(frozen=True)
class CategoryBreakdown:
    period: Period
    total_cents: int
    entry_count: int  # of the period's entries (all kinds), as the dashboard header
    top: list[CategorySlice]
    rest: list[CategorySlice] = field(default_factory=list[CategorySlice])

    @property
    def rest_total_cents(self) -> int:
        return sum(s.total_cents for s in self.rest)

    @property
    def rest_count(self) -> int:
        return sum(s.count for s in self.rest)


def breakdown_of(summary: Summary, top_n: int = 5) -> CategoryBreakdown:
    by_id = {r.category_id: r for r in summary.by_category}
    ranked = top_with_rest(
        [RankedRow(r.category_id, r.total_cents, r.count) for r in summary.by_category], top_n
    )

    def slices(rows: list[RankedRow]) -> list[CategorySlice]:
        return [CategorySlice(r.key, by_id[r.key].group, r.total_cents, r.count) for r in rows]

    return CategoryBreakdown(
        summary.period,
        summary.expenses_cents,
        summary.entry_count,
        slices(ranked.top),
        slices(ranked.rest),
    )


class GetCategoryBreakdown:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, period: Period, top_n: int = 5) -> CategoryBreakdown:
        return breakdown_of(GetSummary(self._uow).execute(period), top_n)


__all__ = [
    "CashFlow",
    "CategoryBreakdown",
    "CategorySlice",
    "GetCashFlow",
    "GetCategoryBreakdown",
    "GetMonthPace",
    "GetNetWorthSeries",
    "MonthFlow",
    "MonthPace",
    "NetWorthPoint",
    "NetWorthSeries",
    "PendingItem",
    "breakdown_of",
    "pending_items",
]
