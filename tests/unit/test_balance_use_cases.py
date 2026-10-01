import datetime as dt

from fakes import MemoryUnitOfWork
from financas.application.queries.balances import ListAccountBalances
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.models import Account, TransactionKind

D = dt.date


def spend(uow: MemoryUnitOfWork, account: Account, day: dt.date, cents: int) -> None:
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(account.id, day, TransactionKind.EXPENSE, cents, "gasto")
    )


def test_first_balance_has_nothing_to_compare(uow: MemoryUnitOfWork, checking: Account) -> None:
    result = RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 7, 1), 100_000))
    assert result.computed_cents is None and result.difference_cents is None
    assert uow.anchors.list_for_account(checking.id)[0].balance_cents == 100_000


def test_difference_points_to_missing_entries(uow: MemoryUnitOfWork, checking: Account) -> None:
    use_case = RecordBalance(uow)
    use_case.execute(RecordBalanceCommand(checking.id, D(2026, 7, 1), 100_000))
    spend(uow, checking, D(2026, 7, 5), 30_000)
    result = use_case.execute(RecordBalanceCommand(checking.id, D(2026, 7, 10), 65_000, "app"))
    assert result.computed_cents == 70_000
    assert result.difference_cents == -5_000  # 50 reais of spending were never entered
    assert result.anchor.note == "app"


def test_same_day_replaces_the_anchor_and_ignores_itself(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    use_case = RecordBalance(uow)
    use_case.execute(RecordBalanceCommand(checking.id, D(2026, 7, 1), 100_000))
    use_case.execute(RecordBalanceCommand(checking.id, D(2026, 7, 10), 90_000))
    again = use_case.execute(RecordBalanceCommand(checking.id, D(2026, 7, 10), 95_000))
    assert again.computed_cents == 100_000
    anchors = uow.anchors.list_for_account(checking.id)
    assert [a.balance_cents for a in anchors] == [100_000, 95_000]


def test_negative_balance_is_valid(uow: MemoryUnitOfWork, checking: Account) -> None:
    result = RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 7, 1), -2_500))
    assert result.anchor.balance_cents == -2_500


def test_investment_valuation_shows_yield_as_the_difference(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    use_case = RecordBalance(uow)
    use_case.execute(RecordBalanceCommand(savings.id, D(2026, 7, 1), 1_000_000))
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 10), 100_000)
    )
    result = use_case.execute(RecordBalanceCommand(savings.id, D(2026, 7, 31), 1_112_000))
    assert result.computed_cents == 1_100_000
    assert result.difference_cents == 12_000


def test_list_balances_marks_missing_anchor_as_unavailable(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 7, 1), 100_000))
    spend(uow, checking, D(2026, 7, 5), 25_000)
    rows = {r.account_id: r for r in ListAccountBalances(uow).execute(D(2026, 7, 20))}
    assert rows[checking.id].balance_cents == 75_000
    assert rows[checking.id].last_anchor_date == D(2026, 7, 1)
    assert rows[savings.id].balance_cents is None
    assert rows[savings.id].last_anchor_date is None
