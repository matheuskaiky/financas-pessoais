import datetime as dt

import pytest

from fakes import MemoryUnitOfWork
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.models import Account, CategoryGroup, TransactionKind
from financas.domain.money import YearMonth

K = TransactionKind
D = dt.date
JULY = Period.month(YearMonth(2026, 7))
TEN = D(2026, 7, 10)


def add(
    uow: MemoryUnitOfWork,
    account: Account,
    kind: TransactionKind,
    cents: int,
    day: dt.date = TEN,
    slug: str | None = None,
    recurring: bool = False,
) -> None:
    category = uow.categories.get_by_slug(slug) if slug else None
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            account.id,
            day,
            kind,
            cents,
            "item",
            category_id=category.id if category else None,
            is_recurring=recurring,
        )
    )


def test_empty_month(uow: MemoryUnitOfWork) -> None:
    s = GetSummary(uow).execute(JULY)
    assert (s.income_cents, s.expenses_cents, s.balance_cents) == (0, 0, 0)
    assert s.savings_rate is None
    assert s.by_category == [] and s.by_group == []


def test_income_expenses_balance_and_savings_rate(uow: MemoryUnitOfWork, checking: Account) -> None:
    add(uow, checking, K.INCOME, 500_000, slug="salary")
    add(uow, checking, K.EXPENSE, 100_000, slug="home")
    add(uow, checking, K.EXPENSE, 50_000, slug="food")
    s = GetSummary(uow).execute(JULY)
    assert s.income_cents == 500_000
    assert s.expenses_cents == 150_000
    assert s.balance_cents == 350_000
    assert s.savings_rate == 0.7


def test_refunds_are_apart_and_reduce_net_spending(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    add(uow, checking, K.INCOME, 200_000, slug="salary")
    add(uow, checking, K.EXPENSE, 80_000, slug="shopping")
    add(uow, checking, K.REFUND, 30_000)
    s = GetSummary(uow).execute(JULY)
    assert (s.expenses_cents, s.refunds_cents, s.net_expenses_cents) == (80_000, 30_000, 50_000)
    assert s.balance_cents == 150_000
    assert s.by_category[0].total_cents == 80_000  # categories show gross spending


def test_transfers_are_neither_income_nor_expense(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    add(uow, checking, K.INCOME, 100_000, slug="salary")
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 11), 40_000)
    )
    RegisterTransfer(uow).execute(RegisterTransferCommand(None, checking.id, D(2026, 7, 12), 9_000))
    s = GetSummary(uow).execute(JULY)
    assert (s.income_cents, s.expenses_cents, s.balance_cents) == (100_000, 0, 100_000)
    assert s.entry_count == 1


def test_recurring_versus_variable(uow: MemoryUnitOfWork, checking: Account) -> None:
    add(uow, checking, K.EXPENSE, 10_000, slug="subscriptions", recurring=True)
    add(uow, checking, K.EXPENSE, 25_000, slug="food")
    s = GetSummary(uow).execute(JULY)
    assert (s.recurring_expenses_cents, s.variable_expenses_cents) == (10_000, 25_000)


def test_by_category_and_group_sorted_with_shares(uow: MemoryUnitOfWork, checking: Account) -> None:
    add(uow, checking, K.EXPENSE, 10_000, slug="food")
    add(uow, checking, K.EXPENSE, 30_000, slug="groceries")
    add(uow, checking, K.EXPENSE, 20_000, slug="home")
    add(uow, checking, K.EXPENSE, 10_000, slug="food")
    s = GetSummary(uow).execute(JULY)
    slugs = [uow.categories.get(r.category_id).slug for r in s.by_category]  # type: ignore[union-attr]
    assert slugs == ["groceries", "food", "home"]  # tie: by slug
    assert [r.total_cents for r in s.by_category] == [30_000, 20_000, 20_000]
    assert [r.count for r in s.by_category] == [1, 2, 1]
    assert s.by_category[0].share == pytest.approx(30_000 / 70_000)
    groups = {g.group: g for g in s.by_group}
    assert groups[CategoryGroup.ESSENTIAL].total_cents == 50_000
    assert groups[CategoryGroup.NON_ESSENTIAL].total_cents == 20_000
    assert s.by_group[0].group is CategoryGroup.ESSENTIAL


def test_period_boundaries(uow: MemoryUnitOfWork, checking: Account) -> None:
    add(uow, checking, K.EXPENSE, 1, day=D(2026, 6, 30))
    add(uow, checking, K.EXPENSE, 2, day=D(2026, 7, 1))
    add(uow, checking, K.EXPENSE, 4, day=D(2026, 7, 31))
    add(uow, checking, K.EXPENSE, 8, day=D(2026, 8, 1))
    assert GetSummary(uow).execute(JULY).expenses_cents == 6


def test_whole_year_and_monthly_series(uow: MemoryUnitOfWork, checking: Account) -> None:
    add(uow, checking, K.EXPENSE, 100, day=D(2026, 1, 15))
    add(uow, checking, K.EXPENSE, 200, day=D(2026, 12, 31))
    add(uow, checking, K.EXPENSE, 400, day=D(2025, 12, 31))
    year = GetSummary(uow).execute(Period.year(2026))
    assert year.expenses_cents == 300
    months = GetSummary(uow).months_of_year(2026)
    assert len(months) == 12
    assert months[0].expenses_cents == 100 and months[11].expenses_cents == 200
    assert sum(m.expenses_cents for m in months) == 300


def test_an_itemized_expense_counts_by_item_and_its_parent_never_as_a_category(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    from fakes import FixedClock
    from financas.application.use_cases.transactions import (
        SplitItem,
        UpdateTransaction,
        UpdateTransactionCommand,
    )

    add(uow, checking, K.EXPENSE, 20_000, slug="groceries")  # the one we itemize
    add(uow, checking, K.EXPENSE, 3_000, slug="food")
    parent = next(t for t in uow.transactions.items.values() if t.amount_cents == -20_000)
    groceries, health, home = (
        uow.categories.get_by_slug(s) for s in ("groceries", "health", "home")
    )
    assert groceries and health and home
    UpdateTransaction(uow, FixedClock(TEN)).execute(
        UpdateTransactionCommand(
            parent.id,
            parent.posted_on,
            20_000,
            parent.description,
            splits=(
                SplitItem("Feira", groceries.id, 12_000),
                SplitItem("Higiene", health.id, 5_000),
                SplitItem("Limpeza", home.id, 3_000),
            ),
        )
    )
    s = GetSummary(uow).execute(JULY)
    totals = {r.category_id: r.total_cents for r in s.by_category}
    assert (
        totals[groceries.id] == 12_000 and totals[health.id] == 5_000 and totals[home.id] == 3_000
    )
    assert sum(totals.values()) == s.expenses_cents == 23_000  # nothing orphaned, nothing doubled
    assert uow.transactions.get(parent.id).category_id is None  # type: ignore[union-attr]
    assert {g.group for g in s.by_group} >= {CategoryGroup.ESSENTIAL}
