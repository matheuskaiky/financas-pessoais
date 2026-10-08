"""Dated snapshots, profit since the beginning and checking <-> investment transfers (9.6)."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.investments import GetInvestmentProfit, ListHoldings
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    SetInvestmentSettings,
)
from financas.application.use_cases.holdings import (
    DeleteHoldingSnapshot,
    RecordHoldingSnapshot,
    RecordHoldingSnapshotCommand,
    RegisterHolding,
    RegisterHoldingCommand,
)
from financas.application.use_cases.transactions import RegisterTransfer, RegisterTransferCommand
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    Indexer,
    Institution,
    InstrumentType,
    InvestmentHolding,
    InvestmentTracking,
    Liquidity,
    RateMode,
    TransactionKind,
)
from financas.domain.services.balances import AnchorPoint
from financas.domain.services.investments import profit_timeline

D = dt.date
TODAY = D(2026, 6, 30)


def points(*pairs: tuple[dt.date, int]) -> list[AnchorPoint]:
    return [AnchorPoint(d, c) for d, c in pairs]


# --- the pure math ---


def test_profit_is_the_balance_minus_what_was_contributed_up_to_the_day() -> None:
    flows = [(D(2026, 1, 10), 100_000)]
    (first, second) = profit_timeline(
        points((D(2026, 1, 31), 101_000), (D(2026, 2, 28), 102_500)), flows
    )
    assert (first.net_contributions_cents, first.profit_cents) == (100_000, 1_000)
    assert first.return_rate == pytest.approx(0.01)
    assert (second.profit_cents, second.return_rate) == (2_500, pytest.approx(0.025))
    assert first.period_profit_cents is None and first.change_rate is None


def test_the_period_profit_removes_the_contributions_of_the_period() -> None:
    flows = [(D(2026, 1, 10), 100_000), (D(2026, 2, 10), 50_000)]
    _, second = profit_timeline(points((D(2026, 1, 31), 101_000), (D(2026, 2, 28), 153_000)), flows)
    assert second.period_contributions_cents == 50_000
    assert second.balance_change_cents == 52_000
    assert second.period_profit_cents == 2_000  # 52_000 - 50_000
    assert second.change_rate == pytest.approx(52_000 / 101_000)


def test_a_withdrawal_lowers_the_cost_and_keeps_the_profit_consistent() -> None:
    flows = [(D(2026, 1, 10), 100_000), (D(2026, 2, 15), -40_000)]
    first, second = profit_timeline(
        points((D(2026, 1, 31), 105_000), (D(2026, 2, 28), 66_000)), flows
    )
    assert first.profit_cents == 5_000
    assert second.net_contributions_cents == 60_000
    assert second.profit_cents == 6_000  # 66_000 - 60_000
    assert second.period_profit_cents == 1_000  # (66_000 - 105_000) - (-40_000)
    assert second.return_rate == pytest.approx(0.1)


def test_no_yield_and_a_loss_are_plain_numbers() -> None:
    flows = [(D(2026, 1, 10), 100_000)]
    flat, loss = profit_timeline(points((D(2026, 1, 31), 100_000), (D(2026, 2, 28), 97_000)), flows)
    assert (flat.profit_cents, flat.return_rate, flat.period_profit_cents) == (0, 0.0, None)
    assert (loss.profit_cents, loss.period_profit_cents) == (-3_000, -3_000)
    assert loss.return_rate == pytest.approx(-0.03)


def test_nothing_invested_has_no_percentage() -> None:
    (only,) = profit_timeline(points((D(2026, 1, 31), 500)), [])
    assert only.profit_cents == 500 and only.return_rate is None


def test_a_holding_starts_from_its_cost_basis_and_ignores_flows_inside_it() -> None:
    flows = [(D(2026, 1, 1), 100_000), (D(2026, 2, 10), 20_000)]  # the first IS the principal
    first, second = profit_timeline(
        points((D(2026, 1, 31), 101_000), (D(2026, 2, 28), 122_000)),
        flows,
        base_cents=100_000,
        base_date=D(2026, 1, 1),
    )
    assert (first.net_contributions_cents, first.profit_cents) == (100_000, 1_000)
    assert (second.net_contributions_cents, second.profit_cents) == (120_000, 2_000)


# --- over the ledger ---


@pytest.fixture
def broker(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    account = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, institution.id, "Corretora")
    )
    return SetInvestmentSettings(uow).execute(
        account.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.HOLDINGS
    )


@pytest.fixture
def cdb(uow: MemoryUnitOfWork, broker: Account, institution: Institution) -> InvestmentHolding:
    return RegisterHolding(uow).execute(
        RegisterHoldingCommand(
            account_id=broker.id,
            name="CDB Inter 100% CDI",
            instrument_type=InstrumentType.CDB,
            issuer_id=institution.id,
            applied_on=D(2026, 1, 15),
            principal_cents=100_000,
            liquidity=Liquidity.DAILY,
            indexer=Indexer.CDI,
            rate_mode=RateMode.PERCENT_OF_INDEX,
            rate_bps=10_000,
        )
    )


def snap(uow: MemoryUnitOfWork, holding: InvestmentHolding, day: dt.date, cents: int):
    return RecordHoldingSnapshot(uow).execute(RecordHoldingSnapshotCommand(holding.id, day, cents))


def series_of(uow: MemoryUnitOfWork, holding: InvestmentHolding):
    profit = GetInvestmentProfit(uow, FixedClock(TODAY)).execute()
    return next(s for s in profit.series if s.holding and s.holding.id == holding.id)


def test_snapshots_track_the_cumulative_profit_and_the_period_delta(
    uow: MemoryUnitOfWork, cdb: InvestmentHolding
) -> None:
    snap(uow, cdb, D(2026, 1, 15), 100_000)  # the cost basis
    snap(uow, cdb, D(2026, 2, 15), 101_050)
    snap(uow, cdb, D(2026, 3, 15), 102_200)
    series = series_of(uow, cdb)
    assert [r.point.profit_cents for r in series.rows] == [0, 1_050, 2_200]
    assert [r.point.period_profit_cents for r in series.rows] == [None, 1_050, 1_150]
    assert series.rows[-1].point.return_rate == pytest.approx(0.022)
    assert (series.current_value_cents, series.cost_cents, series.profit_cents) == (
        102_200, 100_000, 2_200
    )  # fmt: skip


def test_the_same_day_replaces_the_snapshot_in_place(
    uow: MemoryUnitOfWork, cdb: InvestmentHolding
) -> None:
    snap(uow, cdb, D(2026, 3, 15), 101_000)
    snap(uow, cdb, D(2026, 3, 15), 105_000)
    (row,) = series_of(uow, cdb).rows
    assert row.point.balance_cents == 105_000
    assert len(uow.anchors.list_for_holding(cdb.id)) == 1


def test_the_current_value_follows_the_latest_snapshot_even_when_one_is_added_in_the_past(
    uow: MemoryUnitOfWork, cdb: InvestmentHolding, broker: Account
) -> None:
    snap(uow, cdb, D(2026, 5, 31), 110_000)
    snap(uow, cdb, D(2026, 2, 28), 101_000)  # an older one filled in later
    (view,) = ListHoldings(uow, FixedClock(TODAY), 35).execute()
    assert view.current_value_cents == 110_000


def test_deleting_a_snapshot_removes_only_that_point(
    uow: MemoryUnitOfWork, cdb: InvestmentHolding
) -> None:
    snap(uow, cdb, D(2026, 2, 28), 101_000)
    snap(uow, cdb, D(2026, 3, 31), 102_000)
    wrong = series_of(uow, cdb).rows[0].anchor_id
    DeleteHoldingSnapshot(uow).execute(cdb.id, wrong)
    assert [r.point.on_date for r in series_of(uow, cdb).rows] == [D(2026, 3, 31)]
    with pytest.raises(DomainError) as exc:
        DeleteHoldingSnapshot(uow).execute(cdb.id, wrong)
    assert exc.value.code == "NOT_FOUND"


def test_a_negative_snapshot_is_refused(uow: MemoryUnitOfWork, cdb: InvestmentHolding) -> None:
    with pytest.raises(DomainError):
        snap(uow, cdb, D(2026, 3, 1), -1)


def test_an_account_tracked_as_a_whole_has_its_own_timeline_and_the_totals_add_up(
    uow: MemoryUnitOfWork, institution: Institution, checking: Account, savings: Account
) -> None:
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 1, 10), 100_000)
    )
    RecordBalance(uow).execute(
        RecordBalanceCommand(savings.id, D(2026, 1, 31), 100_500, gross_balance_cents=101_000)
    )
    profit = GetInvestmentProfit(uow, FixedClock(TODAY)).execute()
    (series,) = profit.series
    assert series.rows[0].point.balance_cents == 101_000  # the gross position wins
    assert (profit.value_cents, profit.cost_cents, profit.profit_cents) == (101_000, 100_000, 1_000)
    assert profit.return_rate == pytest.approx(0.01) and profit.pending == 0


def test_an_account_without_a_snapshot_is_pending_and_out_of_the_totals(
    uow: MemoryUnitOfWork, savings: Account
) -> None:
    profit = GetInvestmentProfit(uow, FixedClock(TODAY)).execute()
    assert profit.pending == 1 and profit.value_cents == 0 and profit.return_rate is None


# --- checking <-> investment transfers ---


def test_a_contribution_by_transfer_is_capital_not_income_or_expense(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    out, inc = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 1, 10), 100_000)
    )
    assert out.kind is inc.kind is TransactionKind.TRANSFER  # never income or expense
    RecordBalance(uow).execute(RecordBalanceCommand(savings.id, D(2026, 1, 31), 101_000))
    (series,) = GetInvestmentProfit(uow, FixedClock(TODAY)).execute().series
    assert series.cost_cents == 100_000 and series.profit_cents == 1_000


def test_a_redemption_by_transfer_lowers_the_cost_without_breaking_the_yield(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 1, 10), 100_000)
    )
    RecordBalance(uow).execute(RecordBalanceCommand(savings.id, D(2026, 1, 31), 105_000))
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(savings.id, checking.id, D(2026, 2, 10), 40_000)
    )
    RecordBalance(uow).execute(RecordBalanceCommand(savings.id, D(2026, 2, 28), 66_000))
    (series,) = GetInvestmentProfit(uow, FixedClock(TODAY)).execute().series
    assert series.cost_cents == 60_000 and series.profit_cents == 6_000
    assert series.return_rate == pytest.approx(0.1)


def test_a_transfer_into_a_note_links_the_leg_and_raises_its_cost_basis(
    uow: MemoryUnitOfWork, checking: Account, broker: Account, cdb: InvestmentHolding
) -> None:
    out, inc = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, broker.id, D(2026, 3, 1), 50_000, holding_id=cdb.id)
    )
    assert inc.holding_id == cdb.id and out.holding_id is None  # the investment leg carries it
    assert out.kind is TransactionKind.TRANSFER
    snap(uow, cdb, D(2026, 3, 31), 152_000)
    series = series_of(uow, cdb)
    assert series.cost_cents == 150_000  # principal + the top-up
    assert series.profit_cents == 2_000


def test_a_note_must_sit_on_one_side_of_the_transfer(
    uow: MemoryUnitOfWork, checking: Account, savings: Account, cdb: InvestmentHolding
) -> None:
    with pytest.raises(DomainError) as exc:
        RegisterTransfer(uow).execute(
            RegisterTransferCommand(
                checking.id, savings.id, D(2026, 3, 1), 50_000, holding_id=cdb.id
            )
        )
    assert exc.value.code == "HOLDING_NOT_IN_ACCOUNT"
    assert not uow.transactions.list_by_account(checking.id)  # nothing was written
