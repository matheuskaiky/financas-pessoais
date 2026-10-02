"""Phase 3a application tests: valuations, flows, yield, allocation, net worth (9.6, 10)."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.investments import (
    GetInvestmentPeriodTotals,
    GetNetWorth,
    GetYearEndPosition,
    ListInvestments,
)
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    SetInvestmentSettings,
)
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
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    InvestmentTracking,
    TransactionKind,
)
from financas.domain.money import YearMonth

D = dt.date
K = TransactionKind


def value(
    uow: MemoryUnitOfWork, account: Account, day: dt.date, cents: int, gross: int | None = None
):
    return RecordBalance(uow).execute(
        RecordBalanceCommand(account.id, day, cents, gross_balance_cents=gross)
    )


def flow(
    uow: MemoryUnitOfWork, savings: Account, checking: Account | None, day: dt.date, cents: int,
    direction: FlowDirection = FlowDirection.CONTRIBUTION,
):  # fmt: skip
    return RegisterInvestmentFlow(uow).execute(
        RegisterInvestmentFlowCommand(
            savings.id, direction, day, cents, checking.id if checking else None
        )
    )


def overview(uow: MemoryUnitOfWork, today: dt.date, stale: int = 35):
    return ListInvestments(uow, FixedClock(today), stale).execute()


# --- accounts ---


def test_investment_account_defaults(savings: Account) -> None:
    assert savings.tracking is InvestmentTracking.ACCOUNT
    assert savings.asset_class is AssetClass.OTHER and savings.is_emergency_fund is False


def test_investment_fields_only_for_investment_accounts(
    uow: MemoryUnitOfWork, institution: object
) -> None:
    inst = uow.institutions.list_all()[0]
    ok = CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.INVESTMENT, inst.id, "Tesouro", asset_class=AssetClass.FIXED_INCOME,
            is_emergency_fund=True,
        )
    )  # fmt: skip
    assert ok.asset_class is AssetClass.FIXED_INCOME and ok.is_emergency_fund
    checking = CreateAccount(uow).execute(CreateAccountCommand(AccountKind.CHECKING, inst.id, "CC"))
    assert checking.tracking is None and checking.asset_class is None
    with pytest.raises(DomainError) as exc:
        CreateAccount(uow).execute(
            CreateAccountCommand(AccountKind.CHECKING, inst.id, "X", asset_class=AssetClass.CRYPTO)
        )
    assert exc.value.code == "INVESTMENT_FIELDS_ONLY_FOR_INVESTMENTS"


def test_set_investment_settings(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    updated = SetInvestmentSettings(uow).execute(savings.id, AssetClass.CRYPTO, True)
    assert updated.asset_class is AssetClass.CRYPTO and updated.is_emergency_fund
    with pytest.raises(DomainError) as exc:
        SetInvestmentSettings(uow).execute(checking.id, AssetClass.CRYPTO, False)
    assert exc.value.code == "INVESTMENT_REQUIRED"


# --- valuations (net and gross) ---


def test_valuation_stores_net_and_gross(uow: MemoryUnitOfWork, savings: Account) -> None:
    result = value(uow, savings, D(2026, 7, 31), 1_000_000, gross=1_020_000)
    assert (result.anchor.balance_cents, result.anchor.gross_balance_cents) == (
        1_000_000,
        1_020_000,
    )
    (stored,) = uow.anchors.list_for_account(savings.id)
    assert stored.gross_balance_cents == 1_020_000


def test_gross_rules(uow: MemoryUnitOfWork, savings: Account, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        value(uow, savings, D(2026, 7, 31), 1_000_000, gross=999_999)
    assert exc.value.code == "INVALID_GROSS_BALANCE"
    with pytest.raises(DomainError) as exc:
        value(uow, checking, D(2026, 7, 31), 1_000_000, gross=1_100_000)
    assert exc.value.code == "GROSS_ONLY_FOR_INVESTMENTS"
    assert uow.anchors.items == {}


# --- contributions and withdrawals ---


def test_contribution_and_withdrawal_are_transfers(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    into = flow(uow, savings, checking, D(2026, 7, 10), 100_000)
    assert [(t.account_id, t.amount_cents) for t in into] == [
        (checking.id, -100_000),
        (savings.id, 100_000),
    ]
    assert {t.kind for t in into} == {K.TRANSFER}
    out = flow(uow, savings, checking, D(2026, 7, 20), 30_000, FlowDirection.WITHDRAWAL)
    assert [(t.account_id, t.amount_cents) for t in out] == [
        (savings.id, -30_000),
        (checking.id, 30_000),
    ]


def test_flow_from_an_untracked_account_has_one_leg(
    uow: MemoryUnitOfWork, savings: Account
) -> None:
    (leg,) = flow(uow, savings, None, D(2026, 7, 10), 50_000)
    assert (leg.account_id, leg.amount_cents) == (savings.id, 50_000)


def test_flow_rules(
    uow: MemoryUnitOfWork, savings: Account, checking: Account, card: Account
) -> None:
    for kwargs, code in [
        ({"investment": checking, "other": None}, "INVESTMENT_REQUIRED"),
        ({"investment": savings, "other": card}, "ACCOUNT_KIND_NOT_ALLOWED"),
        ({"investment": savings, "other": savings}, "ACCOUNT_KIND_NOT_ALLOWED"),
    ]:
        with pytest.raises(DomainError) as exc:
            flow(uow, kwargs["investment"], kwargs["other"], D(2026, 7, 1), 100)
        assert exc.value.code == code
    with pytest.raises(DomainError) as exc:
        flow(uow, savings, checking, D(2026, 7, 1), 0)
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"
    assert uow.transactions.items == {}


# --- overview: value, flows, yield, staleness, pending, allocation ---


def test_overview_current_value_yield_and_return(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    value(uow, savings, D(2026, 7, 1), 1_000_000)
    flow(uow, savings, checking, D(2026, 7, 10), 100_000)
    value(uow, savings, D(2026, 7, 31), 1_112_000)
    flow(uow, savings, checking, D(2026, 8, 5), 50_000)  # after the last valuation
    result = overview(uow, D(2026, 8, 10))
    (row,) = result.accounts
    assert row.current_value_cents == 1_112_000 + 50_000
    assert row.net_contributions_cents == 100_000  # between the first and the last valuation
    assert row.yield_cents == 12_000
    assert row.simple_return == pytest.approx(12_000 / 1_100_000)
    assert row.age_days == 10 and row.stale is False
    assert result.total_cents == 1_162_000 and result.pending == []
    assert row.share == pytest.approx(1.0)


def test_stale_valuation_is_flagged(uow: MemoryUnitOfWork, savings: Account) -> None:
    value(uow, savings, D(2026, 7, 25), 500_000)
    assert overview(uow, D(2026, 8, 25)).accounts[0].stale is False
    row = overview(uow, D(2026, 9, 1)).accounts[0]
    assert row.age_days == 38 and row.stale is True
    assert overview(uow, D(2026, 9, 1), stale=60).accounts[0].stale is False


def test_account_without_valuation_is_pending_and_left_out(
    uow: MemoryUnitOfWork, savings: Account, institution: object
) -> None:
    inst = uow.institutions.list_all()[0]
    crypto = CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.INVESTMENT, inst.id, "Exchange", asset_class=AssetClass.CRYPTO
        )
    )
    value(uow, savings, D(2026, 7, 1), 800_000)
    result = overview(uow, D(2026, 7, 10))
    assert result.total_cents == 800_000
    assert [a.id for a in result.pending] == [crypto.id]
    by_id = {r.account.id: r for r in result.accounts}
    assert by_id[crypto.id].current_value_cents is None and by_id[crypto.id].share is None
    assert result.allocation.pending_count == 1


def test_allocation_by_asset_class(uow: MemoryUnitOfWork, savings: Account) -> None:
    inst = uow.institutions.list_all()[0]
    funds = CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.INVESTMENT, inst.id, "FIIs", asset_class=AssetClass.REAL_ESTATE_FUNDS
        )
    )
    SetInvestmentSettings(uow).execute(savings.id, AssetClass.FIXED_INCOME, False)
    value(uow, savings, D(2026, 7, 1), 8_222_657)
    value(uow, funds, D(2026, 7, 1), 948_000)
    allocation = overview(uow, D(2026, 7, 2)).allocation
    assert [(r.asset_class, r.value_cents) for r in allocation.rows] == [
        (AssetClass.FIXED_INCOME, 8_222_657),
        (AssetClass.REAL_ESTATE_FUNDS, 948_000),
    ]
    assert round(allocation.rows[0].share * 100, 1) == 89.7


# --- period totals: contributions, capitalized yield (not income), distributions ---


def test_year_totals_keep_capitalized_yield_out_of_income(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    value(uow, savings, D(2025, 12, 31), 1_000_000)
    flow(uow, savings, checking, D(2026, 3, 1), 200_000)
    flow(uow, savings, checking, D(2026, 9, 1), 50_000, FlowDirection.WITHDRAWAL)
    value(uow, savings, D(2026, 12, 31), 1_240_000)
    income = uow.categories.get_by_slug("investment_income")
    assert income
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 9, 10), K.INCOME, 14_230, "FII", category_id=income.id
        )
    )
    totals = GetInvestmentPeriodTotals(uow).execute(Period.year(2026))
    assert totals.net_contributions_cents == 150_000
    assert totals.capitalized_yield_cents == 1_240_000 - 1_000_000 - 150_000
    assert totals.simple_return == pytest.approx(90_000 / 1_150_000)
    assert totals.distributions_cents == 14_230
    summary = GetSummary(uow).execute(Period.year(2026))
    assert summary.income_cents == 14_230  # only the distribution; the yield is not income
    assert totals.pending_accounts == []


def test_period_totals_flag_accounts_without_two_valuations(
    uow: MemoryUnitOfWork, savings: Account
) -> None:
    value(uow, savings, D(2026, 5, 1), 100)
    totals = GetInvestmentPeriodTotals(uow).execute(Period.year(2026))
    assert totals.capitalized_yield_cents is None and totals.simple_return is None
    assert [a.id for a in totals.pending_accounts] == [savings.id]


def test_month_summary_has_net_contributions_and_investment_rate(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(checking.id, D(2026, 7, 5), K.INCOME, 1_000_000, "Salário")
    )
    flow(uow, savings, checking, D(2026, 7, 6), 250_000)
    flow(uow, savings, checking, D(2026, 7, 20), 50_000, FlowDirection.WITHDRAWAL)
    summary = GetSummary(uow).execute(Period.month(YearMonth(2026, 7)))
    assert summary.net_contributions_cents == 200_000
    assert summary.investment_rate == pytest.approx(0.2)
    assert (
        summary.income_cents == 1_000_000 and summary.expenses_cents == 0
    )  # transfers are neither
    empty = GetSummary(uow).execute(Period.month(YearMonth(2026, 8)))
    assert empty.net_contributions_cents == 0 and empty.investment_rate is None


# --- year-end position (31 Dec) ---


def test_year_end_position_per_account(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    value(uow, savings, D(2025, 12, 31), 700_000)
    flow(uow, savings, checking, D(2026, 6, 1), 60_000)
    value(uow, savings, D(2026, 12, 31), 800_000)
    flow(uow, savings, checking, D(2027, 1, 5), 10_000)  # after the year end
    position = GetYearEndPosition(uow).execute(2026)
    (row,) = position.rows
    assert position.date == D(2026, 12, 31)
    assert row.value_cents == 800_000 and row.yield_cents == 40_000
    assert position.total_cents == 800_000 and position.total_yield_cents == 40_000
    assert position.pending == []
    earlier = GetYearEndPosition(uow).execute(2025)
    assert earlier.total_cents == 700_000 and earlier.total_yield_cents is None


def test_year_end_position_lists_accounts_without_valuation(
    uow: MemoryUnitOfWork, savings: Account
) -> None:
    position = GetYearEndPosition(uow).execute(2026)
    assert position.total_cents == 0 and [a.id for a in position.pending] == [savings.id]


# --- net worth ---


def test_net_worth_is_cash_plus_investments_minus_statements(
    uow: MemoryUnitOfWork, checking: Account, savings: Account, card: Account
) -> None:
    value(uow, checking, D(2026, 9, 1), 760_045)
    value(uow, savings, D(2026, 9, 1), 9_170_657)
    # closed statement (Aug: closes 2026-08-25) and open statement (Oct) and a future installment
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Agosto", D(2026, 8, 10), total_cents=100_000)
    )
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id, "Parcelada", D(2026, 9, 26), total_cents=90_000, installments=3
        )
    )
    view = GetNetWorth(uow, FixedClock(D(2026, 10, 1))).execute()
    assert view.cash_cents == 760_045 and view.investments_cents == 9_170_657
    assert view.closed_statements_cents == 100_000 + 0
    assert view.open_statements_cents == 30_000  # installment 1 of the plan, October statement
    assert view.future_installments_cents == 60_000
    assert view.net_worth_cents == 760_045 + 9_170_657 - 100_000 - 30_000
    assert view.pending == [] and view.is_partial is False


def test_net_worth_is_partial_when_a_balance_or_valuation_is_missing(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    value(uow, checking, D(2026, 9, 1), 100_000)
    view = GetNetWorth(uow, FixedClock(D(2026, 9, 2))).execute()
    assert view.net_worth_cents == 100_000 and view.is_partial
    assert [a.id for a in view.pending] == [savings.id]


def test_net_worth_follows_entries_after_the_informed_balance(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    value(uow, checking, D(2026, 9, 1), 100_000)
    value(uow, savings, D(2026, 9, 1), 500_000)
    flow(uow, savings, checking, D(2026, 9, 10), 40_000)  # moves money between them: net worth same
    view = GetNetWorth(uow, FixedClock(D(2026, 9, 15))).execute()
    assert (view.cash_cents, view.investments_cents) == (60_000, 540_000)
    assert view.net_worth_cents == 600_000
