"""The CLI end to end, on a temporary database (synthetic data only)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from financas.interfaces.cli import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINANCAS_DB_URL", f"sqlite:///{tmp_path / 'data' / 'f.db'}")
    monkeypatch.setenv("FINANCAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COLUMNS", "220")


def run(*args: str, ok: bool = True) -> str:
    result = runner.invoke(app, list(args), terminal_width=200)
    assert (result.exit_code == 0) is ok, result.output
    return result.output


def setup_basic() -> None:
    run("init")
    run("institution", "add", "Banco do Brasil", "--color", "#fcfc30")
    run("account", "add", "Conta Corrente", "-i", "banco do brasil")
    run("account", "add", "Caixinha", "-i", "banco", "-k", "investment")


def test_full_flow() -> None:
    setup_basic()
    assert "Saúde" in run("category", "list")
    run("balance", "set", "corrente", "1.000,00", "-d", "01/07/2026")
    out = run(
        "add", "45,90", "Padaria São João", "-c", "food", "-d", "10/07/2026", "-a", "corrente"
    )
    assert "-R$ 45,90" in out
    out = run("add", "5.000,00", "Salário", "-k", "income", "-c", "salary", "-d", "05/07/2026")
    assert "R$ 5.000,00" in out
    out = run("transfer", "500,00", "--from", "corrente", "--to", "caixinha", "-d", "06/07/2026")
    assert "2 perna(s)" in out
    # the next entry with the same description gets the suggested category
    assert "Categoria sugerida: Alimentação" in run(
        "add", "10", "padaria sao joao", "-d", "11/07/2026"
    )
    listing = run("list", "-m", "2026-07")
    assert "Padaria São João" in listing and "julho de 2026" in listing
    summary = run("summary", "-m", "2026-07")
    assert "R$ 5.000,00" in summary and "R$ 55,90" in summary and "Alimentação" in summary
    accounts = run("account", "list")
    assert "Conta Corrente" in accounts and "saldo indisponível" in accounts  # the savings has none


def test_errors_are_shown_in_portuguese_with_exit_code_1() -> None:
    setup_basic()
    assert "Valor inválido" in run("add", "abc", "x", ok=False)
    assert "Cor inválida" in run("institution", "add", "Nu", "--color", "roxo", ok=False)
    assert "Data inválida" in run("add", "1", "x", "-d", "31/02/2026", ok=False)
    assert "não encontrado" in run("add", "1", "x", "-a", "inexistente", ok=False)
    assert "não combina" in run("add", "1", "x", "-k", "income", "-c", "food", ok=False)
    assert "Informe ao menos uma conta" in run("transfer", "10", ok=False)


def test_style_sets_color_and_image(tmp_path: Path) -> None:
    setup_basic()
    image = tmp_path / "logo.png"
    image.write_bytes(PNG)
    run("style", "institution", "banco", "--image", str(image), "--color", "#1e395f")
    assert "#1E395F" in run("institution", "list")
    assert len(list((tmp_path / "data" / "images").iterdir())) == 1
    run("style", "institution", "banco", "--remove-image")
    assert list((tmp_path / "data" / "images").iterdir()) == []
    bad = tmp_path / "logo.svg"
    bad.write_text("<svg/>", encoding="utf-8")
    assert "Formato de imagem" in run(
        "style", "institution", "banco", "--image", str(bad), ok=False
    )


def test_delete_and_backup(tmp_path: Path) -> None:
    setup_basic()
    out = run("add", "10", "Teste", "-d", "10/07/2026")
    code = out.split("Código:")[1].strip().rstrip(".")
    assert "apagados: 1" in run("delete", code)
    assert "não encontrado" in run("delete", "zzzzzzzz", ok=False)
    assert "Backup criado" in run("backup")
    assert len(list((tmp_path / "data" / "backups").iterdir())) == 1


def setup_card() -> None:
    setup_basic()
    out = run(
        "card",
        "add",
        "Nubank",
        "-i",
        "banco",
        "--closing",
        "25",
        "--due",
        "5",
        "--limit",
        "12.000,00",
    )
    assert "Cartão criado: Nubank (fecha dia 25, vence dia 5)" in out


def test_card_purchase_with_schedule_and_explanation() -> None:
    setup_card()
    out = run(
        "card", "buy", "Fone de ouvido", "--card", "nubank", "-d", "26/07/2026",
        "--total", "301,00", "-n", "3", "-y",
    )  # fmt: skip
    assert "Compra em 26/07/2026, depois do fechamento do dia 25 → fatura de ago/2026" in out
    assert "(fecha 25/08 · vence 05/09)" in out
    assert "Melhor dia de compra neste cartão: dia 26" in out
    assert "1/3" in out and "R$ 100,34" in out and "R$ 100,33" in out
    assert "3 lançamento(s)" in out
    listing = run("statement", "list")
    assert "Fatura ago/2026 · fecha 25/08 · vence 05/09" in listing
    assert "Fatura set/2026" in listing and "Fatura out/2026" in listing
    assert "Fone de ouvido" in run("card", "installments")
    shown = run("statement", "show", "nubank", "2026-08")
    assert "R$ 100,34" in shown and "Fone de ouvido" in shown


def test_card_purchase_can_be_declined_and_validates() -> None:
    setup_card()
    result = runner.invoke(
        app,
        ["card", "buy", "Teste", "--card", "nubank", "-d", "10/07/2026", "--total", "10,00"],
        input="n\n",
        terminal_width=200,
    )
    assert result.exit_code == 0 and "Nada foi salvo" in result.output
    assert "Fatura jul" not in run("statement", "list")  # nothing was saved
    assert "Informe a data da compra" in run(
        "card", "buy", "x", "--card", "nubank", "--total", "1", "-y", ok=False
    )
    assert "Compra em andamento: informe a fatura" in run(
        "card",
        "buy",
        "x",
        "--card",
        "nubank",
        "-n",
        "10",
        "--current",
        "3",
        "--installment-value",
        "61,88",
        "-y",
        ok=False,
    )
    assert "Informe o valor total ou o valor da parcela" in run(
        "card", "buy", "x", "--card", "nubank", "-d", "10/07/2026", "-y", ok=False
    )


def test_running_purchase_statement_payment_and_reconciliation() -> None:
    setup_card()
    out = run(
        "card", "buy", "Geladeira", "--card", "nubank", "-n", "10", "--current", "3",
        "--installment-value", "61,88", "--statement", "2026-09", "-y",
    )  # fmt: skip
    assert "8 lançamento(s)" in out and "Fatura escolhida por você" in out
    run("statement", "inform", "nubank", "2026-09", "70,00")
    shown = run("statement", "show", "nubank", "2026-09")
    assert "informado pelo banco R$ 70,00" in shown and "diferença R$ 8,12" in shown
    assert "Diferença lançada: R$ 8,12" in run("statement", "difference", "nubank", "2026-09")
    paid = run("statement", "pay", "nubank", "2026-09", "--from", "corrente", "--amount", "20,00")
    assert "Pagamento de R$ 20,00 registrado" in paid
    assert "R$ 50,00" in run("statement", "show", "nubank", "2026-09")  # still to pay
    run("statement", "dates", "nubank", "2026-09", "--closing", "24/09/2026", "--due", "06/10/2026")
    assert "fecha 24/09 · vence 06/10" in run("statement", "list")
    assert "Total informado" in run("statement", "inform", "nubank", "2026-09", "--clear")


def test_card_list_edit_and_limit() -> None:
    setup_card()
    run("card", "buy", "Mochila", "--card", "nubank", "-d", "10/07/2026", "--total", "100,00", "-y")
    listing = run("card", "list")
    assert "Nubank" in listing and "R$ 12.000,00" in listing and "R$ 100,00" in listing
    run("card", "edit", "nubank", "--no-limit")
    assert "limite não informado" in run("card", "list")
    assert "Dia inválido" in run("card", "edit", "nubank", "--closing", "40", ok=False)
    assert "veja" in run("account", "list")  # cards are not shown as an unavailable balance


def test_add_on_a_card_uses_the_cycle_and_income_is_refused() -> None:
    setup_card()
    out = run("add", "45,90", "Padaria", "-a", "nubank", "-d", "26/07/2026")
    assert "-R$ 45,90" in out
    assert "Fatura ago/2026" in run("statement", "list")
    assert "não aceita esse lançamento" in run(
        "add", "5", "x", "-a", "nubank", "-k", "income", ok=False
    )
