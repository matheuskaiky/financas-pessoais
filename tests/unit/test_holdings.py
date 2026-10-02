"""Phase 3b application tests: holdings, valuations, redemption and the Brazilian views."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.investments import (
    GetFixedIncomeOverview,
    GetNetWorth,
    GetYearEndPosition,
    ListHoldings,
    ListInvestments,
)
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
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
    SetHoldingFlags,
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
    HoldingStatus,
    Indexer,
    Institution,
    InstrumentType,
    InvestmentHolding,
    InvestmentTracking,
    Liquidity,
    RateMode,
    TransactionKind,
)
from financas.domain.services.holdings import LiquidityBucket

D = dt.date
TODAY = D(2026, 10, 1)


@pytest.fixture
def inter(uow: MemoryUnitOfWork) -> Institution:
    return CreateInstitution(uow).execute(
        CreateInstitutionCommand(name="Inter", group_slug="inter")
    )


@pytest.fixture
def broker(uow: MemoryUnitOfWork, inter: Institution) -> Account:
    """An investment account tracked per holding (the Inter account)."""
    account = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, inter.id, "Inter investimentos")
    )
    return SetInvestmentSettings(uow).execute(
        account.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.HOLDINGS
    )


def register(
    uow: MemoryUnitOfWork, broker: Account, issuer: Institution, **kw: object
) -> InvestmentHolding:
    values: dict[str, object] = {
        "account_id": broker.id,
        "name": "CDB Inter 110% CDI",
        "instrument_type": InstrumentType.CDB,
        "issuer_id": issuer.id,
        "applied_on": D(2026, 3, 1),
        "principal_cents": 1_000_000,
        "liquidity": Liquidity.AT_MATURITY,
        "indexer": Indexer.CDI,
        "rate_mode": RateMode.PERCENT_OF_INDEX,
        "rate_bps": 11_000,
        "maturity_on": D(2028, 3, 1),
    }
    values.update(kw)
    return RegisterHolding(uow).execute(RegisterHoldingCommand(**values))  # type: ignore[arg-type]


def value(
    uow: MemoryUnitOfWork, holding: InvestmentHolding, day: dt.date, cents: int, **kw: object
):
    return RecordHoldingValuation(uow).execute(
        RecordHoldingValuationCommand(holding.id, day, cents, **kw)  # type: ignore[arg-type]
    )


def flow(
    uow: MemoryUnitOfWork, broker: Account, holding: InvestmentHolding | None, day: dt.date,
    cents: int, other: Account | None = None, direction: FlowDirection = FlowDirection.CONTRIBUTION,
):  # fmt: skip
    return RegisterInvestmentFlow(uow).execute(
        RegisterInvestmentFlowCommand(
            broker.id, direction, day, cents, other.id if other else None,
            holding_id=holding.id if holding else None,
        )
    )  # fmt: skip


def views(uow: MemoryUnitOfWork, today: dt.date = TODAY, **kw: object):
    return ListHoldings(uow, FixedClock(today), 35).execute(**kw)  # type: ignore[arg-type]


# --- one level per account ---


def test_tracking_level_rules(uow: MemoryUnitOfWork, savings: Account, broker: Account) -> None:
    assert (
        savings.tracking is InvestmentTracking.ACCOUNT
        and broker.tracking is InvestmentTracking.HOLDINGS
    )
    with pytest.raises(
        DomainError
    ) as exc:  # a holdings-level account has no whole-account valuation
        RecordBalance(uow).execute(RecordBalanceCommand(broker.id, D(2026, 7, 1), 100))
    assert exc.value.code == "ACCOUNT_TRACKS_HOLDINGS"
    with pytest.raises(DomainError) as exc:  # and an account-level one has no holdings
        RegisterHolding(uow).execute(
            RegisterHoldingCommand(
                savings.id, "x", InstrumentType.CDB, "i", D(2026, 1, 1), 100, Liquidity.DAILY
            )
        )
    assert exc.value.code == "ACCOUNT_NOT_HOLDINGS_LEVEL"


def test_switching_level_is_refused_while_the_current_level_has_data(
    uow: MemoryUnitOfWork, savings: Account, inter: Institution, broker: Account
) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(savings.id, D(2026, 7, 1), 100_000))
    with pytest.raises(DomainError) as exc:
        SetInvestmentSettings(uow).execute(
            savings.id, AssetClass.OTHER, False, tracking=InvestmentTracking.HOLDINGS
        )
    assert exc.value.code == "TRACKING_IN_USE"
    register(uow, broker, inter)
    with pytest.raises(DomainError) as exc:
        SetInvestmentSettings(uow).execute(
            broker.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.ACCOUNT
        )
    assert exc.value.code == "TRACKING_IN_USE"
    empty = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, inter.id, "Vazia")
    )
    switched = SetInvestmentSettings(uow).execute(
        empty.id, AssetClass.OTHER, False, tracking=InvestmentTracking.HOLDINGS
    )
    assert switched.tracking is InvestmentTracking.HOLDINGS


# --- registering holdings ---


def test_register_holding_stores_the_contract_data(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    h = register(uow, broker, inter, name="  CDB Inter 110% CDI  ")
    assert h.name == "CDB Inter 110% CDI" and h.status is HoldingStatus.ACTIVE
    assert (h.indexer, h.rate_mode, h.rate_bps) == (Indexer.CDI, RateMode.PERCENT_OF_INDEX, 11_000)
    assert h.asset_class is AssetClass.FIXED_INCOME  # the account's class
    assert uow.holdings.get(h.id) == h
    assert uow.transactions.items == {}  # contract data only: nothing moved


@pytest.mark.parametrize(
    ("kind", "covered"),
    [
        (InstrumentType.CDB, True),
        (InstrumentType.LCI, True),
        (InstrumentType.SAVINGS_ACCOUNT, True),
        (InstrumentType.TREASURY_IPCA, False),
        (InstrumentType.DEBENTURE, False),
        (InstrumentType.REIT, False),
    ],
)
def test_fgc_is_suggested_by_type_and_editable(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, kind: InstrumentType, covered: bool
) -> None:
    h = register(uow, broker, inter, instrument_type=kind)
    assert h.fgc_covered is covered
    overridden = register(
        uow, broker, inter, name="x", instrument_type=kind, fgc_covered=not covered
    )
    assert overridden.fgc_covered is (not covered)
    flipped = SetHoldingFlags(uow).execute(h.id, not covered, True)
    assert flipped.fgc_covered is (not covered) and flipped.is_emergency_fund is True


def test_register_holding_validation(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    for overrides, code in [
        ({"issuer_id": "nope"}, "NOT_FOUND"),
        ({"rate_bps": None}, "INVALID_RATE"),
        ({"maturity_on": None}, "MATURITY_REQUIRED"),
        ({"maturity_on": D(2026, 1, 1)}, "INVALID_MATURITY"),
        ({"principal_cents": 0}, "AMOUNT_NOT_POSITIVE"),
    ]:
        with pytest.raises(DomainError) as exc:
            register(uow, broker, inter, **overrides)
        assert exc.value.code == code
    assert uow.holdings.items == {}


def test_registering_with_contribution_creates_a_tagged_transfer(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, checking: Account
) -> None:
    h = register(uow, broker, inter, contribute=True, from_account_id=checking.id)
    out, into = sorted(uow.transactions.items.values(), key=lambda t: t.amount_cents)
    assert (out.account_id, out.amount_cents, out.holding_id) == (checking.id, -1_000_000, None)
    assert (into.account_id, into.amount_cents, into.holding_id) == (broker.id, 1_000_000, h.id)
    assert out.posted_on == D(2026, 3, 1) and out.kind is TransactionKind.TRANSFER


# --- valuations, flows, yield ---


def test_holding_valuation_flow_and_yield(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, checking: Account
) -> None:
    h = register(uow, broker, inter)
    value(uow, h, D(2026, 7, 1), 1_000_000, gross_balance_cents=1_030_000)
    flow(uow, broker, h, D(2026, 7, 10), 100_000, checking)
    result = value(uow, h, D(2026, 7, 31), 1_112_000)
    assert (result.computed_cents, result.difference_cents) == (1_100_000, 12_000)
    (view,) = views(uow)
    assert view.current_value_cents == 1_112_000 and view.yield_cents == 12_000
    assert view.net_contributions_cents == 100_000
    assert view.simple_return == pytest.approx(12_000 / 1_100_000)
    assert view.last_valuation and view.last_valuation.gross_balance_cents is None
    assert view.issuer.id == inter.id and view.age_days == 62 and view.stale is True


def test_holding_valuation_rules(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    h = register(uow, broker, inter)
    with pytest.raises(DomainError) as exc:
        value(uow, h, D(2026, 7, 1), 100, gross_balance_cents=90)
    assert exc.value.code == "INVALID_GROSS_BALANCE"
    with pytest.raises(DomainError) as exc:
        RecordHoldingValuation(uow).execute(
            RecordHoldingValuationCommand("nope", D(2026, 7, 1), 100)
        )
    assert exc.value.code == "NOT_FOUND"
    value(uow, h, D(2026, 7, 1), 100)
    value(uow, h, D(2026, 7, 1), 150)  # same day: replaced
    assert [a.balance_cents for a in uow.anchors.list_for_holding(h.id)] == [150]
    assert uow.anchors.list_for_account(broker.id) == []  # holding valuations are not the account's


def test_flows_need_the_holding_on_a_holdings_level_account(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, savings: Account, checking: Account
) -> None:
    h = register(uow, broker, inter)
    other = register(uow, CreateAccountAsHoldings(uow, inter), inter, name="outra")
    for target, holding, code in [
        (broker, None, "HOLDING_REQUIRED"),
        (broker, other, "HOLDING_NOT_IN_ACCOUNT"),
        (savings, h, "ACCOUNT_NOT_HOLDINGS_LEVEL"),
    ]:
        with pytest.raises(DomainError) as exc:
            flow(uow, target, holding, D(2026, 7, 1), 100, checking)
        assert exc.value.code == code
    legs = flow(uow, broker, h, D(2026, 7, 1), 100, checking)
    assert [leg.holding_id for leg in legs] == [None, h.id]


def CreateAccountAsHoldings(uow: MemoryUnitOfWork, inst: Institution) -> Account:
    account = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, inst.id, "Outra corretora")
    )
    return SetInvestmentSettings(uow).execute(
        account.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.HOLDINGS
    )


def test_account_overview_is_the_sum_of_its_holdings_and_lists_pending(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    a = register(uow, broker, inter, name="CDB A")
    b = register(uow, broker, inter, name="CDB B")
    register(uow, broker, inter, name="CDB C")  # no valuation: pending
    value(uow, a, D(2026, 9, 1), 1_000_000)
    value(uow, b, D(2026, 9, 1), 500_000)
    overview = ListInvestments(uow, FixedClock(TODAY), 35).execute()
    (row,) = overview.accounts
    assert row.current_value_cents == 1_500_000 and overview.total_cents == 1_500_000
    assert [h.name for h in overview.pending_holdings] == ["CDB C"]
    assert overview.pending == []  # the account itself has a value
    assert len(row.holdings) == 3 and row.stale is False and row.age_days == 30


def test_account_without_holdings_or_valuations_is_pending(
    uow: MemoryUnitOfWork, broker: Account
) -> None:
    overview = ListInvestments(uow, FixedClock(TODAY), 35).execute()
    assert overview.total_cents == 0 and [a.id for a in overview.pending] == [broker.id]


# --- redemption ---


def test_redeem_a_holding_at_maturity(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, checking: Account
) -> None:
    h = register(
        uow, broker, inter, contribute=True, from_account_id=checking.id, principal_cents=1_000_000
    )
    value(uow, h, D(2026, 3, 2), 1_000_000)
    value(uow, h, D(2027, 2, 28), 1_100_000)
    legs = RedeemHolding(uow).execute(
        RedeemHoldingCommand(h.id, D(2027, 3, 1), 1_102_000, checking.id)
    )
    assert [(leg.account_id, leg.amount_cents) for leg in legs] == [
        (broker.id, -1_102_000),
        (checking.id, 1_102_000),
    ]
    assert legs[0].holding_id == h.id and uow.holdings.get(h.id).status is HoldingStatus.REDEEMED  # type: ignore[union-attr]
    zero = uow.anchors.list_for_holding(h.id)[-1]
    assert (zero.on_date, zero.balance_cents) == (D(2027, 3, 1), 0)
    clock = FixedClock(D(2027, 6, 1))
    (view,) = ListHoldings(uow, clock, 35).execute(include_redeemed=True)
    assert view.current_value_cents == 0 and view.stale is False
    assert view.yield_cents == 102_000  # payout minus what was put in: no tax computed
    assert ListHoldings(uow, clock, 35).execute() == []  # redeemed holdings are hidden by default
    overview = ListInvestments(uow, clock, 35).execute()
    assert overview.total_cents == 0 and overview.pending == [] and overview.pending_holdings == []


def test_a_redeemed_holding_accepts_nothing_more(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, checking: Account
) -> None:
    h = register(uow, broker, inter)
    RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 1), 1_000_000))
    for action in (
        lambda: RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 2), 1)),
        lambda: value(uow, h, D(2026, 9, 3), 100),
        lambda: flow(uow, broker, h, D(2026, 9, 3), 100, checking),
    ):
        with pytest.raises(DomainError) as exc:
            action()
        assert exc.value.code == "HOLDING_REDEEMED"


def test_redemption_is_atomic(uow: MemoryUnitOfWork, broker: Account, inter: Institution) -> None:
    h = register(uow, broker, inter)
    with pytest.raises(DomainError):
        RedeemHolding(uow).execute(RedeemHoldingCommand(h.id, D(2026, 9, 1), 0))  # amount 0
    assert uow.holdings.get(h.id).status is HoldingStatus.ACTIVE  # type: ignore[union-attr]
    assert uow.transactions.items == {} and uow.anchors.list_for_holding(h.id) == []


# --- Brazilian views: ladder, liquidity, FGC, emergency fund ---


def fixed_income(uow: MemoryUnitOfWork, limit: int = 25_000_000):
    return GetFixedIncomeOverview(uow, FixedClock(TODAY), 35, limit).execute()


def test_ladder_and_liquidity_from_the_valued_holdings(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    a = register(uow, broker, inter, name="A", maturity_on=D(2027, 9, 10))
    b = register(uow, broker, inter, name="B", maturity_on=D(2028, 3, 1))
    c = register(
        uow, broker, inter, name="C", liquidity=Liquidity.DAILY, maturity_on=None,
        indexer=None, rate_mode=None, rate_bps=None,
    )  # fmt: skip
    d = register(
        uow, broker, inter, name="D", liquidity=Liquidity.DAILY, liquid_from=D(2026, 10, 20),
        maturity_on=D(2029, 1, 1),
    )  # fmt: skip
    register(uow, broker, inter, name="E")  # never valued: pending
    for h, cents in ((a, 1_272_025), (b, 1_592_030), (c, 500_000), (d, 300_000)):
        value(uow, h, D(2026, 9, 30), cents)
    overview = fixed_income(uow)
    assert [(str(r.month), r.value_cents) for r in overview.ladder] == [
        ("2027-09", 1_272_025),
        ("2028-03", 1_592_030),
        ("2029-01", 300_000),
    ]
    buckets = {r.bucket: r.value_cents for r in overview.liquidity}
    assert buckets[LiquidityBucket.TODAY] == 500_000
    assert buckets[LiquidityBucket.WITHIN_30] == 300_000  # grace period ends 2026-10-20
    assert buckets[LiquidityBucket.WITHIN_365] == 1_272_025  # matures 2027-09-10: 344 days
    assert buckets[LiquidityBucket.LATER] == 1_592_030
    assert [h.name for h in overview.pending] == ["E"]
    assert {v.holding.name for v in overview.holdings} == {"A", "B", "C", "D"}


def test_fgc_exposure_sums_covered_holdings_and_checking_of_the_group(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    # checking account at the same institution group
    inter_checking = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, inter.id, "Conta Inter")
    )
    RecordBalance(uow).execute(RecordBalanceCommand(inter_checking.id, D(2026, 9, 30), 132_000))
    cdb = register(uow, broker, inter, name="CDB", instrument_type=InstrumentType.CDB)
    lca = register(uow, broker, inter, name="LCA", instrument_type=InstrumentType.LCA)
    bond = register(
        uow, broker, inter, name="Tesouro", instrument_type=InstrumentType.TREASURY_IPCA
    )
    value(uow, cdb, D(2026, 9, 30), 1_500_000)
    value(uow, lca, D(2026, 9, 30), 1_364_055)
    value(uow, bond, D(2026, 9, 30), 9_000_000)  # not covered: out of the FGC
    (row,) = fixed_income(uow).fgc
    assert row.group == "inter"
    assert row.covered_holdings_cents == 2_864_055 and row.checking_cents == 132_000
    assert row.exposure_cents == 2_996_055
    assert round(row.percent_of_limit, 1) == 12.0 and not row.exceeded
    (tight,) = fixed_income(uow, limit=2_000_000).fgc
    assert tight.exceeded


def test_fgc_groups_default_to_the_institution_slug(uow: MemoryUnitOfWork, broker: Account) -> None:
    nu = CreateInstitution(uow).execute(CreateInstitutionCommand(name="Nubank"))  # no group
    h = register(uow, broker, nu, name="Caixinha", instrument_type=InstrumentType.CDB)
    value(uow, h, D(2026, 9, 30), 100_000)
    (row,) = fixed_income(uow).fgc
    assert row.group == "nubank"


def test_emergency_fund_coverage_in_months(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, checking: Account
) -> None:
    groceries = uow.categories.get_by_slug("groceries")
    food = uow.categories.get_by_slug("food")  # non-essential: not counted
    assert groceries and food
    for month in (7, 8, 9):  # the 3 closed months before October
        for day, category, cents in ((5, groceries, 415_000), (6, food, 90_000)):
            RegisterTransaction(uow).execute(
                RegisterTransactionCommand(
                    checking.id, D(2026, month, day), TransactionKind.EXPENSE, cents, "x",
                    category_id=category.id,
                )
            )  # fmt: skip
    reserve = register(uow, broker, inter, name="Reserva", is_emergency_fund=True)
    other = register(uow, broker, inter, name="Outro")
    value(uow, reserve, D(2026, 9, 30), 2_492_615)
    value(uow, other, D(2026, 9, 30), 7_000_000)
    emergency = fixed_income(uow).emergency
    assert emergency.value_cents == 2_492_615 and emergency.items == ["Reserva"]
    assert emergency.average_essential_cents == 415_000
    assert emergency.months is not None and round(emergency.months, 1) == 6.0


def test_emergency_fund_includes_marked_account_level_accounts_and_has_no_months_without_spending(
    uow: MemoryUnitOfWork, savings: Account
) -> None:
    SetInvestmentSettings(uow).execute(savings.id, AssetClass.FIXED_INCOME, True)
    RecordBalance(uow).execute(RecordBalanceCommand(savings.id, D(2026, 9, 1), 600_000))
    emergency = fixed_income(uow).emergency
    assert emergency.value_cents == 600_000 and emergency.items == ["Caixinha"]
    assert emergency.months is None  # no essential spending to divide by


# --- year end and net worth with holdings ---


def test_year_end_position_has_one_row_per_holding(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution
) -> None:
    a = register(uow, broker, inter, name="A")
    b = register(uow, broker, inter, name="B")
    register(uow, broker, inter, name="C")
    value(uow, a, D(2025, 12, 31), 1_000_000)
    value(uow, a, D(2026, 12, 31), 1_100_000)
    value(uow, b, D(2026, 11, 1), 500_000)
    position = GetYearEndPosition(uow).execute(2026)
    by_name = {r.holding.name: r for r in position.rows if r.holding}
    assert by_name["A"].value_cents == 1_100_000 and by_name["A"].yield_cents == 100_000
    assert by_name["B"].value_cents == 500_000 and by_name["B"].yield_cents is None
    assert by_name["C"].value_cents is None
    assert position.total_cents == 1_600_000
    assert [h.name for h in position.pending_holdings] == ["C"] and position.pending == []


def test_net_worth_includes_holdings_and_flags_pending_ones(
    uow: MemoryUnitOfWork, broker: Account, inter: Institution, checking: Account
) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 9, 1), 100_000))
    a = register(uow, broker, inter, name="A")
    register(uow, broker, inter, name="B")
    value(uow, a, D(2026, 9, 1), 900_000)
    view = GetNetWorth(uow, FixedClock(TODAY)).execute()
    assert view.investments_cents == 900_000 and view.net_worth_cents == 1_000_000
    assert view.is_partial and [h.name for h in view.pending_holdings] == ["B"]
