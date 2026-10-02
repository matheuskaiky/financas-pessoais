"""The workbook reader: normalisation into codes and cents, source dialect, no derived sheets."""

import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from financas.application.imports.model import Origin, RowKind, TransferHint
from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.infrastructure.spreadsheet import read_legacy_workbook
from legacy_workbook import D, build_workbook, default_rows, fact


@pytest.fixture
def workbook(tmp_path: Path):
    return read_legacy_workbook(build_workbook(tmp_path / "w.xlsx"))


def by_id(workbook: Any, sheet_id: str) -> Any:
    return next(r for r in workbook.rows if r.sheet_id == sheet_id)


def test_rows_skip_empty_lines_and_keep_excel_row_numbers(workbook: Any) -> None:
    assert len(workbook.rows) == 11  # the empty row is skipped
    assert by_id(workbook, "r01").row_no == 2  # header is row 1
    assert by_id(workbook, "r11").row_no == 12


def test_derived_sheets_are_never_read(tmp_path: Path) -> None:
    # the derived sheet holds text where numbers would be; reading it would fail
    loaded = read_legacy_workbook(build_workbook(tmp_path / "w.xlsx"))
    assert len(loaded.rows) == 11 and len(loaded.accounts) == 2
    assert loaded.categories == ["Alimentação", "Educacao", "Salario"]


def test_float_noise_is_accepted_and_the_sign_is_kept(workbook: Any) -> None:
    assert by_id(workbook, "r01").amount_cents == -5990
    assert by_id(workbook, "r08").amount_cents == 400_000
    assert by_id(workbook, "r09").amount_cents == 2550


@pytest.mark.parametrize("value", [59.905, -0.001, 10.123])
def test_amounts_that_are_not_cents_are_rejected(tmp_path: Path, value: float) -> None:
    rows = [
        fact("x", D(2026, 1, 1), "Banco Alfa", "Conta Corrente", "Outros", "Despesa", "x", value)
    ]
    with pytest.raises(DomainError) as exc:
        read_legacy_workbook(build_workbook(tmp_path / "w.xlsx", rows))
    assert exc.value.code == "IMPORT_AMOUNT_NOT_CENTS" and exc.value.params["row"] == 2


def test_origin_kind_recurring_and_columns(workbook: Any) -> None:
    padaria = by_id(workbook, "r01")
    assert (padaria.origin, padaria.kind, padaria.recurring) is not None
    assert padaria.origin is Origin.CHECKING and padaria.kind is RowKind.EXPENSE
    assert padaria.recurring is True and padaria.place == "TERESINA BR"
    assert padaria.date.isoformat() == "2026-01-03" and padaria.statement is None
    curso = by_id(workbook, "r02")  # unaccented 'Cartao de Credito'
    assert curso.origin is Origin.CARD and curso.statement == YearMonth(2026, 2)
    assert (curso.installment_number, curso.installment_total) == (2, 6)
    assert by_id(workbook, "r06").origin is Origin.CARD  # accented 'Cartão de Crédito'
    assert by_id(workbook, "r09").kind is RowKind.REFUND
    assert by_id(workbook, "r10").kind is RowKind.TRANSFER  # accented 'Transferência'
    assert by_id(workbook, "r02").place is None and by_id(workbook, "r02").recurring is False


def test_unknown_origin_and_kind_are_errors(tmp_path: Path) -> None:
    bad_origin = [fact("x", D(2026, 1, 1), "B", "Poupança", "Outros", "Despesa", "x", -1.0)]
    with pytest.raises(DomainError) as exc:
        read_legacy_workbook(build_workbook(tmp_path / "a.xlsx", bad_origin))
    assert exc.value.code == "IMPORT_UNKNOWN_ORIGIN" and exc.value.params["row"] == 2
    bad_kind = [fact("x", D(2026, 1, 1), "B", "Conta Corrente", "Outros", "Doação", "x", -1.0)]
    with pytest.raises(DomainError) as exc:
        read_legacy_workbook(build_workbook(tmp_path / "b.xlsx", bad_kind))
    assert exc.value.code == "IMPORT_UNKNOWN_KIND"


