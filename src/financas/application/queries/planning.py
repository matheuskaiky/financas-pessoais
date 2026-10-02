"""Budget, recurring charges and daily flow (CLAUDE.md 9.7, 9.8, 11.8).

Definitions (each one has a test):
- Budget: gross spending per expense category and closed month (card expenses in their statement
  month); average = total / number of months, zeros included; ``diff = average - goal``.
- Recurring item: expense entries flagged ``is_recurring``, grouped by the normalized description;
  the amount of a month is the sum of its entries. Alerts compare the last closed month with the
  one before it (disappeared, changed amount, appeared).
- Daily flow: movements per day of one account, transfers included, with the running balance
  from the nearest informed balance (``None`` when the account has none).
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from financas.application.queries.summary import Period, summarize
from financas.domain.errors import DomainError
from financas.domain.models import Account, AccountKind, CategoryKind, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.services.balances import AnchorPoint, balance_on
from financas.domain.services.budget import (
    Budget,
    build_budget,
    last_closed_months,
    months_of_year_until,
)
from financas.domain.services.daily_flow import DayFlow, daily_flow
from financas.domain.services.recurring import RecurringAlert, recurring_alerts


class BudgetRange(StrEnum):
    LAST_3_MONTHS = "last_3_months"
    YEAR = "year"


@dataclass(frozen=True)
class BudgetView:
    budget: Budget
    range: BudgetRange
    year: int
    categories: dict[str, object]  # category id -> Category, for names and colors


class GetBudget:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(
        self, budget_range: BudgetRange = BudgetRange.LAST_3_MONTHS, year: int | None = None
    ) -> BudgetView:
        today = self._clock.today()
        chosen_year = year or today.year
        months = (
            last_closed_months(today, 3)
            if budget_range is BudgetRange.LAST_3_MONTHS
            else months_of_year_until(chosen_year, today)
        )
        with self._uow as uow:
            categories = {c.id: c for c in uow.categories.list_all()}
            statement_months = {s.id: s.month for s in uow.statements.list_all()}
            spending: dict[str, dict[YearMonth, int]] = {}
            if months:
                transactions = uow.transactions.list_for_competence(
                    months[0].day(1), months[-1].last_day()
                )
                for month in months:
                    summary = summarize(
                        transactions, categories, Period.month(month), statement_months
                    )
                    for row in summary.by_category:
                        spending.setdefault(row.category_id, {})[month] = row.total_cents
        goals = {
            c.id: c.monthly_budget_cents
            for c in categories.values()
            if c.kind is CategoryKind.EXPENSE
        }
        return BudgetView(
            build_budget(months, spending, goals), budget_range, chosen_year, categories
        )


@dataclass(frozen=True)
class RecurringItem:
    key: str  # normalized description
    label: str  # the latest description, as typed
    category_id: str
    amounts: dict[YearMonth, int]  # per month, positive cents


@dataclass(frozen=True)
class RecurringView:
    months: list[YearMonth]  # the last closed months, then the current one
    current_month: YearMonth
    items: list[RecurringItem]  # biggest recent amount first
    alerts: list[RecurringAlert]
    labels: dict[str, str]  # key -> label, for the alerts
    monthly_total_cents: dict[YearMonth, int]


class GetRecurring:
    WINDOW = 6  # closed months in the matrix

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self) -> RecurringView:
        today = self._clock.today()
        closed = last_closed_months(today, self.WINDOW)
        current = YearMonth.from_date(today)
        months = [*closed, current]
        with self._uow as uow:
            statement_months = {s.id: s.month for s in uow.statements.list_all()}
            transactions = uow.transactions.list_for_competence(
                months[0].day(1), current.last_day()
            )
        amounts: dict[str, dict[YearMonth, int]] = {}
        latest: dict[str, tuple[dt.date, str, str]] = {}
        for t in sorted(transactions, key=lambda t: t.posted_on):
            if t.kind is not TransactionKind.EXPENSE or not t.is_recurring:
                continue
            month = (
                statement_months[t.statement_id]
                if t.statement_id in statement_months
                else YearMonth.from_date(t.posted_on)
            )
            key = t.description_search
            amounts.setdefault(key, {})
            amounts[key][month] = amounts[key].get(month, 0) - t.amount_cents
            latest[key] = (t.posted_on, t.description, t.category_id)
        closed_only = {k: {m: v for m, v in s.items() if m < current} for k, s in amounts.items()}
        alerts = recurring_alerts(closed_only, closed[-1]) if closed else []
        items = [
            RecurringItem(key, latest[key][1], latest[key][2], series)
            for key, series in amounts.items()
        ]
        items.sort(key=lambda i: (-(i.amounts.get(current) or i.amounts.get(closed[-1], 0)), i.key))
        totals = {m: sum(i.amounts.get(m, 0) for i in items) for m in months}
        return RecurringView(
            months, current, items, alerts, {i.key: i.label for i in items}, totals
        )


@dataclass(frozen=True)
class DailyFlowView:
    account: Account
    period: Period
    opening_balance_cents: int | None
    rows: list[DayFlow]
    closing_balance_cents: int | None
    inflow_cents: int
    outflow_cents: int


class GetDailyFlow:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, account_id: str, period: Period) -> DailyFlowView:
        with self._uow as uow:
            account = uow.accounts.get(account_id)
            if account is None:
                raise DomainError("NOT_FOUND", entity="account")
            if account.kind is AccountKind.CREDIT_CARD:
                raise DomainError("CARD_HAS_NO_DAILY_FLOW")
            anchors = [
                AnchorPoint(a.on_date, a.balance_cents)
                for a in uow.anchors.list_for_account(account.id)
            ]
            movements = uow.transactions.movements(account.id)
        opening = balance_on(anchors, movements, period.start - dt.timedelta(days=1))
        inside = [(d, c) for d, c in movements if period.start <= d <= period.end]
        rows = daily_flow(inside, opening)
        closing = rows[-1].balance_cents if rows else opening
        return DailyFlowView(
            account,
            period,
            opening,
            rows,
            closing,
            sum(r.inflow_cents for r in rows),
            sum(r.outflow_cents for r in rows),
        )
