"""Smart suggestions: frequency weighted by recency, habitual amounts, valid methods per flow."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.suggestions import (
    WINDOW_DAYS,
    ListSuggestions,
    habitual_amount,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.models import Account, PaymentMethod, Transaction, TransactionKind

D = dt.date
TODAY = D(2026, 7, 25)
CLOCK = FixedClock(TODAY)
PM = PaymentMethod


def cat(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def add(
    uow: MemoryUnitOfWork,
    account: Account,
    description: str,
    cents: int,
    days_ago: int = 1,
    kind: TransactionKind = TransactionKind.EXPENSE,
    slug: str = "food",
    **kw: object,
) -> Transaction:
    return RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            account.id,
            TODAY - dt.timedelta(days=days_ago),
            kind,
            cents,
            description,
            cat(uow, slug),
            **kw,  # type: ignore[arg-type]
        )
    )


def suggest(uow: MemoryUnitOfWork, flow: str = "expense", q: str = "", limit: int = 8):
    return ListSuggestions(uow, CLOCK).execute(flow, q, limit)


def test_a_frequent_lunch_is_the_top_suggestion_with_its_habitual_amount(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    for n in range(5):
        add(
            uow,
            checking,
            "Almoço Restaurante Central",
            3_500,
            days_ago=2 + n * 3,
            payment_method=PM.DEBIT,
        )
    add(uow, checking, "Cinema", 4_000, days_ago=4)
    top = suggest(uow)[0]
    assert top.description == "Almoço Restaurante Central" and top.count == 5
    assert (top.habitual_amount_cents, top.payment_method) == (3_500, PM.DEBIT)
    assert (top.category_id, top.account_id) == (cat(uow, "food"), checking.id)


def test_recency_beats_old_frequency(uow: MemoryUnitOfWork, checking: Account) -> None:
    for n in range(8):  # eight times, four to five months ago
        add(uow, checking, "Academia antiga", 9_000, days_ago=120 + n * 3)
    for n in range(3):  # three times in the last two weeks
        add(uow, checking, "Padaria", 1_800, days_ago=1 + n * 4)
    assert [s.description for s in suggest(uow)][:2] == ["Padaria", "Academia antiga"]


def test_nothing_older_than_the_window_nor_transfers_installments_or_refunded(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    add(uow, checking, "Velho", 1_000, days_ago=WINDOW_DAYS + 5)
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, TODAY, 5_000, "Aporte")
    )
    estornada = add(uow, checking, "Estornada", 2_000)
    uow.transactions.update(
        Transaction(**{**vars(estornada), "is_refunded": True})  # type: ignore[arg-type]
    )
    add(uow, checking, "Atual", 500)
    assert [s.description for s in suggest(uow)] == ["Atual"]


def test_spelling_case_and_accents_are_one_habit_and_the_latest_spelling_shows(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    add(uow, checking, "cafe da manha", 800, days_ago=9)
    add(uow, checking, "Café da Manhã", 800, days_ago=1)
    (only,) = suggest(uow)
    assert only.description == "Café da Manhã" and only.count == 2


def test_the_same_description_on_another_account_or_method_is_another_suggestion(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    add(uow, checking, "Uber", 2_000, payment_method=PM.PIX)
    add(uow, card, "Uber", 2_000)
    found = suggest(uow, q="uber")
    assert {(s.account_id, s.payment_method) for s in found} == {
        (checking.id, PM.PIX),
        (card.id, PM.CREDIT_CARD),
    }


@pytest.mark.parametrize(
    ("amounts", "expected"),
    [
        ([3_500] * 5, 3_500),  # zero variance
        ([5_000_00], 5_000_00),  # a single entry: its own amount
        ([3_500, 3_500, 3_500, 4_100], 3_500),  # a clear majority of 4 (75 %)
        ([3_500, 3_500, 4_100, 5_000], None),  # no majority
        ([3_500, 4_100], None),  # fewer than 3 and different
        ([], None),
    ],
)
def test_the_habitual_amount(amounts: list[int], expected: int | None) -> None:
    assert habitual_amount(amounts) == expected


def test_income_suggestions_never_carry_debit_or_boleto(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    for n in range(3):
        add(
            uow,
            checking,
            "Salário",
            500_000,
            days_ago=3 + 30 * n,
            kind=TransactionKind.INCOME,
            slug="salary",
            payment_method=PM.TED,
        )
    add(
        uow,
        checking,
        "Dividendos",
        12_000,
        kind=TransactionKind.INCOME,
        slug="investment_income",
        payment_method=PM.PIX,
    )
    income = suggest(uow, "income")
    assert next(s.description for s in income) == "Salário"
    assert {s.payment_method for s in income} <= {PM.PIX, PM.TED, PM.OTHER, None}
    assert income[0].habitual_amount_cents == 500_000
    assert suggest(uow, "expense") == []  # the flows do not mix


def test_a_legacy_income_with_a_debit_method_is_proposed_without_it(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    legacy = add(uow, checking, "Venda", 3_000, kind=TransactionKind.INCOME, slug="other_income")
    uow.transactions.update(
        Transaction(**{**vars(legacy), "payment_method": PM.DEBIT})  # type: ignore[arg-type]
    )
    (only,) = suggest(uow, "income")
    assert only.payment_method is None


def test_search_is_accent_and_case_insensitive_and_the_limit_and_flow_are_safe(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    for name in ("Supermercado São João", "Farmácia", "Feira"):
        add(uow, checking, name, 1_000)
    assert [s.description for s in suggest(uow, q="SAO joao")] == ["Supermercado São João"]
    assert [s.description for s in suggest(uow, q="farmacia")] == ["Farmácia"]
    assert len(suggest(uow, limit=2)) == 2 and len(suggest(uow, limit=0)) == 1
    assert suggest(uow, "nonsense") == []
