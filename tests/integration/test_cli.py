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


# --- investments (Phase 3a) ---


def setup_investments() -> None:
    setup_basic()  # "Conta Corrente" and the investment account "Caixinha" (class: other)
    run("invest", "settings", "caixinha", "--class", "fixed_income", "--emergency")


def test_investment_valuation_flow_yield_and_list() -> None:
    setup_investments()
    run("invest", "value", "caixinha", "10.000,00", "-d", "01/07/2026")
    run("invest", "flow", "caixinha", "1.000,00", "--from", "corrente", "-d", "10/07/2026")
    out = run(
        "invest", "value", "caixinha", "11.120,00", "--gross", "11.300,00", "-d", "31/07/2026"
    )
    assert "Avaliação registrada: R$ 11.120,00" in out
    assert "+ R$ 120,00" in out and "não é renda" in out
    listing = run("invest", "list")
    assert "Caixinha" in listing and "Renda fixa" in listing and "R$ 11.120,00" in listing
    assert "+ R$ 120,00" in listing and "Total investido (líquido): R$ 11.120,00" in listing
    assert "não calcula imposto" in listing
    summary = run("summary", "-m", "2026-07")
    assert "Aportes líquidos: R$ 1.000,00" in summary


def test_investment_withdrawal_untracked_side_and_errors() -> None:
    setup_investments()
    assert "Resgate registrado (2 perna(s))" in run(
        "invest",
        "flow",
        "caixinha",
        "200,00",
        "--from",
        "corrente",
        "--withdraw",
        "-d",
        "05/07/2026",
    )
    assert "Aporte registrado (1 perna(s))" in run(
        "invest", "flow", "caixinha", "50,00", "-d", "06/07/2026"
    )
    assert "valor bruto não pode ser menor" in run(
        "invest", "value", "caixinha", "100,00", "--gross", "90,00", ok=False
    )
    assert "só se aplica a contas de investimento" in run(
        "invest", "value", "corrente", "100,00", "--gross", "120,00", ok=False
    )
    assert "exige uma conta de investimento" in run("invest", "flow", "corrente", "10,00", ok=False)
    assert "Informe um nome" not in run("invest", "list")


def test_investment_year_networth_and_pending() -> None:
    setup_investments()
    run("invest", "value", "caixinha", "10.000,00", "-d", "31/12/2025")
    run("invest", "flow", "caixinha", "500,00", "--from", "corrente", "-d", "15/03/2026")
    run("invest", "value", "caixinha", "10.700,00", "-d", "30/06/2026")
    year = run("invest", "year", "2026")
    assert "Aportes líquidos: R$ 500,00" in year
    assert "Rendimento capitalizado: + R$ 200,00" in year and "não é renda" in year
    assert "Posição em 31/12/2026" in year and "R$ 10.700,00" in year
    out = run("networth")
    assert "Patrimônio líquido" in out and "Investimentos: R$ 10.700,00" in out
    assert "Parcial · saldo ou avaliação pendente: Conta Corrente" in out  # checking has no balance
    run("balance", "set", "corrente", "1.000,00", "-d", "01/01/2026")
    assert "Parcial" not in run("networth")


# --- fixed-income holdings (Phase 3b) ---


def setup_holdings() -> None:
    run("init")
    run("institution", "add", "Inter", "--group", "Grupo Inter")
    run("account", "add", "Conta Inter", "-i", "inter")
    run("account", "add", "Corretora", "-i", "inter", "-k", "investment", "--class", "fixed_income")
    run("invest", "settings", "corretora", "--tracking", "holdings")


def add_cdb(name: str = "CDB Inter 110% CDI", *extra: str) -> str:
    return run(
        "invest", "holding", "add", name, "-a", "corretora", "-t", "cdb", "--issuer", "inter",
        "--applied", "01/03/2026", "--principal", "10.000,00", "--liquidity", "at_maturity",
        "--maturity", "01/03/2028", "--indexer", "cdi", "--mode", "percent_of_index",
        "--rate", "110",
        *extra,
    )  # fmt: skip


def test_holding_registration_valuation_and_listing() -> None:
    setup_holdings()
    out = add_cdb()
    assert "Aplicação cadastrada: CDB Inter 110% CDI · 110% do CDI · FGC: sim." in out
    run(
        "invest",
        "holding",
        "value",
        "cdb inter",
        "10.500,00",
        "-d",
        "30/09/2026",
        "--gross",
        "10.700,00",
    )
    listing = run("invest", "holding", "list")
    assert "CDB Inter 110% CDI" in listing and "110% do CDI" in listing and "01/03/2028" in listing
    assert "No vencimento" in listing and "R$ 10.500,00" in listing
    assert "Total investido (líquido): R$ 10.500,00" in run("invest", "list")
    out = run("invest", "holding", "value", "cdb inter", "10.600,00", "-d", "31/10/2026")
    assert "Rendimento desde a avaliação anterior: + R$ 100,00 (não é renda)" in out


