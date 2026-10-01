"""Month and year totals (section 10). Always computed, never stored.

Definitions (each one has a test):
- ``income``: sum of ``income`` entries. ``expenses``: sum of ``expense`` entries (positive).
- ``refunds``: sum of ``refund`` entries, shown apart; ``net_expenses = expenses - refunds``.
  (Open decision 3: refunds kept apart, as in the spreadsheet.)
- ``balance = income - net_expenses``; ``savings_rate = balance / income`` (``None`` if no income).
- Transfers are neither income nor expense and never appear here.
- ``recurring`` = expenses flagged ``is_recurring``; ``variable`` = the other expenses.
- Category and group rows cover gross expenses; ``share`` is the part of total expenses.
- A card expense will count in its statement month (Phase 2); today everything counts in
  the month of ``posted_on``.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from financas.domain.models import Category, CategoryGroup, Transaction, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.ports import UnitOfWork


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date  # inclusive

    @classmethod
    def month(cls, month: YearMonth) -> "Period":
        return cls(month.day(1), month.last_day())

    @classmethod
    def year(cls, year: int) -> "Period":
        return cls(dt.date(year, 1, 1), dt.date(year, 12, 31))


@dataclass(frozen=True)
class CategoryTotal:
    category_id: str
    group: CategoryGroup
    total_cents: int
    count: int
    share: float | None


@dataclass(frozen=True)
class GroupTotal:
    group: CategoryGroup
    total_cents: int
    count: int
    share: float | None


@dataclass(frozen=True)
class Summary:
    period: Period
    income_cents: int
    expenses_cents: int
    refunds_cents: int
    net_expenses_cents: int
    balance_cents: int
    savings_rate: float | None
    recurring_expenses_cents: int
    variable_expenses_cents: int
    by_category: list[CategoryTotal]
    by_group: list[GroupTotal]
    entry_count: int


def summarize(
    transactions: Iterable[Transaction],
    categories: Mapping[str, Category],
    period: Period,
) -> Summary:
    income = expenses = refunds = recurring = 0
    per_category: dict[str, list[int]] = {}
    count = 0
    for t in transactions:
        if not period.start <= t.posted_on <= period.end:
            continue
        match t.kind:
            case TransactionKind.TRANSFER:
                continue
            case TransactionKind.INCOME:
                income += t.amount_cents
            case TransactionKind.REFUND:
                refunds += t.amount_cents
            case TransactionKind.EXPENSE:
                spent = -t.amount_cents
                expenses += spent
                if t.is_recurring:
                    recurring += spent
                row = per_category.setdefault(t.category_id, [0, 0])
                row[0] += spent
                row[1] += 1
        count += 1

    def share(total: int) -> float | None:
        return total / expenses if expenses else None

    by_category = sorted(
        (
            CategoryTotal(cid, categories[cid].group, total, n, share(total))
            for cid, (total, n) in per_category.items()
        ),
        key=lambda r: (-r.total_cents, categories[r.category_id].slug),
    )
    group_rows: dict[CategoryGroup, list[int]] = {}
    for row in by_category:
        acc = group_rows.setdefault(row.group, [0, 0])
        acc[0] += row.total_cents
        acc[1] += row.count
    by_group = sorted(
        (GroupTotal(g, total, n, share(total)) for g, (total, n) in group_rows.items()),
        key=lambda r: (-r.total_cents, r.group.value),
    )
    net_expenses = expenses - refunds
    balance = income - net_expenses
    return Summary(
        period=period,
        income_cents=income,
        expenses_cents=expenses,
        refunds_cents=refunds,
        net_expenses_cents=net_expenses,
        balance_cents=balance,
        savings_rate=balance / income if income else None,
        recurring_expenses_cents=recurring,
        variable_expenses_cents=expenses - recurring,
        by_category=by_category,
        by_group=by_group,
        entry_count=count,
    )


class GetSummary:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, period: Period) -> Summary:
        with self._uow as uow:
            categories = {c.id: c for c in uow.categories.list_all()}
            transactions = uow.transactions.list_between(period.start, period.end)
        return summarize(transactions, categories, period)

    def months_of_year(self, year: int) -> list[Summary]:
        """Twelve monthly summaries (for the year view and charts)."""
        with self._uow as uow:
            categories = {c.id: c for c in uow.categories.list_all()}
            year_period = Period.year(year)
            transactions = uow.transactions.list_between(year_period.start, year_period.end)
        return [
            summarize(transactions, categories, Period.month(YearMonth(year, m)))
            for m in range(1, 13)
        ]
