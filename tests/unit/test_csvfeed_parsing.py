"""Normalisation tables of the CSV feed: amounts, dates, booleans, numbers, headers."""

import contextlib
import datetime as dt
import random
import string

import pytest

from financas.application.csvfeed.model import AmountType, FeedColumn, FeedKind
from financas.application.csvfeed.parsing import (
    normalize_header,
    parse_amount,
    parse_amount_type,
    parse_bool,
    parse_date,
    parse_int,
    parse_kind,
    parse_statement_month,
    resolve_header,
)
from financas.domain.errors import DomainError
from financas.domain.money import YearMonth

TODAY = dt.date(2026, 10, 2)


@pytest.mark.parametrize(
    ("text", "cents", "sign"),
    [
        ("1234.56", 123456, None),
        ("1234,56", 123456, None),
        ("1.234,56", 123456, None),
        ("1,234.56", 123456, None),
        ("R$ 1.234,56", 123456, None),
        ("R$1.234,56", 123456, None),
        ("r$ 10", 1000, None),
        ("-R$ 1.234,56", 123456, "-"),
        ("R$ -5,5", 550, "-"),
        ("− 5,00", 500, "-"),  # the real minus sign the app prints
        ("+10", 1000, "+"),
        ("1234", 123400, None),
        ("0,5", 50, None),
        ("0", 0, None),
        ("1.234,5", 123450, None),
        ("1.234.567", 123456700, None),  # repeated separator: thousands only
        ("1,234,567.89", 123456789, None),
        ("12345,67", 1234567, None),
        ("12345.6", 1234560, None),
        ("R$ 1.234,56", 123456, None),  # a non-breaking space from a spreadsheet
        ("  7,00  ", 700, None),
        ("999999999,99", 99999999999, None),
    ],
)
def test_amounts_accepted(text: str, cents: int, sign: str | None) -> None:
    amount = parse_amount(text)
    assert (amount.magnitude_cents, amount.sign) == (cents, sign)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("1.234", "AMOUNT_AMBIGUOUS"),
        ("1,234", "AMOUNT_AMBIGUOUS"),
        ("12.345", "AMOUNT_AMBIGUOUS"),
        ("1.234,567", "AMOUNT_TOO_MANY_DECIMALS"),
        ("1,234.567", "AMOUNT_TOO_MANY_DECIMALS"),
        ("1234.567", "AMOUNT_TOO_MANY_DECIMALS"),
        ("12.3456", "AMOUNT_TOO_MANY_DECIMALS"),
        ("10000000000,00", "AMOUNT_TOO_LARGE"),
        ("", "INVALID_AMOUNT"),
        ("abc", "INVALID_AMOUNT"),
        (",5", "INVALID_AMOUNT"),
        ("5,", "INVALID_AMOUNT"),
        ("1.2.3", "INVALID_AMOUNT"),
        ("1,23.45", "INVALID_AMOUNT"),
        ("12 34", "INVALID_AMOUNT"),
        ("(5,00)", "INVALID_AMOUNT"),
        ("--5", "INVALID_AMOUNT"),
        ("-5-", "INVALID_AMOUNT"),
        ("5%", "INVALID_AMOUNT"),
        ("1e3", "INVALID_AMOUNT"),
        ("٣", "INVALID_AMOUNT"),  # Arabic-Indic digit: ASCII digits only
        ("NaN", "INVALID_AMOUNT"),
        ("R$", "INVALID_AMOUNT"),
    ],
)
def test_amounts_rejected(text: str, code: str) -> None:
    with pytest.raises(DomainError) as error:
        parse_amount(text)
    assert error.value.code == code


def test_garbage_never_crashes_the_amount_parser() -> None:
    rng = random.Random(7)
    alphabet = string.digits * 3 + ".,+-R$  −()eE%x\t٣"
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        try:
            amount = parse_amount(text)
        except DomainError:
            continue
        assert 0 <= amount.magnitude_cents < 10**12


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-30", dt.date(2026, 9, 30)),
        ("30/09/2026", dt.date(2026, 9, 30)),
        ("5/9/2026", dt.date(2026, 9, 5)),
        ("05/09/2026", dt.date(2026, 9, 5)),
        ("29/02/2024", dt.date(2024, 2, 29)),
        (" 2026-10-02 ", TODAY),
        ("2027-10-03", dt.date(2027, 10, 3)),  # 366 days ahead is the limit
    ],
)
def test_dates_accepted(text: str, expected: dt.date) -> None:
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("30/09/26", "INVALID_DATE"),
        ("2026/09/30", "INVALID_DATE"),
        ("30-09-2026", "INVALID_DATE"),
        ("2026-9-30", "INVALID_DATE"),
        ("31/02/2026", "INVALID_DATE"),
        ("29/02/2027", "INVALID_DATE"),
        ("2026-13-01", "INVALID_DATE"),
        ("", "INVALID_DATE"),
        ("hoje", "INVALID_DATE"),
        ("٣٠/٠٩/٢٠٢٦", "INVALID_DATE"),
        ("2027-10-04", "DATE_TOO_FAR"),
        ("1899-12-31", "DATE_TOO_OLD"),
    ],
)
def test_dates_rejected(text: str, code: str) -> None:
    with pytest.raises(DomainError) as error:
        parse_date(text, TODAY)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", True), ("YES", True), ("true", True), ("1", True), ("sim", True),
        ("Sim", True), ("s", True), ("x", True), ("X", True),
        ("no", False), ("false", False), ("0", False), ("não", False), ("nao", False),
        ("NÃO", False), ("n", False), ("", False), ("  ", False),
    ],
)  # fmt: skip
def test_booleans(text: str, expected: bool) -> None:
    assert parse_bool(text) is expected