def test_holding_flow_redemption_and_atomic_errors() -> None:
    setup_holdings()
    add_cdb("CDB A", "--contribute", "--from", "conta inter")
    run("invest", "holding", "value", "cdb a", "10.000,00", "-d", "02/03/2026")
    assert "Aporte registrado (2 perna(s))" in run(
        "invest", "holding", "flow", "cdb a", "500,00", "--from", "conta inter", "-d", "10/04/2026"
    )
    assert "exige uma conta de investimento" in run(
        "invest", "flow", "conta inter", "1,00", ok=False
    )
    assert "controlada por aplicação" in run("invest", "flow", "corretora", "1,00", ok=False)
    run("invest", "holding", "value", "cdb a", "10.800,00", "-d", "28/02/2027")
    assert "Aplicação resgatada: CDB A" in run(
        "invest",
        "holding",
        "redeem",
        "cdb a",
        "10.850,00",
        "--to",
        "conta inter",
        "-d",
        "01/03/2027",
    )
    assert "Resgatada" in run("invest", "holding", "list", "--all")
    assert "CDB A" not in run("invest", "holding", "list")
    assert "já foi resgatada" in run(
        "invest", "holding", "redeem", "cdb a", "1,00", "-d", "02/03/2027", ok=False
    )


def test_holding_validation_messages() -> None:
    setup_holdings()
    add_args = (
        "invest", "holding", "add", "X", "-a", "corretora", "-t", "cdb", "--issuer", "inter",
        "--applied", "01/03/2026", "--principal", "100,00", "--liquidity", "at_maturity",
    )  # fmt: skip
    assert "Informe o vencimento" in run(*add_args, ok=False)
    assert "Taxa inválida" in run(
        *add_args,
        "--maturity",
        "01/03/2028",
        "--mode",
        "percent_of_index",
        "--rate",
        "110",
        ok=False,
    )
    assert "Taxa inválida" in run(
        *add_args, "--maturity", "01/03/2028", "--rate", "6,555", ok=False
    )
    assert "depois da data de aplicação" in run(*add_args, "--maturity", "01/01/2026", ok=False)
    run("account", "add", "Poupança", "-i", "inter", "-k", "investment")
    assert "controlada por conta" in run(
        "invest", "holding", "add", "Y", "-a", "poupanca", "-t", "cdb", "--issuer", "inter",
        "--applied", "01/03/2026", "--principal", "1,00", "--liquidity", "daily", ok=False,
    )  # fmt: skip


def test_ladder_liquidity_fgc_and_emergency_views() -> None:
    setup_holdings()
    run("balance", "set", "conta inter", "1.320,00", "-d", "30/09/2026")
    add_cdb("CDB longo")
    add_cdb("CDB reserva", "--emergency")
    run(
        "invest", "holding", "add", "Tesouro IPCA", "-a", "corretora", "-t", "treasury_ipca",
        "--issuer", "inter", "--applied", "01/03/2026", "--principal", "5.000,00",
        "--liquidity", "daily", "--maturity", "01/03/2035", "--indexer", "ipca",
        "--mode", "spread_over_index", "--rate", "6,5",
    )  # fmt: skip
    for name, cents in (
        ("cdb longo", "10.000,00"),
        ("cdb reserva", "20.000,00"),
        ("tesouro", "5.000,00"),
    ):
        run("invest", "holding", "value", name, cents, "-d", "30/09/2026")
    ladder = run("invest", "ladder")
    assert "mar/2028" in ladder and "R$ 30.000,00" in ladder and "mar/2035" in ladder
    assert "não projeta valor no vencimento" in ladder
    liquidity = run("invest", "liquidity")
    assert (
        "Disponível hoje" in liquidity and "R$ 5.000,00" in liquidity and "Mais tarde" in liquidity
    )
    fgc = run("invest", "fgc")
    assert (
        "grupo_inter" in fgc and "R$ 31.320,00" in fgc and "R$ 1.320,00" in fgc
    )  # CDBs + checking
    assert "fgc.org.br" in fgc
    assert "IPCA + 6,50%" in run("invest", "holding", "list")
    emergency = run("invest", "emergency")
    assert "R$ 20.000,00" in emergency and "CDB reserva" in emergency
    assert "sem gasto essencial" in emergency
    run("invest", "holding", "flags", "tesouro", "--fgc")  # the user can mark it as covered
    assert "R$ 36.320,00" in run("invest", "fgc")
