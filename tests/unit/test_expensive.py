"""The "Compras Mais Caras" ranking: what counts, in which order, and how plans and items appear."""

import dataclasses
import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.expensive import (
    GetMostExpensive,
    parse_top,
)
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
    SplitItem,
)
from financas.domain.models import Account, TransactionKind

D = dt.date
START, END = D(2026, 7, 1), D(2026, 7, 31)


def cat(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def spend(uow: MemoryUnitOfWork, account: Account, cents: int, day: int = 10, **kw: object):
    values: dict[str, object] = {
        "account_id": account.id,
        "posted_on": D(2026, 7, day),
        "kind": TransactionKind.EXPENSE,
        "amount_cents": cents,
        "description": f"Compra {cents}",
        "category_id": cat(uow, "shopping"),
    }
    values.update(kw)
    return RegisterTransaction(uow).execute(RegisterTransactionCommand(**values))  # type: ignore[arg-type]


def names(uow: MemoryUnitOfWork, top: int = 10) -> list[str]:
    return [r.description for r in GetMostExpensive(uow).execute(START, END, top)]


@pytest.mark.parametrize(
    ("text", "top"), [("5", 5), ("10", 10), ("7", 5), ("0", 5), ("x", 5), ("", 5)]
)
def test_top_is_five_or_ten(text: str, top: int) -> None:
    assert parse_top(text) == top


def test_order_is_amount_then_newest_date_then_highest_id(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    a = spend(uow, checking, 5_000, day=10, description="A")
    b = spend(uow, checking, 9_000, day=2, description="B")
    c = spend(uow, checking, 5_000, day=20, description="C")  # same amount, newer
    d = spend(uow, checking, 5_000, day=20, description="D")  # same amount and date: higher id wins
    first_of_the_tie = max((c, d), key=lambda t: t.id).description
    other = d.description if first_of_the_tie == c.description else c.description
    assert names(uow) == ["B", first_of_the_tie, other, "A"]
    assert a and b


def test_only_expenses_count_never_income_transfers_payments_or_refunded(
    uow: MemoryUnitOfWork, checking: Account, card: Account, savings: Account
) -> None:
    spend(uow, checking, 1_000, description="Normal")
    spend(uow, checking, 9_999_00, description="Estornada", is_refunded=True)  # type: ignore[arg-type]
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 7, 5), TransactionKind.INCOME, 500_000, "Salário"
        )
    )
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 6), 300_000, "Aporte")
    )
    bought = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            account_id=card.id, description="Fone", purchased_on=D(2026, 7, 8), total_cents=20_000
        )
    )
    statement_id = bought.transactions[0].statement_id
    assert statement_id
    PayStatement(uow, FixedClock(D(2026, 7, 28))).execute(
        PayStatementCommand(statement_id, checking.id, D(2026, 7, 28))
    )  # the payment is a transfer: neither leg is an expense
    assert names(uow) == ["Fone", "Normal"]


def test_an_installment_purchase_is_one_row_at_its_total(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            account_id=card.id,
            description="Monitor",
            purchased_on=D(2026, 7, 10),
            installments=3,
            total_cents=45_000,
        )
    )
    (row,) = GetMostExpensive(uow).execute(START, END)
    assert (row.kind, row.description, row.amount_cents, row.installments) == (
        "plan",
        "Monitor",
        45_000,
        3,
    )
    assert GetMostExpensive(uow).execute(D(2026, 8, 1), D(2026, 8, 31)) == []  # by purchase date


def test_an_itemized_expense_counts_once_at_its_full_amount(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    spend(
        uow,
        checking,
        18_000,
        category_id=None,
        description="Feira",
        splits=(
            SplitItem("Hortifruti", cat(uow, "groceries"), 11_000),
            SplitItem("Açougue", cat(uow, "food"), 7_000),
        ),
    )
    (row,) = GetMostExpensive(uow).execute(START, END)
    assert (row.amount_cents, row.has_items, row.category_id) == (18_000, True, None)


def test_fewer_rows_than_asked_are_not_padded_and_an_account_filter_applies(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    spend(uow, checking, 1_000, description="Banco")
    spend(uow, card, 2_000, description="Cartão")
    assert len(GetMostExpensive(uow).execute(START, END, 5)) == 2
    only = GetMostExpensive(uow).execute(START, END, 5, account_id=checking.id)
    assert [r.description for r in only] == ["Banco"]
    assert GetMostExpensive(uow).execute(D(2026, 6, 1), D(2026, 6, 30)) == []


def test_a_plan_and_an_entry_with_the_same_id_text_keep_their_identity() -> None:
    from financas.application.queries.expensive import ExpensiveRow, rank_expensive

    base = ExpensiveRow("entry", "7", "7", "e", None, D(2026, 7, 1), "a", "c", False, 100, None)
    plan = dataclasses.replace(base, kind="plan", description="p")
    ranked = rank_expensive([], [base, plan], {}, 10)
    assert {(r.kind, r.id) for r in ranked} == {("entry", "7"), ("plan", "7")}
