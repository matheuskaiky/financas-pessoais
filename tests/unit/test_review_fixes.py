"""Regression tests for the findings of the code review (domain and application)."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.investments import (
    GetFixedIncomeOverview,
    GetNetWorth,
    ListInvestments,
)
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    DeletePurchase,
    MoveEntryToStatement,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
    SetAccountActive,
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
from financas.application.use_cases.investments import (
    FlowDirection,
    RegisterInvestmentFlow,
    RegisterInvestmentFlowCommand,
)
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    HoldingStatus,
    Institution,
    InstrumentType,
    InvestmentTracking,
    Liquidity,
    TransactionKind,
)
from financas.domain.money import YearMonth

D = dt.date
YM = YearMonth
TODAY = D(2026, 10, 1)


def code(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


@pytest.fixture
def broker(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    account = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, institution.id, "Corretora")
    )
    return SetInvestmentSettings(uow).execute(
        account.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.HOLDINGS
    )


def holding(uow: MemoryUnitOfWork, broker: Account, issuer: Institution, **kw: object):
    values: dict[str, object] = {
        "account_id": broker.id,
        "name": "CDB",
        "instrument_type": InstrumentType.CDB,
        "issuer_id": issuer.id,
        "applied_on": D(2026, 3, 1),
        "principal_cents": 1_000_000,
        "liquidity": Liquidity.DAILY,
    }
    values.update(kw)
    return RegisterHolding(uow).execute(RegisterHoldingCommand(**values))  # type: ignore[arg-type]


def value(uow: MemoryUnitOfWork, h: object, day: dt.date, cents: int):
    return RecordHoldingValuation(uow).execute(
        RecordHoldingValuationCommand(h.id, day, cents)  # type: ignore[attr-defined]
    )


# --- allocation uses the class of each holding ---


def test_holdings_are_allocated_by_their_own_asset_class(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution
) -> None:
    cdb = holding(uow, broker, institution, name="CDB", asset_class=AssetClass.FIXED_INCOME)
    fii = holding(
        uow, broker, institution, name="FII", instrument_type=InstrumentType.REIT,
        asset_class=AssetClass.REAL_ESTATE_FUNDS,
    )  # fmt: skip
    stock = holding(
        uow, broker, institution, name="Ação", instrument_type=InstrumentType.STOCK,
        asset_class=AssetClass.EQUITIES,
    )  # fmt: skip
    for h, cents in ((cdb, 100_000), (fii, 50_000), (stock, 25_000)):
        value(uow, h, D(2026, 9, 1), cents)
    allocation = ListInvestments(uow, FixedClock(TODAY), 35).execute().allocation
    assert {r.asset_class: r.value_cents for r in allocation.rows} == {
        AssetClass.FIXED_INCOME: 100_000,
        AssetClass.REAL_ESTATE_FUNDS: 50_000,
        AssetClass.EQUITIES: 25_000,
    }
    assert allocation.total_cents == 175_000


def test_a_redeemed_holding_leaves_the_allocation(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution
) -> None:
    h = holding(uow, broker, institution)
    value(uow, h, D(2026, 9, 1), 100_000)
    RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 5), 100_500))
    assert ListInvestments(uow, FixedClock(TODAY), 35).execute().allocation.rows == []


# --- the holding is tagged only on the leg of its own account ---


def test_contribution_from_a_non_checking_account_is_refused_and_tags_one_leg(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution, savings: Account,
    checking: Account,
) -> None:  # fmt: skip
    with pytest.raises(DomainError) as exc:
        holding(uow, broker, institution, contribute=True, from_account_id=savings.id)
    assert code(exc) == "ACCOUNT_KIND_NOT_ALLOWED"
    assert uow.holdings.items == {} and uow.transactions.items == {}
    h = holding(uow, broker, institution, contribute=True, from_account_id=checking.id)
    assert {t.account_id: t.holding_id for t in uow.transactions.items.values()} == {
        checking.id: None,
        broker.id: h.id,
    }


def test_a_generic_transfer_between_investment_accounts_tags_only_the_holdings_account(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution, savings: Account
) -> None:
    h = holding(uow, broker, institution)
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(savings.id, broker.id, D(2026, 7, 1), 100, holding_id=h.id)
    )
    assert {leg.account_id: leg.holding_id for leg in legs} == {savings.id: None, broker.id: h.id}


def test_redemption_must_pay_into_a_checking_account(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution, savings: Account
) -> None:
    h = holding(uow, broker, institution)
    with pytest.raises(DomainError) as exc:
        RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 1), 1_000, savings.id))
    assert code(exc) == "ACCOUNT_KIND_NOT_ALLOWED"
    assert uow.holdings.get(h.id).status is HoldingStatus.ACTIVE  # type: ignore[union-attr]


# --- the redemption date ---


def test_redemption_cannot_be_dated_before_the_last_valuation_or_the_application(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution
) -> None:
    h = holding(uow, broker, institution)
    value(uow, h, D(2026, 9, 1), 100_000)
    for day in (D(2026, 6, 1), D(2026, 2, 1), D(2026, 8, 31)):
        with pytest.raises(DomainError) as exc:
            RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, day, 100_000))
        assert code(exc) == "INVALID_REDEMPTION_DATE"
    RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 1), 100_500))  # same day: fine


# --- the valuation difference is measured from the LAST valuation ---


def test_difference_of_an_investment_valuation_uses_the_last_valuation(
    uow: MemoryUnitOfWork, savings: Account
) -> None:
    use_case = RecordBalance(uow)
    use_case.execute(RecordBalanceCommand(savings.id, D(2026, 1, 1), 100_000))
    use_case.execute(RecordBalanceCommand(savings.id, D(2026, 3, 1), 110_000))
    result = use_case.execute(RecordBalanceCommand(savings.id, D(2026, 2, 1), 104_000))
    assert (result.computed_cents, result.difference_cents) == (
        100_000,
        4_000,
    )  # +40,00, not -60,00


def test_checking_difference_still_uses_the_nearest_anchor(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    use_case = RecordBalance(uow)
    use_case.execute(RecordBalanceCommand(checking.id, D(2026, 3, 1), 110_000))
    result = use_case.execute(RecordBalanceCommand(checking.id, D(2026, 2, 1), 104_000))
    assert result.computed_cents == 110_000  # the nearest anchor, here the later one


def test_holding_valuation_difference_uses_the_last_valuation(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution
) -> None:
    h = holding(uow, broker, institution)
    value(uow, h, D(2026, 1, 1), 100_000)
    value(uow, h, D(2026, 3, 1), 110_000)
    result = value(uow, h, D(2026, 2, 1), 104_000)
    assert result.difference_cents == 4_000


# --- paid statements are history ---


def paid_plan(uow: MemoryUnitOfWork, card: Account, checking: Account):
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Fone", D(2026, 7, 26), total_cents=30_100, installments=3)
    )
    first = result.transactions[0].statement_id or ""
    PayStatement(uow, FixedClock(D(2026, 9, 1))).execute(
        PayStatementCommand(first, checking.id, D(2026, 9, 1))
    )
    return result


def test_an_installment_cannot_be_deleted_alone_or_moved_to_a_paid_statement(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    result = paid_plan(uow, card, checking)
    clock = FixedClock(D(2026, 9, 2))
    for entry in result.transactions:
        with pytest.raises(DomainError) as exc:
            DeleteTransaction(uow, clock).execute(entry.id)
        assert code(exc) == "USE_DELETE_PURCHASE"
    with pytest.raises(DomainError) as exc:  # into the paid August statement
        MoveEntryToStatement(uow, clock).execute(result.transactions[1].id, YM(2026, 8))
    assert code(exc) == "STATEMENT_ALREADY_PAID"
    with pytest.raises(DomainError) as exc:  # out of the paid statement
        MoveEntryToStatement(uow, clock).execute(result.transactions[0].id, YM(2026, 10))
    assert code(exc) == "STATEMENT_ALREADY_PAID"
    assert len(uow.transactions.list_by_plan(result.plan.id)) == 3  # type: ignore[union-attr]


def test_a_plain_entry_on_a_paid_statement_cannot_be_deleted(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    plain = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(card.id, D(2026, 8, 10), TransactionKind.EXPENSE, 500, "Mercado")
    )  # statement 2026-08, closing on the 25th
    statement_id = plain.statement_id or ""
    PayStatement(uow, FixedClock(D(2026, 9, 1))).execute(
        PayStatementCommand(statement_id, checking.id, D(2026, 9, 1))
    )
    with pytest.raises(DomainError) as exc:
        DeleteTransaction(uow, FixedClock(D(2026, 9, 2))).execute(plain.id)
    assert code(exc) == "STATEMENT_ALREADY_PAID"
    assert uow.transactions.get(plain.id) is not None


def test_deleting_a_payment_leg_is_still_allowed(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    result = paid_plan(uow, card, checking)
    payments = [t for t in uow.transactions.items.values() if t.kind is TransactionKind.TRANSFER]
    assert len(payments) == 2
    assert DeleteTransaction(uow, FixedClock(D(2026, 9, 2))).execute(payments[0].id) == 2
    # the statement is open to change again, so the plan can be deleted as a whole
    assert DeletePurchase(uow, FixedClock(D(2026, 9, 2))).execute(result.plan.id) == 3  # type: ignore[union-attr]


def test_an_empty_closed_statement_is_not_locked(uow: MemoryUnitOfWork, card: Account) -> None:
    entry = (
        RegisterCardPurchase(uow)
        .execute(CardPurchaseCommand(card.id, "x", D(2026, 8, 26), total_cents=500))
        .transactions[0]
    )  # statement 2026-09
    MoveEntryToStatement(uow, FixedClock(D(2026, 10, 1))).execute(entry.id, YM(2026, 7))
    assert uow.statements.get(uow.transactions.get(entry.id).statement_id or "").month == YM(
        2026, 7
    )  # type: ignore[union-attr]


# --- net worth keeps deactivated accounts that still hold money ---


def test_a_deactivated_account_still_counts_in_net_worth(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 9, 1), 500_000))
    RecordBalance(uow).execute(RecordBalanceCommand(savings.id, D(2026, 9, 1), 100_000))
    SetAccountActive(uow).execute(checking.id, False)
    view = GetNetWorth(uow, FixedClock(TODAY)).execute()
    assert (view.cash_cents, view.investments_cents) == (500_000, 100_000)
    assert view.pending == []


def test_a_deactivated_account_without_balance_is_not_pending(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    SetAccountActive(uow).execute(checking.id, False)
    view = GetNetWorth(uow, FixedClock(TODAY)).execute()
    assert view.pending == [] and view.is_partial is False


# --- simple return after a redemption ---


def test_a_redeemed_holding_does_not_skew_the_account_return(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution
) -> None:
    gone = holding(uow, broker, institution, name="Resgatada")
    kept = holding(uow, broker, institution, name="Ativa")
    value(uow, gone, D(2026, 3, 2), 100_000)
    value(uow, kept, D(2026, 3, 2), 100_000)
    value(uow, kept, D(2026, 9, 1), 110_000)
    RedeemHolding(uow).execute(RedeemHoldingCommand(gone.id, D(2026, 9, 1), 105_000))
    (row,) = ListInvestments(uow, FixedClock(TODAY), 35).execute().accounts
    assert row.yield_cents == 5_000 + 10_000  # both yields count in reais
    assert row.simple_return == pytest.approx(10_000 / 100_000)  # the return uses the live base


# --- the emergency fund average only counts months with history ---


def test_emergency_average_ignores_months_before_the_first_essential_spending(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution, checking: Account
) -> None:
    groceries = uow.categories.get_by_slug("groceries")
    assert groceries
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 9, 5), TransactionKind.EXPENSE, 400_000, "Mercado",
            category_id=groceries.id,
        )
    )  # fmt: skip  # only September (the last closed month) has data
    reserve = holding(uow, broker, institution, is_emergency_fund=True)
    value(uow, reserve, D(2026, 9, 30), 2_000_000)
    emergency = GetFixedIncomeOverview(uow, FixedClock(TODAY), 35, 25_000_000).execute().emergency
    assert emergency.average_essential_cents == 400_000  # not 133.333 (divided by 3)
    assert emergency.months == pytest.approx(5.0)


# --- switching the tracking level ---


def test_switching_level_is_refused_when_the_account_already_has_movements(
    uow: MemoryUnitOfWork, savings: Account, checking: Account
) -> None:
    RegisterInvestmentFlow(uow).execute(
        RegisterInvestmentFlowCommand(
            savings.id, FlowDirection.CONTRIBUTION, D(2026, 7, 1), 100_000, checking.id
        )
    )
    with pytest.raises(DomainError) as exc:
        SetInvestmentSettings(uow).execute(
            savings.id, AssetClass.OTHER, False, tracking=InvestmentTracking.HOLDINGS
        )
    assert code(exc) == "TRACKING_IN_USE"


# --- atomicity: a failure after the first write rolls everything back ---


def boom(*_: object, **__: object) -> None:
    raise RuntimeError("disk full")


def test_redemption_rolls_back_when_a_later_write_fails(
    uow: MemoryUnitOfWork, broker: Account, institution: Institution, checking: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    h = holding(uow, broker, institution)
    monkeypatch.setattr(uow.holdings, "update", boom)  # the last write of the use case
    with pytest.raises(RuntimeError):
        RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 1), 1_000, checking.id))
    assert uow.transactions.items == {}  # the withdrawal legs were rolled back
    assert uow.anchors.list_for_holding(h.id) == []  # and so was the zero valuation
    assert uow.holdings.get(h.id).status is HoldingStatus.ACTIVE  # type: ignore[union-attr]


def test_card_purchase_rolls_back_plan_and_statements_when_saving_entries_fails(
    uow: MemoryUnitOfWork, card: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uow.transactions, "add_many", boom)
    with pytest.raises(RuntimeError):
        RegisterCardPurchase(uow).execute(
            CardPurchaseCommand(card.id, "Fone", D(2026, 7, 26), total_cents=30_100, installments=3)
        )
    assert uow.plans.items == {} and uow.statements.items == {} and uow.transactions.items == {}


def test_creating_an_institution_and_account_keeps_nothing_on_failure(
    uow: MemoryUnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uow.institutions, "add", boom)
    with pytest.raises(RuntimeError):
        CreateInstitution(uow).execute(CreateInstitutionCommand(name="X"))
    assert uow.institutions.items == {}
