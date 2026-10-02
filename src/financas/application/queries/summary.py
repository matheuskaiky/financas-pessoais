"""Month and year totals (section 10). Always computed, never stored.

Definitions (each one has a test):
- ``income``: sum of ``income`` entries. ``expenses``: sum of ``expense`` entries (positive).
- ``refunds``: sum of ``refund`` entries, shown apart; ``net_expenses = expenses - refunds``.
  (Open decision 3: refunds kept apart, as in the spreadsheet.)
- ``balance = income - net_expenses``; ``savings_rate = balance / income`` (``None`` if no income).
- Transfers are neither income nor expense and never appear here.
- ``recurring`` = expenses flagged ``is_recurring``; ``variable`` = the other expenses.
- Category and group rows cover gross expenses; ``share`` is the part of total expenses.
- A card entry counts in its **statement month** (open decision 1); everything else counts in the
  month of ``posted_on``. Card payments are transfers and never appear here.
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
    statement_months: Mapping[str, YearMonth] | None = None,
) -> Summary:
    income = expenses = refunds = recurring = 0
    per_category: dict[str, list[int]] = {}
    count = 0
    months = statement_months or {}
    for t in transactions:
        if t.statement_id is not None and t.statement_id in months:
            counted_on = months[t.statement_id].day(1)  # competence: the statement month
        else:
            counted_on = t.posted_on
        if not period.start <= counted_on <= period.end:
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
            transactions = uow.transactions.list_for_competence(period.start, period.end)
            months = {s.id: s.month for s in uow.statements.list_all()}
        return summarize(transactions, categories, period, months)

    def months_of_year(self, year: int) -> list[Summary]:
        """Twelve monthly summaries (for the year view and charts)."""
        with self._uow as uow:
            categories = {c.id: c for c in uow.categories.list_all()}
            year_period = Period.year(year)
            transactions = uow.transactions.list_for_competence(year_period.start, year_period.end)
            months = {s.id: s.month for s in uow.statements.list_all()}
        return [
            summarize(transactions, categories, Period.month(YearMonth(year, m)), months)
            for m in range(1, 13)
        ]
