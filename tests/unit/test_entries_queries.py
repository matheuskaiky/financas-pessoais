"""The payment-method filter of the entries list and the wording-based inference."""

import dataclasses
import datetime as dt

import pytest

from financas.application.queries.entries import (
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
        ("TED recebida", PM.TRANSFER),
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
