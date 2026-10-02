"""`financas import plan` on a synthetic workbook: review files, no database writes."""

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from financas.interfaces.cli import app
from legacy_workbook import D, build_workbook, fact

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINANCAS_DB_URL", f"sqlite:///{tmp_path / 'data' / 'f.db'}")
    monkeypatch.setenv("FINANCAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COLUMNS", "220")


def plan(xlsx: Path, *args: str, ok: bool = True) -> str:
    result = runner.invoke(app, ["import", "plan", str(xlsx), *args], terminal_width=200)
    assert (result.exit_code == 0) is ok, result.output
    return result.output


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def fill_cards(directory: Path) -> None:
    rows = read(directory / "contas.csv")
    for row in rows:
        if row["kind"] == "credit_card":
            row.update(due_day="26", closes_before_due="7", limit="1.000,00")
    write(directory / "contas.csv", rows)


def test_first_run_writes_templates_and_asks_for_the_cards(tmp_path: Path) -> None:
    xlsx = build_workbook(tmp_path / "old.xlsx")
    out = plan(xlsx, ok=False)  # the card has no due day yet
    directory = tmp_path / "data" / "import"
    assert "Modelo de contas criado" in out and "Cartão sem vencimento" in out
    for name in (
        "contas.csv",
        "categorias.csv",
        "contrapartes.csv",
        "lancamentos.csv",
        "relatorio.txt",
    ):
        assert (directory / name).exists(), name
    accounts = {r["key"]: r for r in read(directory / "contas.csv")}
    assert accounts["banco_alfa_cc"]["kind"] == "checking"
    assert accounts["banco_beta_card"]["kind"] == "credit_card"
    assert (
        accounts["sweep"]["kind"] == "investment"
        and accounts["sweep"]["asset_class"] == "fixed_income"
    )
    assert not (tmp_path / "data" / "f.db").exists()  # a dry run never creates the database


def test_reviewed_files_drive_the_plan_and_decisions_are_remembered(tmp_path: Path) -> None:
    xlsx = build_workbook(tmp_path / "old.xlsx")
    plan(xlsx, ok=False)
    directory = tmp_path / "data" / "import"
    fill_cards(directory)
    out = plan(xlsx, "--holder", "Fulano de Tal")
    assert "Linhas no período (2026, por competência): 11" in out
    # the card has no purchases to pay: the 1,000.00 payment is only an outflow of the checking
    assert "pagamentos de fatura: 0" in out and "Pix de terceiro" in out
    assert "pagamento de fatura de fora do período" in out
    counterparties = {r["key"]: r["suggested"] for r in read(directory / "contrapartes.csv")}
    assert all(r["decision"] == "" for r in read(directory / "contrapartes.csv"))
    assert counterparties["fulano de tal"] == "own"
    assert counterparties["beltrano silva"] == "third_party"
    # the user changes one decision and drops a row: the next run honours both
    rows = read(directory / "contrapartes.csv")
    for row in rows:
        if row["key"] == "beltrano silva":
            row["decision"] = "category:food"
    write(directory / "contrapartes.csv", rows)
    entries = read(directory / "lancamentos.csv")
    assert any(e["sheet_ids"] == "r08" and e["keep"] == "yes" for e in entries)
    for e in entries:
        if e["sheet_ids"] == "r08":
            e["keep"] = "no"
        if e["sheet_ids"] == "r01":
            e["category_override"] = "services"
    write(directory / "lancamentos.csv", entries)
    out = plan(xlsx, "--holder", "Fulano de Tal")
    after = {e["sheet_ids"]: e for e in read(directory / "lancamentos.csv")}
    assert "r08" not in after  # dropped
    assert (
        after["r01"]["category"] == "services" and after["r01"]["category_override"] == "services"
    )
    assert after["r03"]["category"] == "food" and "provisional" not in after["r03"]["flags"]
    report = (directory / "relatorio.txt").read_text(encoding="utf-8")
    assert report.strip() == out.strip()
    assert "Padaria" not in report and "Beltrano" not in report  # no descriptions in the report


def test_an_unknown_category_blocks_the_plan(tmp_path: Path) -> None:
    rows = [
        fact(
            "x1",
            D(2026, 2, 1),
            "Banco Alfa",
            "Conta Corrente",
            "Categoria Nova",
            "Despesa",
            "Algo",
            -10.0,
        )
    ]
    xlsx = build_workbook(tmp_path / "old.xlsx", rows)
    out = plan(xlsx, ok=False)
    assert "Categoria sem equivalente" in out and "Categoria Nova" in out
    mapping = read(tmp_path / "data" / "import" / "categorias.csv")
    assert {"legacy": "Categoria Nova", "slug": ""} in mapping
    for row in mapping:
        if row["legacy"] == "Categoria Nova":
            row["slug"] = "services"
    write(tmp_path / "data" / "import" / "categorias.csv", mapping)
    assert "Problemas:\n  nenhum" in plan(xlsx)


def test_an_invalid_accounts_file_is_a_clear_error(tmp_path: Path) -> None:
    xlsx = build_workbook(tmp_path / "old.xlsx")
    plan(xlsx, ok=False)
    directory = tmp_path / "data" / "import"
    rows = read(directory / "contas.csv")
    rows[0]["due_day"] = "abc"
    write(directory / "contas.csv", rows)
    assert "contas.csv: há uma linha inválida" in plan(xlsx, ok=False)