@pytest.mark.parametrize("text", ["maybe", "2", "talvez", "yes!", "ss"])
def test_booleans_rejected(text: str) -> None:
    with pytest.raises(DomainError) as error:
        parse_bool(text)
    assert error.value.code == "INVALID_BOOLEAN"


@pytest.mark.parametrize(
    ("text", "column"),
    [
        ("date", FeedColumn.DATE),
        ("Data", FeedColumn.DATE),
        (" DATA ", FeedColumn.DATE),
        ("Tipo", FeedColumn.KIND),
        ("Conta", FeedColumn.ACCOUNT),
        ("Conta Destino", FeedColumn.TO_ACCOUNT),
        ("conta-destino", FeedColumn.TO_ACCOUNT),
        ("to_account", FeedColumn.TO_ACCOUNT),
        ("Valor", FeedColumn.AMOUNT),
        ("Descrição", FeedColumn.DESCRIPTION),
        ("DESCRICAO", FeedColumn.DESCRIPTION),
        ("Categoria", FeedColumn.CATEGORY),
        ("Recorrente", FeedColumn.RECURRING),
        ("Observações", FeedColumn.NOTES),
        ("Fatura", FeedColumn.STATEMENT),
        ("Parcelas", FeedColumn.INSTALLMENTS),
        ("Parcela atual", FeedColumn.INSTALLMENT_NUMBER),
        ("Tipo Valor", FeedColumn.AMOUNT_TYPE),
        ("Valor Bruto", FeedColumn.GROSS_AMOUNT),
    ],
)
def test_headers(text: str, column: FeedColumn) -> None:
    assert resolve_header(text) is column


def test_unknown_header_and_normalisation() -> None:
    assert resolve_header("descripcion") is None
    assert resolve_header("valro") is None
    assert normalize_header(" Conta  Destino ") == "conta_destino"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("expense", FeedKind.EXPENSE), ("Despesa", FeedKind.EXPENSE),
        ("income", FeedKind.INCOME), ("RECEITA", FeedKind.INCOME),
        ("refund", FeedKind.REFUND), ("Estorno", FeedKind.REFUND),
        ("transfer", FeedKind.TRANSFER), ("transferencia", FeedKind.TRANSFER),
        ("Transferência", FeedKind.TRANSFER),
        ("balance", FeedKind.BALANCE), ("Saldo", FeedKind.BALANCE),
    ],
)  # fmt: skip
def test_kinds(text: str, kind: FeedKind) -> None:
    assert parse_kind(text) is kind


def test_kind_and_amount_type_rejected() -> None:
    for action in (lambda: parse_kind("gasto"), lambda: parse_amount_type("tudo")):
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.code == "INVALID_CHOICE"
    assert parse_amount_type("Total") is AmountType.TOTAL
    assert parse_amount_type("parcela") is AmountType.INSTALLMENT


def test_integers_and_months() -> None:
    assert parse_int("12") == 12
    for bad in ("", "-1", "1.0", "1,5", "x", "٣", "1234567"):
        with pytest.raises(DomainError):
            parse_int(bad)
    assert parse_statement_month("2026-09") == YearMonth(2026, 9)
    for bad in ("2026-9", "09/2026", "2026-13", "1800-01", "2026-09-01", "set/2026"):
        with pytest.raises(DomainError) as error:
            parse_statement_month(bad)
        assert error.value.code == "INVALID_YEAR_MONTH"


def test_garbage_never_crashes_the_other_parsers() -> None:
    rng = random.Random(11)
    alphabet = string.printable + "ãéçÃ٣ ﻿"
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        for parser in (
            lambda t: parse_date(t, TODAY),
            parse_bool,
            parse_int,
            parse_kind,
            parse_amount_type,
            parse_statement_month,
        ):
            with contextlib.suppress(DomainError):
                parser(text)
