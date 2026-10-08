"""The account balance at the end of each day of the entries list ("Saldo da conta")."""

import datetime as dt

import pytest

from fakes import MemoryUnitOfWork
from financas.application.queries.day_balances import GetDayBalances
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.catalog import CreateAccount, CreateAccountCommand
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.models import Account, AccountKind, Institution, TransactionKind

D = dt.date
K = TransactionKind


@pytest.fixture
def other(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Segunda")
    )


def start(uow: MemoryUnitOfWork, account: Account, cents: int = 100_000) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(account.id, D(2026, 7, 1), cents))


def spend(uow: MemoryUnitOfWork, account: Account, day: int, cents: int, **kw: object) -> None:
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            account.id,
            D(2026, 7, day),
            K.EXPENSE,
            cents,
            "x",
            **kw,  # type: ignore[arg-type]
        )
    )


def balances(uow: MemoryUnitOfWork, days: list[int], account: Account | None = None):
    result = GetDayBalances(uow).execute(
        [D(2026, 7, d) for d in days], account.id if account else None
    )
    return {d.day: b.balance_cents for d, b in result.items()}


def test_an_expense_lowers_the_balance_at_the_end_of_its_day(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    start(uow, checking, 100_000)  # R$ 1.000,00
    spend(uow, checking, 10, 20_000)
    assert balances(uow, [10], checking) == {10: 80_000}  # R$ 800,00


def test_the_balance_rolls_forward_across_consecutive_days(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    start(uow, checking, 100_000)
    spend(uow, checking, 10, 20_000)
    spend(uow, checking, 11, 5_000)
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(checking.id, D(2026, 7, 12), K.INCOME, 30_000, "pix")
    )
    assert balances(uow, [9, 10, 11, 12], checking) == {
        9: 100_000,
        10: 80_000,
        11: 75_000,
        12: 105_000,
    }


def test_an_internal_transfer_moves_both_accounts_but_not_the_total(
    uow: MemoryUnitOfWork, checking: Account, other: Account
) -> None:
    start(uow, checking, 100_000)
    start(uow, other, 10_000)
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, other.id, D(2026, 7, 10), 50_000)
    )
    assert balances(uow, [10], checking) == {10: 50_000}
    assert balances(uow, [10], other) == {10: 60_000}
    assert balances(uow, [10]) == {10: 110_000}  # all checking accounts: nothing left the house


def test_a_neutral_expense_still_moves_the_bank_balance(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    from financas.application.use_cases.catalog import CreateCategory, CreateCategoryCommand
    from financas.domain.models import CategoryGroup, CategoryKind

    third = CreateCategory(uow).execute(
        CreateCategoryCommand(
            "Reembolso / Terceiros",
            CategoryGroup.NON_ESSENTIAL,
            CategoryKind.EXPENSE,
            is_neutral=True,
        )
    )
    start(uow, checking, 100_000)
    spend(uow, checking, 10, 25_000, category_id=third.id)
    assert balances(uow, [10], checking) == {10: 75_000}  # reconciles with the bank, cent by cent


def test_no_informed_balance_is_unavailable_never_zero(
    uow: MemoryUnitOfWork, checking: Account, other: Account
) -> None:
    spend(uow, checking, 10, 1_000)
    result = GetDayBalances(uow).execute([D(2026, 7, 10)])
    assert result[D(2026, 7, 10)].balance_cents is None
    start(uow, other, 10_000)
    partial = GetDayBalances(uow).execute([D(2026, 7, 10)])[D(2026, 7, 10)]
    assert partial.balance_cents == 10_000 and partial.partial  # the other account has none


def test_a_card_or_investment_list_has_no_balance(uow: MemoryUnitOfWork, savings: Account) -> None:
    assert GetDayBalances(uow).execute([D(2026, 7, 10)], savings.id) == {}