def test_transfer_hints_and_counterparties(workbook: Any) -> None:
    third = by_id(workbook, "r03")
    assert third.transfer_hint is TransferHint.PIX and third.counterparty == "beltrano silva"
    own = by_id(workbook, "r04")  # 'Pix - Enviado | dd/mm hh:mm NAME'
    assert own.transfer_hint is TransferHint.PIX and own.counterparty == "fulano de tal"
    received = by_id(workbook, "r10")  # 'Transferência recebida | dd/mm hh:mm NAME'
    assert received.transfer_hint is TransferHint.PIX and received.counterparty == "fulano de tal"
    nameless = by_id(workbook, "r11")  # just 'Pix - Enviado'
    assert nameless.transfer_hint is TransferHint.PIX and nameless.counterparty == ""
    for sheet_id in ("r05", "r06"):
        row = by_id(workbook, sheet_id)
        assert row.transfer_hint is TransferHint.STATEMENT_PAYMENT and row.counterparty == ""
    sweep = by_id(workbook, "r07")
    assert sweep.transfer_hint is TransferHint.SWEEP and sweep.counterparty == ""


def test_only_transfers_get_hints(workbook: Any) -> None:
    for sheet_id in ("r01", "r02", "r08", "r09"):
        row = by_id(workbook, sheet_id)
        assert row.transfer_hint is TransferHint.NONE and row.counterparty == ""


def test_long_digit_tokens_are_dropped_from_the_counterparty(tmp_path: Path) -> None:
    rows = [
        fact("x", D(2026, 1, 1), "Banco Alfa", "Conta Corrente", "Transferencia", "Transferencia",
             "Fulano de Tal 12345678901 | Pix enviado", -5.0),
    ]  # fmt: skip
    (row,) = read_legacy_workbook(build_workbook(tmp_path / "w.xlsx", rows)).rows
    assert row.counterparty == "fulano de tal"


def test_a_card_transfer_that_is_not_a_payment_has_no_hint(tmp_path: Path) -> None:
    rows = [
        fact("x", D(2026, 1, 1), "Banco Beta", "Cartão de Crédito", "Transferencia",
             "Transferencia", "Ajuste qualquer", 5.0, statement="2026-01"),
    ]  # fmt: skip
    (row,) = read_legacy_workbook(build_workbook(tmp_path / "w.xlsx", rows)).rows
    assert row.transfer_hint is TransferHint.NONE


def test_accents_survive_as_nfc_and_are_trimmed(tmp_path: Path) -> None:
    decomposed = unicodedata.normalize("NFD", "  Café São João  ")
    rows = [
        fact(
            "x",
            D(2026, 1, 1),
            "Banco Alfa",
            "Conta Corrente",
            "Alimentação",
            "Despesa",
            decomposed,
            -1.0,
        )
    ]
    (row,) = read_legacy_workbook(build_workbook(tmp_path / "w.xlsx", rows)).rows
    assert row.description == "Café São João"
    assert unicodedata.is_normalized("NFC", row.description)
    assert row.category == "Alimentação"


def test_accounts_tolerate_placeholders(workbook: Any) -> None:
    alfa, beta = workbook.accounts
    assert (alfa.institution, alfa.credit_limit_cents, alfa.opening_balance_cents) == (
        "Banco Alfa",
        None,
        30_421,
    )
    assert beta.origin == "Cartao de Credito" and beta.credit_limit_cents is None  # PREENCHER
    assert beta.opening_balance_cents is None


def test_default_dataset_has_every_shape() -> None:
    assert {r["tipo"] for r in default_rows()} == {
        "Despesa",
        "Receita",
        "Estorno",
        "Transferencia",
        "Transferência",
    }


def test_missing_openpyxl_is_a_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_workbook(tmp_path / "w.xlsx")
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(DomainError) as exc:
        read_legacy_workbook(path)
    assert exc.value.code == "IMPORT_READER_MISSING"
