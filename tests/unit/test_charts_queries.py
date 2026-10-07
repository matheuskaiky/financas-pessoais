"""Chart series (CLAUDE.md 10, 11): net worth over time, month pace, cash flow, categories."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.charts import (
    GetCashFlow,
    GetCategoryBreakdown,
    GetMonthPace,
    GetNetWorthSeries,
)
from financas.application.queries.investments import GetNetWorth
from financas.application.queries.summary import Period
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudgets
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
    SetInvestmentSettings,
)
from financas.application.use_cases.holdings import (
    RecordHoldingValuation,
    RecordHoldingValuationCommand,
    RedeemHolding,
    RedeemHoldingCommand,
    RegisterHolding,
    RegisterHoldingCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
)
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    Indexer,
    InstrumentType,
    InvestmentTracking,
    Liquidity,
    RateMode,
    TransactionKind,
)
from financas.domain.money import YearMonth

D = dt.date
TODAY = D(2026, 10, 2)


def balance(uow: MemoryUnitOfWork, account: Account, day: dt.date, cents: int) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(account.id, day, cents))


def entry(
    uow: MemoryUnitOfWork, account: Account, day: dt.date, cents: int,
    kind: TransactionKind = TransactionKind.EXPENSE, category: str | None = None,
) -> None:  # fmt: skip
    category_id = None
    if category:
        with uow as work:
            found = work.categories.get_by_slug(category)
            assert found is not None
            category_id = found.id
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(account.id, day, kind, cents, "item", category_id)
    )


def series(uow: MemoryUnitOfWork, today: dt.date = TODAY):
    return GetNetWorthSeries(uow, FixedClock(today)).execute()


# --- net worth over time ---


def test_series_is_empty_without_any_informed_balance(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    result = series(uow)
    assert result.points == [] and result.first_date is None
    assert result.partial and [p.name for p in result.pending] == ["Conta Corrente"]


def test_series_follows_balances_and_entries_and_ends_at_todays_net_worth(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    balance(uow, checking, D(2026, 8, 1), 100_000)
    entry(uow, checking, D(2026, 8, 15), 20_000)
    balance(uow, savings, D(2026, 9, 1), 500_000)
    result = series(uow)
    by_day = {p.on: p.cents for p in result.points}
    assert result.first_date == D(2026, 8, 1) and result.points[0].on == D(2026, 8, 1)
    assert by_day[D(2026, 8, 1)] == 100_000
    assert by_day[D(2026, 8, 31)] == 80_000  # the savings account has no valuation yet: not there
    assert by_day[D(2026, 9, 30)] == 580_000
    today_view = GetNetWorth(uow, FixedClock(TODAY)).execute()
    assert result.points[-1].on == TODAY and result.points[-1].cents == today_view.net_worth_cents
    assert result.partial is False and result.pending == []
    assert [p.on for p in result.points] == sorted({p.on for p in result.points})


def test_series_is_partial_and_lists_the_account_with_no_valuation_at_all(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    balance(uow, checking, D(2026, 9, 1), 100_000)
    result = series(uow)
    assert result.partial is True
    assert [(p.kind, p.name) for p in result.pending] == [("account", "Caixinha")]
    assert result.points[-1].cents == 100_000


def test_net_worth_on_a_past_date_ignores_entries_posted_after_it(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    balance(uow, checking, D(2026, 8, 1), 500_000)
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "TV", D(2026, 8, 10), total_cents=100_000)
    )
    clock = FixedClock(TODAY)
    before = GetNetWorth(uow, clock).execute(on=D(2026, 8, 5))
    during = GetNetWorth(uow, clock).execute(on=D(2026, 8, 20))  # statement open, TV on it
    after = GetNetWorth(uow, clock).execute(on=D(2026, 9, 5))  # closed on 08-25, unpaid
    assert (before.net_worth_cents, before.open_statements_cents) == (500_000, 0)
    assert (during.net_worth_cents, during.open_statements_cents) == (400_000, 100_000)
    assert (after.net_worth_cents, after.closed_statements_cents) == (400_000, 100_000)
    assert GetNetWorth(uow, clock).execute(on=D(2027, 1, 1)).net_worth_cents == 400_000  # not past


def test_installments_of_a_plan_already_bought_count_on_the_open_statement(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    balance(uow, checking, D(2026, 8, 1), 500_000)
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Sofá", D(2026, 8, 10), total_cents=90_000, installments=3)
    )
    clock = FixedClock(TODAY)
    mid = GetNetWorth(uow, clock).execute(on=D(2026, 9, 10))
    # installment 1 (Aug statement, closed) + installment 2 (Sep statement, open, posts on 09-25)
    assert mid.closed_statements_cents == 30_000 and mid.open_statements_cents == 30_000
    assert mid.future_installments_cents == 30_000
    assert mid.net_worth_cents == 500_000 - 60_000
    early = GetNetWorth(uow, clock).execute(on=D(2026, 8, 20))
    assert early.open_statements_cents == 30_000  # only installment 1 is on the open statement


def test_redeemed_holdings_still_count_before_the_redemption(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    inst = CreateInstitution(uow).execute(
        CreateInstitutionCommand(name="Inter", group_slug="inter")
    )
    account = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, inst.id, "Corretora")
    )
    SetInvestmentSettings(uow).execute(
        account.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.HOLDINGS
    )
    holding = RegisterHolding(uow).execute(
        RegisterHoldingCommand(
            account_id=account.id,
            name="CDB",
            instrument_type=InstrumentType.CDB,
            issuer_id=inst.id,
            applied_on=D(2026, 3, 1),
            principal_cents=900_000,
            liquidity=Liquidity.DAILY,
            indexer=Indexer.CDI,
            rate_mode=RateMode.PERCENT_OF_INDEX,
            rate_bps=11_000,
            maturity_on=D(2028, 3, 1),
        )
    )
    RecordHoldingValuation(uow).execute(
        RecordHoldingValuationCommand(holding.id, D(2026, 8, 1), 900_000)
    )
    RedeemHolding(uow).execute(RedeemHoldingCommand(holding.id, D(2026, 9, 15), 900_000, None))
    clock = FixedClock(TODAY)
    assert GetNetWorth(uow, clock).execute(on=D(2026, 9, 1)).investments_cents == 900_000
    assert GetNetWorth(uow, clock).execute().investments_cents == 0
    assert GetNetWorth(uow, clock).execute(on=D(2026, 7, 1)).investments_cents == 0  # not applied


# --- month pace ---


def pace(uow: MemoryUnitOfWork, month: YearMonth, today: dt.date = TODAY):
    return GetMonthPace(uow, FixedClock(today)).execute(month)


def test_pace_accumulates_expenses_by_day_and_stops_at_today(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry(uow, checking, D(2026, 10, 1), 10_000)
    entry(uow, checking, D(2026, 10, 1), 5_000)
    entry(uow, checking, D(2026, 10, 2), 2_000)
    entry(uow, checking, D(2026, 10, 2), 9_999, TransactionKind.INCOME, "salary")  # not spending
    result = pace(uow, YearMonth(2026, 10))
    assert (result.days, result.elapsed_days) == (31, 2)
    assert result.daily_cents == [15_000, 2_000] and result.daily_counts == [2, 1]
    assert result.cumulative_cents == [15_000, 17_000]
    assert result.total_cents == 17_000 and result.entry_count == 3
    assert result.average_cents == [] and result.average_months == []  # nothing to compare with
    assert result.ceiling_cents is None and result.crossed_day is None


def test_pace_average_ceiling_and_the_day_it_was_crossed(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    # previous months: July is empty (before the first spending), Aug and Sep have spending
    entry(uow, checking, D(2026, 8, 10), 30_000)
    entry(uow, checking, D(2026, 9, 5), 60_000)
    entry(uow, checking, D(2026, 9, 20), 30_000)
    entry(uow, checking, D(2026, 10, 1), 40_000)
    entry(uow, checking, D(2026, 10, 2), 70_000)
    SetCategoryBudgets(uow).execute({_slug(uow, "food"): 60_000, _slug(uow, "health"): 40_000})
    result = pace(uow, YearMonth(2026, 10))
    assert result.average_months == [YearMonth(2026, 8), YearMonth(2026, 9)]
    # Aug curve: 30_000 from day 10 (carried to the end); Sep: 60_000 from day 5, 90_000 from 20
    assert len(result.average_cents) == 31
    assert result.average_cents[0] == 0 and result.average_cents[4] == 30_000  # (0 + 60_000) / 2
    assert result.average_cents[9] == 45_000 and result.average_cents[19] == 60_000
    assert result.average_cents[30] == 60_000  # (30_000 + 90_000) / 2 at the end of the month
    assert result.ceiling_cents == 100_000
    assert result.cumulative_cents == [40_000, 110_000] and result.crossed_day == 2


def _slug(uow: MemoryUnitOfWork, slug: str) -> str:
    with uow as work:
        found = work.categories.get_by_slug(slug)
        assert found is not None
        return found.id


def test_pace_of_a_past_month_covers_all_days_and_a_future_month_is_empty(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry(uow, checking, D(2026, 2, 28), 1_000)
    past = pace(uow, YearMonth(2026, 2))
    assert (past.days, past.elapsed_days) == (28, 28) and past.cumulative_cents[-1] == 1_000
    future = pace(uow, YearMonth(2026, 12))
    assert future.elapsed_days == 0 and future.cumulative_cents == [] and future.total_cents == 0


def test_pace_counts_card_entries_in_the_statement_month(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    # bought on 09-26, after the 09-25 closing: goes to the October statement (competence Oct)
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Fone", D(2026, 9, 26), total_cents=30_100, installments=3)
    )
    october = pace(uow, YearMonth(2026, 10))
    september = pace(uow, YearMonth(2026, 9))
    assert september.total_cents == 0
    assert october.total_cents == 10_034  # installment 1, posted on 09-26 -> first day of October
    assert october.daily_cents[0] == 10_034 and october.entry_count == 1


# --- cash flow ---


def test_cash_flow_has_one_row_per_month_up_to_the_current_one(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry(uow, checking, D(2026, 8, 3), 9_000_00, TransactionKind.INCOME, "salary")
    entry(uow, checking, D(2026, 8, 9), 4_000_00)
    entry(uow, checking, D(2026, 8, 10), 500_00, TransactionKind.REFUND, "refund")
    entry(uow, checking, D(2026, 10, 2), 100_00)
    flow = GetCashFlow(uow, FixedClock(TODAY)).execute(2026)
    assert [m.month.month for m in flow.months] == list(range(1, 11))
    august = flow.months[7]
    assert (august.income_cents, august.expenses_cents, august.refunds_cents) == (
        900_000,
        350_000,
        50_000,
    )
    assert august.balance_cents == 550_000
    assert flow.months[8].income_cents == 0 and flow.months[9].expenses_cents == 10_000
    assert len(GetCashFlow(uow, FixedClock(TODAY)).execute(2025).months) == 12
    assert GetCashFlow(uow, FixedClock(TODAY)).execute(2027).months == []


# --- categories ---


@pytest.mark.parametrize(("extra", "rest_rows"), [(0, 0), (1, 0), (2, 2), (5, 5)])
def test_category_breakdown_keeps_five_and_folds_the_long_tail(
    uow: MemoryUnitOfWork, checking: Account, extra: int, rest_rows: int
) -> None:
    slugs = ["groceries", "health", "transport", "telecom", "insurance"]
    spare = ["home", "education", "food", "shopping", "services"]
    for n, slug in enumerate(slugs + spare[:extra]):
        entry(uow, checking, D(2026, 9, 3), (100 - n) * 100, category=slug)
    entry(uow, checking, D(2026, 9, 4), 100, category="groceries")  # same category: one row
    result = GetCategoryBreakdown(uow).execute(Period.month(YearMonth(2026, 9)))
    assert len(result.rest) == rest_rows
    assert len(result.top) == (5 if rest_rows else 5 + extra)
    total = sum(s.total_cents for s in result.top) + result.rest_total_cents
    assert total == result.total_cents
    first = result.top[0]
    assert first.total_cents == 10_100 and first.count == 2  # groceries, biggest first
    if rest_rows:
        assert result.rest_count == rest_rows


def test_category_breakdown_of_an_empty_month(uow: MemoryUnitOfWork) -> None:
    result = GetCategoryBreakdown(uow).execute(Period.month(YearMonth(2026, 9)))
    assert result.top == [] and result.rest == [] and result.total_cents == 0


def test_category_breakdown_of_an_itemized_expense_has_no_orphaned_parent(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    from financas.application.use_cases.transactions import (
        SplitItem,
        UpdateTransaction,
        UpdateTransactionCommand,
    )

    day = D(2026, 9, 3)
    entry(uow, checking, day, 30_000, category="groceries")
    entry(uow, checking, day, 4_000, category="food")
    parent = next(t for t in uow.transactions.items.values() if t.amount_cents == -30_000)
    health, home = (uow.categories.get_by_slug(s) for s in ("health", "home"))
    assert health and home
    UpdateTransaction(uow, FixedClock(day)).execute(
        UpdateTransactionCommand(
            parent.id,
            day,
            30_000,
            parent.description,
            splits=(SplitItem("A", health.id, 18_000), SplitItem("B", home.id, 12_000)),
        )
    )
    result = GetCategoryBreakdown(uow).execute(Period.month(YearMonth(2026, 9)))
    shown = {s.category_id: s.total_cents for s in [*result.top, *result.rest]}
    food = uow.categories.get_by_slug("food")
    assert food is not None
    assert shown == {health.id: 18_000, home.id: 12_000, food.id: 4_000}
    assert result.total_cents == 34_000 == sum(shown.values())  # = unsplit entries + child items
