"""The payment-method filter of the entries list and the wording-based inference."""

import dataclasses
import datetime as dt

import pytest

from financas.application.queries.entries import (
    day_totals,
    filter_by_method,
    matches_method,
    parse_method_filter,
)
from financas.domain.models import AccountKind, PaymentMethod, Transaction, TransactionKind
from financas.domain.services.payment_methods import default_payment_method, infer_payment_method

PM = PaymentMethod


def entry(account: str, method: PaymentMethod | None, name: str = "x") -> Transaction:
    return Transaction(
        id=name,
        account_id=account,
        posted_on=dt.date(2026, 7, 1),
        kind=TransactionKind.EXPENSE,
        category_id="c",
        amount_cents=-100,
        description=name,
        description_search=name,
        payment_method=method,
    )


ROWS = [
    entry("bank", PM.PIX, "pix1"),
    entry("bank", PM.PIX, "pix2"),
    entry("bank2", PM.DEBIT, "deb"),
    entry("bank", PM.BOLETO, "bol"),
    entry("bank", None, "old"),  # not informed (an entry from before the column)
    entry("card", PM.CREDIT_CARD, "card1"),
    dataclasses.replace(entry("card", None, "card2")),  # a card entry always counts as a card
]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("", ["pix1", "pix2", "deb", "bol", "old", "card1", "card2"]),
        ("cartao_credito", ["card1", "card2"]),
        ("pix", ["pix1", "pix2"]),  # across every bank account
        ("debito", ["deb"]),
        ("boleto", ["bol"]),
        ("dinheiro", []),
    ],
)
def test_filter_by_method(method: str, expected: list[str]) -> None:
    assert [e.id for e in filter_by_method(ROWS, method, {"card"})] == expected


def test_a_blank_filter_matches_everything_and_a_bad_one_is_ignored() -> None:
    assert all(matches_method(e, "", {"card"}) for e in ROWS)
    assert parse_method_filter("") == "" and parse_method_filter("cheque") == ""
    assert parse_method_filter(" pix ") == "pix" and parse_method_filter("cartao_credito")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PIX TRANSF JOAO S", PM.PIX),
        ("Pix enviado - Padaria", PM.PIX),
        ("PAGTO ELETRON COBRANCA CONDOMINIO", PM.BOLETO),
        ("Pagamento de boleto - Luz", PM.BOLETO),
        ("COMPRA DEBITO MERCADO CENTRAL", PM.DEBIT),
        ("Compra no débito - Padaria", PM.DEBIT),
        ("TED recebida", PM.TED),
        ("DOC enviado", PM.TRANSFER),
        ("Salário", None),
        ("Supermercado", None),
    ],
)
def test_the_description_points_to_a_method(text: str, expected: PaymentMethod | None) -> None:
    assert infer_payment_method(text) is expected


def test_the_default_depends_on_the_account_and_falls_back_to_pix() -> None:
    assert default_payment_method(AccountKind.CREDIT_CARD, "PIX qualquer") is PM.CREDIT_CARD
    assert default_payment_method(AccountKind.CHECKING, "COMPRA DEBITO X") is PM.DEBIT
    assert default_payment_method(AccountKind.CHECKING, "Salário") is PM.PIX  # the fallback
    assert default_payment_method(AccountKind.INVESTMENT, "Aporte") is None


# --- the day's totals and net balance ("Saldo do dia") ---

_DAY = dt.date(2026, 7, 10)


def _entry(kind: TransactionKind, cents: int, **kw: object) -> Transaction:
    return Transaction(
        id=f"t{abs(hash((kind, cents, tuple(kw.items()))))}{len(kw)}",
        account_id=str(kw.pop("account_id", "a1")),
        posted_on=dt.date.fromisoformat(str(kw.pop("day", _DAY))),
        kind=kind,
        category_id="c",
        amount_cents=cents,
        description="x",
        description_search="x",
        **kw,  # type: ignore[arg-type]
    )


def test_a_day_with_only_expenses_is_negative() -> None:
    totals = day_totals(
        [
            _entry(TransactionKind.EXPENSE, -12_000),
            _entry(TransactionKind.EXPENSE, -30_000, notes="b"),
        ]
    )
    day = totals[_DAY]
    assert (day.income_cents, day.expense_cents, day.net_cents) == (0, 42_000, -42_000)
    assert day.has_spending


def test_income_and_expense_make_the_net_of_the_day() -> None:
    totals = day_totals(
        [_entry(TransactionKind.INCOME, 300_000), _entry(TransactionKind.EXPENSE, -120_000)]
    )
    day = totals[_DAY]
    assert (day.income_cents, day.expense_cents, day.net_cents) == (300_000, 120_000, 180_000)


def test_a_day_with_only_income_has_no_spending() -> None:
    day = day_totals([_entry(TransactionKind.INCOME, 5_000)])[_DAY]
    assert (day.net_cents, day.has_spending) == (5_000, False)


def test_refunds_reduce_the_spending_and_refunded_purchases_count_nowhere() -> None:
    day = day_totals(
        [
            _entry(TransactionKind.EXPENSE, -10_000),
            _entry(TransactionKind.REFUND, 2_500, notes="r"),
            _entry(TransactionKind.EXPENSE, -99_999, is_refunded=True),
        ]
    )[_DAY]
    assert (day.expense_cents, day.net_cents) == (7_500, -7_500)


def test_transfers_stay_out_of_the_net_unless_the_list_is_one_account() -> None:
    legs = [
        _entry(TransactionKind.EXPENSE, -10_000),
        _entry(TransactionKind.TRANSFER, -50_000, notes="out"),
        _entry(TransactionKind.TRANSFER, 20_000, notes="in", account_id="a1"),
    ]
    mixed = day_totals(legs)[_DAY]
    assert (mixed.income_cents, mixed.expense_cents, mixed.net_cents) == (0, 10_000, -10_000)
    one_account = day_totals(legs, include_transfers=True)[_DAY]
    assert one_account.net_cents == -10_000 - 50_000 + 20_000  # a transfer moves that account
    assert (one_account.income_cents, one_account.expense_cents) == (0, 10_000)  # still not income


def test_every_day_is_summed_on_its_own_and_money_stays_an_int() -> None:
    totals = day_totals(
        [
            _entry(TransactionKind.INCOME, 100, day="2026-07-09"),
            _entry(TransactionKind.EXPENSE, -300, day="2026-07-10"),
        ]
    )
    assert {d.isoformat(): t.net_cents for d, t in totals.items()} == {
        "2026-07-09": 100,
        "2026-07-10": -300,
    }
    assert all(isinstance(t.net_cents, int) for t in totals.values())
