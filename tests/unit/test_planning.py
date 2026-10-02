"""Phase 4 application tests: budget goals, recurring alerts and daily flow."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.planning import (
    BudgetRange,
    GetBudget,
    GetDailyFlow,
    GetRecurring,
)
from financas.application.queries.summary import Period
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudget, SetCategoryBudgets
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.investments import (
    FlowDirection,
    RegisterInvestmentFlow,
    RegisterInvestmentFlowCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.services.recurring import AlertKind

D = dt.date
YM = YearMonth
TODAY = D(2026, 10, 15)  # closed months: ... 2026-07, 2026-08, 2026-09


def category(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def spend(
    uow: MemoryUnitOfWork, account: Account, day: dt.date, cents: int, slug: str,
    description: str = "x", recurring: bool = False,
):  # fmt: skip
    return RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            account.id, day, TransactionKind.EXPENSE, cents, description,
            category_id=category(uow, slug), is_recurring=recurring,
        )
    )  # fmt: skip


# --- budget goals ---


def test_set_and_clear_a_goal(uow: MemoryUnitOfWork) -> None:
    food = category(uow, "food")
    assert SetCategoryBudget(uow).execute(food, 70_000).monthly_budget_cents == 70_000
    assert uow.categories.get(food).monthly_budget_cents == 70_000  # type: ignore[union-attr]
    assert SetCategoryBudget(uow).execute(food, None).monthly_budget_cents is None


def test_goal_rules(uow: MemoryUnitOfWork) -> None:
    for slug, cents, code in [
        ("salary", 1_000, "BUDGET_ONLY_FOR_EXPENSES"),
        ("transfer", 1_000, "BUDGET_ONLY_FOR_EXPENSES"),
        ("food", 0, "AMOUNT_NOT_POSITIVE"),
        ("food", -5, "AMOUNT_NOT_POSITIVE"),
    ]:
        with pytest.raises(DomainError) as exc:
            SetCategoryBudget(uow).execute(category(uow, slug), cents)
        assert exc.value.code == code
    with pytest.raises(DomainError) as exc:
        SetCategoryBudget(uow).execute("nope", 100)
    assert exc.value.code == "NOT_FOUND"


def test_several_goals_are_saved_all_or_nothing(uow: MemoryUnitOfWork) -> None:
    food, health, salary = category(uow, "food"), category(uow, "health"), category(uow, "salary")
    SetCategoryBudgets(uow).execute({food: 70_000, health: None})
    assert uow.categories.get(food).monthly_budget_cents == 70_000  # type: ignore[union-attr]
    bad_goals: list[tuple[dict[str, int | None], str]] = [
        ({food: 90_000, health: -1}, "AMOUNT_NOT_POSITIVE"),
        ({food: 90_000, salary: 10}, "BUDGET_ONLY_FOR_EXPENSES"),
        ({food: 90_000, health: 10**13}, "AMOUNT_TOO_LARGE"),
        ({food: 90_000, "nope": 5}, "NOT_FOUND"),
    ]
    for goals, code in bad_goals:
        with pytest.raises(DomainError) as exc:
            SetCategoryBudgets(uow).execute(goals)
        assert exc.value.code == code
        assert uow.categories.get(food).monthly_budget_cents == 70_000  # type: ignore[union-attr]
    with pytest.raises(DomainError) as exc:
        SetCategoryBudget(uow).execute(food, 10**13)
    assert exc.value.code == "AMOUNT_TOO_LARGE"


# --- the matrix ---


def test_budget_over_the_last_three_closed_months(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    SetCategoryBudget(uow).execute(category(uow, "food"), 70_000)
    SetCategoryBudget(uow).execute(category(uow, "groceries"), 120_000)
    for month, amount in ((7, 91_000), (8, 78_820), (9, 84_230)):
        spend(uow, checking, D(2026, month, 10), amount, "food")
    spend(uow, checking, D(2026, 7, 11), 100_000, "groceries")
    spend(uow, checking, D(2026, 10, 3), 999_999, "food")  # the current month is not closed
    spend(uow, checking, D(2026, 6, 3), 999_999, "food")  # before the window
    spend(uow, checking, D(2026, 8, 12), 20_000, "health")  # no goal
    view = GetBudget(uow, FixedClock(TODAY)).execute()
    budget = view.budget
    assert budget.months == [YM(2026, 7), YM(2026, 8), YM(2026, 9)]
    by_id = {r.category_id: r for r in budget.with_goal}
    food = by_id[category(uow, "food")]
    assert food.months == (91_000, 78_820, 84_230) and food.average_cents == 84_683
    assert food.diff_cents == 14_683 and food.over_months == (True, True, True)
    groceries = by_id[category(uow, "groceries")]
    assert groceries.months == (100_000, 0, 0) and groceries.diff_cents == 33_333 - 120_000
    assert [r.category_id for r in budget.without_goal] == [category(uow, "health")]
    assert budget.goal_total_cents == 190_000 and budget.over_count == 1
    assert view.range is BudgetRange.LAST_3_MONTHS and view.categories[category(uow, "food")]


def test_card_purchases_count_in_the_statement_month(uow: MemoryUnitOfWork, card: Account) -> None:
    SetCategoryBudget(uow).execute(category(uow, "shopping"), 10_000)
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id, "Fone", D(2026, 8, 26), category_id=category(uow, "shopping"),
            total_cents=30_000, installments=3,
        )
    )  # fmt: skip  # statements 2026-09, 2026-10, 2026-11 (closes on the 25th)
    budget = GetBudget(uow, FixedClock(TODAY)).execute().budget
    (row,) = budget.with_goal
    assert row.months == (0, 0, 10_000)  # only the September statement is inside the closed months


def test_budget_for_the_whole_year_uses_the_closed_months_of_that_year(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    SetCategoryBudget(uow).execute(category(uow, "food"), 1_000)
    spend(uow, checking, D(2026, 1, 5), 600, "food")
    spend(uow, checking, D(2026, 3, 5), 1_800, "food")
    view = GetBudget(uow, FixedClock(TODAY)).execute(BudgetRange.YEAR, 2026)
    assert len(view.budget.months) == 9  # January to September
    (row,) = view.budget.with_goal
    assert row.average_cents == 267 and row.over_months[2] is True
    past = GetBudget(uow, FixedClock(TODAY)).execute(BudgetRange.YEAR, 2025)
    assert len(past.budget.months) == 12 and past.budget.with_goal[0].average_cents == 0


def test_budget_with_no_closed_month_is_empty(uow: MemoryUnitOfWork) -> None:
    view = GetBudget(uow, FixedClock(D(2026, 1, 10))).execute(BudgetRange.YEAR, 2026)
    assert view.budget.months == [] and view.budget.over_count == 0


# --- recurring ---


def recurring_setup(uow: MemoryUnitOfWork, checking: Account) -> None:
    # rent: stable; internet: changes in Sep; gym: gone in Sep; streaming: new in Sep
    for month in (7, 8, 9):
        spend(uow, checking, D(2026, month, 5), 165_000, "home", "Aluguel", recurring=True)
    spend(uow, checking, D(2026, 7, 6), 11_990, "telecom", "Internet", recurring=True)
    spend(uow, checking, D(2026, 8, 6), 11_990, "telecom", "Internet", recurring=True)
    spend(uow, checking, D(2026, 9, 6), 12_990, "telecom", "Internet", recurring=True)
    spend(uow, checking, D(2026, 7, 7), 9_000, "health", "Academia", recurring=True)
    spend(uow, checking, D(2026, 8, 7), 9_000, "health", "Academia", recurring=True)
    spend(uow, checking, D(2026, 9, 8), 3_990, "subscriptions", "Streaming Novo", recurring=True)
    spend(uow, checking, D(2026, 9, 9), 5_000, "food", "Variável", recurring=False)  # not recurring


def test_recurring_matrix_and_alerts(uow: MemoryUnitOfWork, checking: Account) -> None:
    recurring_setup(uow, checking)
    view = GetRecurring(uow, FixedClock(TODAY)).execute()
    assert view.months[-1] == YM(2026, 10) == view.current_month and len(view.months) == 7
    labels = {i.label: i for i in view.items}
    assert set(labels) == {"Aluguel", "Internet", "Academia", "Streaming Novo"}  # variable excluded
    assert labels["Internet"].amounts == {
        YM(2026, 7): 11_990,
        YM(2026, 8): 11_990,
        YM(2026, 9): 12_990,
    }
    assert view.items[0].label == "Aluguel"  # biggest first
    alerts = {
        (a.kind, view.labels[a.key]): (a.previous_cents, a.current_cents) for a in view.alerts
    }
    assert alerts == {
        (AlertKind.DISAPPEARED, "Academia"): (9_000, None),
        (AlertKind.CHANGED, "Internet"): (11_990, 12_990),
        (AlertKind.APPEARED, "Streaming Novo"): (None, 3_990),
    }
    assert view.monthly_total_cents[YM(2026, 9)] == 165_000 + 12_990 + 3_990


def test_the_month_in_progress_never_raises_alerts(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    for month in (8, 9):
        spend(uow, checking, D(2026, month, 5), 165_000, "home", "Aluguel", recurring=True)
    # October: not charged yet (it is only the 15th)
    assert GetRecurring(uow, FixedClock(TODAY)).execute().alerts == []


def test_several_entries_in_a_month_add_up_and_accents_are_normalized(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    spend(uow, checking, D(2026, 9, 3), 1_000, "services", "Café São João", recurring=True)
    spend(uow, checking, D(2026, 9, 20), 500, "services", "CAFE SAO JOAO", recurring=True)
    (item,) = GetRecurring(uow, FixedClock(TODAY)).execute().items
    assert item.amounts == {YM(2026, 9): 1_500} and item.label == "CAFE SAO JOAO"
    assert item.key == "cafe sao joao"


def test_recurring_card_entries_follow_their_statement_month(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    for day in (D(2026, 7, 26), D(2026, 8, 26)):  # after closing: statements 2026-08 and 2026-09
        RegisterTransaction(uow).execute(
            RegisterTransactionCommand(
                card.id, day, TransactionKind.EXPENSE, 2_990, "Assinatura", is_recurring=True
            )
        )
    (item,) = GetRecurring(uow, FixedClock(TODAY)).execute().items
    assert item.amounts == {YM(2026, 8): 2_990, YM(2026, 9): 2_990}


# --- daily flow ---


def test_daily_flow_of_a_checking_account(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 6, 30), 100_000))
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 7, 5), TransactionKind.INCOME, 500_000, "Salário"
        )
    )
    spend(uow, checking, D(2026, 7, 5), 12_000, "food")
    spend(uow, checking, D(2026, 7, 9), 30_000, "groceries")
    RegisterInvestmentFlow(uow).execute(
        RegisterInvestmentFlowCommand(
            savings.id, FlowDirection.CONTRIBUTION, D(2026, 7, 9), 50_000, checking.id
        )
    )  # a transfer counts in the cash balance
    spend(uow, checking, D(2026, 8, 1), 1_000, "food")  # outside the period
    view = GetDailyFlow(uow).execute(checking.id, Period.month(YM(2026, 7)))
    assert view.opening_balance_cents == 100_000
    assert [(r.day, r.inflow_cents, r.outflow_cents, r.balance_cents) for r in view.rows] == [
        (D(2026, 7, 5), 500_000, 12_000, 588_000),
        (D(2026, 7, 9), 0, 80_000, 508_000),
    ]
    assert view.closing_balance_cents == 508_000
    assert (view.inflow_cents, view.outflow_cents) == (500_000, 92_000)


def test_daily_flow_without_an_informed_balance_has_no_running_balance(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    spend(uow, checking, D(2026, 7, 5), 1_000, "food")
    view = GetDailyFlow(uow).execute(checking.id, Period.month(YM(2026, 7)))
    assert view.opening_balance_cents is None and view.closing_balance_cents is None
    assert view.rows[0].balance_cents is None and view.rows[0].outflow_cents == 1_000


def test_daily_flow_opening_balance_comes_from_the_nearest_anchor_and_movements(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 6, 1), 100_000))
    spend(uow, checking, D(2026, 6, 10), 10_000, "food")  # before the period: moves the opening
    view = GetDailyFlow(uow).execute(checking.id, Period.month(YM(2026, 7)))
    assert view.opening_balance_cents == 90_000 and view.rows == []
    assert view.closing_balance_cents == 90_000


def test_daily_flow_rules(uow: MemoryUnitOfWork, card: Account) -> None:
    with pytest.raises(DomainError) as exc:
        GetDailyFlow(uow).execute(card.id, Period.month(YM(2026, 7)))
    assert exc.value.code == "CARD_HAS_NO_DAILY_FLOW"
    with pytest.raises(DomainError) as exc:
        GetDailyFlow(uow).execute("nope", Period.month(YM(2026, 7)))
    assert exc.value.code == "NOT_FOUND"
