import datetime as dt

import pytest

from fakes import MemoryUnitOfWork
from financas.application.use_cases.catalog import SetAccountActive
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
    SuggestCategory,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, TransactionKind

K = TransactionKind
D = dt.date


def register(uow: MemoryUnitOfWork, account: Account, **overrides: object):
    values: dict[str, object] = {
        "account_id": account.id,
        "posted_on": D(2026, 7, 10),
        "kind": K.EXPENSE,
        "amount_cents": 12_345,
        "description": "Padaria São João",
    }
    values.update(overrides)
    return RegisterTransaction(uow).execute(RegisterTransactionCommand(**values))  # type: ignore[arg-type]


def category_id(uow: MemoryUnitOfWork, slug: str) -> str:
    category = uow.categories.get_by_slug(slug)
    assert category
    return category.id


def test_expense_is_stored_negative_with_search_key(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    t = register(uow, checking, category_id=category_id(uow, "food"))
    assert t.amount_cents == -12_345
    assert t.kind is K.EXPENSE
    assert t.description == "Padaria São João"
    assert t.description_search == "padaria sao joao"
    assert uow.transactions.get(t.id) == t


def test_income_and_refund_are_stored_positive(uow: MemoryUnitOfWork, checking: Account) -> None:
    income = register(uow, checking, kind=K.INCOME, description="Salário")
    refund = register(uow, checking, kind=K.REFUND, description="Estorno loja")
    assert income.amount_cents == 12_345 and refund.amount_cents == 12_345


@pytest.mark.parametrize(
    ("kind", "slug"),
    [
        (K.EXPENSE, "uncategorized"),
        (K.INCOME, "other_income"),
        (K.REFUND, "refund"),
    ],
)
def test_default_category_by_kind(
    uow: MemoryUnitOfWork, checking: Account, kind: TransactionKind, slug: str
) -> None:
    t = register(uow, checking, kind=kind)
    assert t.category_id == category_id(uow, slug)


def test_category_must_match_kind(uow: MemoryUnitOfWork, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, kind=K.INCOME, category_id=category_id(uow, "food"))
    assert exc.value.code == "CATEGORY_KIND_MISMATCH"
    assert uow.transactions.items == {}


@pytest.mark.parametrize("amount", [0, -5])
def test_amount_must_be_positive(uow: MemoryUnitOfWork, checking: Account, amount: int) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, amount_cents=amount)
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"


def test_description_is_required(uow: MemoryUnitOfWork, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, description="   ")
    assert exc.value.code == "EMPTY_DESCRIPTION"


def test_transfers_do_not_go_through_register_transaction(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, kind=K.TRANSFER)
    assert exc.value.code == "USE_TRANSFER_FOR_TRANSFERS"


def test_unknown_account_inactive_account_and_card_are_rejected(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        RegisterTransaction(uow).execute(
            RegisterTransactionCommand("nope", D(2026, 7, 1), K.EXPENSE, 100, "x")
        )
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(DomainError) as exc:
        register(uow, card)
    assert exc.value.code == "ACCOUNT_KIND_NOT_ALLOWED"
    SetAccountActive(uow).execute(checking.id, False)
    with pytest.raises(DomainError) as exc:
        register(uow, checking)
    assert exc.value.code == "ACCOUNT_INACTIVE"


def test_transfer_between_tracked_accounts_creates_two_opposite_legs(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 5), 50_000, "Aporte")
    )
    assert len(legs) == 2
    out, into = legs
    assert (out.account_id, out.amount_cents) == (checking.id, -50_000)
    assert (into.account_id, into.amount_cents) == (savings.id, 50_000)
    assert out.transfer_id and out.transfer_id == into.transfer_id
    assert {leg.kind for leg in legs} == {K.TRANSFER}
    assert out.category_id == category_id(uow, "transfer")


@pytest.mark.parametrize(("side", "sign"), [("from", -1), ("to", 1)])
def test_transfer_to_or_from_an_untracked_account_has_one_leg(
    uow: MemoryUnitOfWork, checking: Account, side: str, sign: int
) -> None:
    cmd = RegisterTransferCommand(
        checking.id if side == "from" else None,
        checking.id if side == "to" else None,
        D(2026, 7, 5),
        1_000,
    )
    legs = RegisterTransfer(uow).execute(cmd)
    assert len(legs) == 1 and legs[0].amount_cents == sign * 1_000


def test_transfer_rules(uow: MemoryUnitOfWork, checking: Account, card: Account) -> None:
    use_case = RegisterTransfer(uow)
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(None, None, D(2026, 7, 5), 100))
    assert exc.value.code == "TRANSFER_NEEDS_ACCOUNT"
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(checking.id, checking.id, D(2026, 7, 5), 100))
    assert exc.value.code == "TRANSFER_SAME_ACCOUNT"
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(checking.id, None, D(2026, 7, 5), 0))
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(checking.id, card.id, D(2026, 7, 5), 100))
    assert exc.value.code == "ACCOUNT_KIND_NOT_ALLOWED"


def test_a_failing_second_leg_leaves_no_first_leg(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    with pytest.raises(DomainError):
        RegisterTransfer(uow).execute(
            RegisterTransferCommand(checking.id, card.id, D(2026, 7, 5), 100)
        )
    assert uow.transactions.items == {}


def test_deleting_one_transfer_leg_deletes_both(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 5), 100)
    )
    assert DeleteTransaction(uow).execute(legs[0].id) == 2
    assert uow.transactions.items == {}


def test_deleting_a_plain_entry_and_unknown_id(uow: MemoryUnitOfWork, checking: Account) -> None:
    t = register(uow, checking)
    assert DeleteTransaction(uow).execute(t.id) == 1
    with pytest.raises(DomainError) as exc:
        DeleteTransaction(uow).execute(t.id)
    assert exc.value.code == "NOT_FOUND"


def test_suggests_the_last_category_for_the_same_description(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    register(uow, checking, posted_on=D(2026, 6, 1), category_id=category_id(uow, "food"))
    register(uow, checking, posted_on=D(2026, 7, 1), category_id=category_id(uow, "groceries"))
    suggest = SuggestCategory(uow)
    # same normalized description, different case and accents
    result = suggest.execute("PADARIA SAO JOAO", K.EXPENSE)
    assert result and result.slug == "groceries"
    assert suggest.execute("Outra coisa", K.EXPENSE) is None
    assert suggest.execute("Padaria São João", K.INCOME) is None
    assert suggest.execute("   ", K.EXPENSE) is None
