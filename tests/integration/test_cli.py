"""The CLI end to end, on a temporary database (synthetic data only)."""

import datetime as dt
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
    declined = runner.invoke(app, ["delete", code], input="n\n", terminal_width=200)
    assert declined.exit_code == 0 and "Nada foi apagado" in declined.output
    assert "apagados: 1" in run("delete", code, "--yes")
    assert "não encontrado" in run("delete", "zzzzzzzz", "-y", ok=False)
    assert "não encontrado" in run("delete", "", "-y", ok=False)  # an empty prefix matches nothing
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
        "--closes-before-due",
        "11",
        "--due",
        "5",
        "--limit",
        "12.000,00",
    )
    assert "Cartão criado: Nubank (vence dia 5, fecha 11 dias antes)" in out


def test_card_purchase_with_schedule_and_explanation() -> None:
    setup_card()
    out = run(
        "card", "buy", "Fone de ouvido", "--card", "nubank", "-d", "26/07/2026",
        "--total", "301,00", "-n", "3", "-y",
    )  # fmt: skip
    assert "Compra em 26/07/2026, depois do fechamento em 25/07 → fatura de ago/2026" in out
    assert "(fecha 25/08 · vence 05/09)" in out
    assert "Melhor dia de compra neste cartão: 25/08/2026" in out
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
    assert "de 1 a 27 dias" in run("card", "edit", "nubank", "--closes-before-due", "40", ok=False)
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


def test_inform_without_an_amount_does_not_wipe_the_total() -> None:
    setup_card()
    run("card", "buy", "Mochila", "--card", "nubank", "-d", "10/07/2026", "--total", "100,00", "-y")
    run("statement", "inform", "nubank", "2026-07", "100,00")
    assert "Informe o total" in run("statement", "inform", "nubank", "2026-07", ok=False)
    assert "informado pelo banco R$ 100,00" in run("statement", "show", "nubank", "2026-07")


def test_user_text_with_brackets_does_not_break_the_tables() -> None:
    setup_basic()
    run("add", "10", "Pagamento [/pagamento] [Casa]", "-d", "10/07/2026")
    listing = run("list", "-m", "2026-07")
    assert "[/pagamento]" in listing and "[Casa]" in listing


def test_a_bad_image_creates_nothing(tmp_path: Path) -> None:
    run("init")
    bad = tmp_path / "logo.png"
    bad.write_bytes(b"GIF89a....")
    assert "Formato de imagem" in run("institution", "add", "Banco", "--image", str(bad), ok=False)
    assert "Banco" not in run("institution", "list")


# --- budget, recurring and daily flow (Phase 4) ---


def month_start(back: int) -> dt.date:
    """The first day of the month ``back`` months before this one (real clock: the CLI uses it)."""
    today = dt.date.today()
    index = today.year * 12 + today.month - 1 - back
    return dt.date(index // 12, index % 12 + 1, 1)


def br(day: dt.date, offset: int = 4) -> str:
    return (day + dt.timedelta(days=offset)).strftime("%d/%m/%Y")


def test_budget_goals_and_matrix() -> None:
    setup_basic()
    assert "Meta de Alimentação: R$ 700,00." in run("budget", "set", "food", "700,00")
    run("budget", "set", "groceries", "1.200,00")
    for back, cents in ((3, "910,00"), (2, "788,20"), (1, "842,30")):
        run("add", cents, "Delivery", "-c", "food", "-d", br(month_start(back)))
    run("add", "999,00", "Mês corrente", "-c", "food", "-d", br(month_start(0)))  # not closed
    run("add", "200,00", "Sem meta", "-c", "health", "-d", br(month_start(2)))
    out = run("budget", "show")
    assert "Alimentação" in out and "R$ 910,00" in out and "R$ 788,20" in out
    assert "R$ 700,00" in out and "Categorias com meta" in out
    assert "Sem meta (Saúde)" in out and "Total das despesas" in out
    assert "R$ 999,00" not in out  # the month in progress is left out
    assert "acima da meta: 1 de 2" in out
    run("budget", "set", "food", "--clear")
    assert "acima da meta: 0 de 1" in run("budget", "show")


def test_budget_errors_and_year_view() -> None:
    setup_basic()
    assert "Só categorias de despesa" in run("budget", "set", "salary", "100,00", ok=False)
    assert "maior que zero" in run("budget", "set", "food", "0", ok=False)
    assert "Informe o valor" in run("budget", "set", "food", ok=False)
    assert "Nenhuma categoria tem meta" in run("budget", "show")
    assert "Ainda não há mês fechado" in run(
        "budget", "show", "--year", str(dt.date.today().year + 1)
    )


def test_recurring_matrix_and_alerts() -> None:
    setup_basic()
    for back, cents in ((3, "119,90"), (2, "119,90"), (1, "129,90")):
        run("add", cents, "Internet", "-c", "telecom", "-r", "-d", br(month_start(back)))
    for back in (3, 2):
        run("add", "90,00", "Academia", "-c", "health", "-r", "-d", br(month_start(back)))
    run("add", "39,90", "Streaming [novo]", "-c", "subscriptions", "-r", "-d", br(month_start(1)))
    listing = run("recurring", "list")
    assert "Internet" in listing and "R$ 129,90" in listing and "Academia" in listing
    assert "em andamento" in listing
    alerts = run("recurring", "alerts")
    assert "Mudou de valor: Internet: de R$ 119,90" in alerts and "para R$ 129,90" in alerts
    assert "Sumiu: Academia" in alerts
    assert "Apareceu: Streaming [novo]: novo em" in alerts


def test_daily_flow_of_an_account() -> None:
    setup_basic()
    run("balance", "set", "corrente", "1.000,00", "-d", "30/06/2026")
    run("add", "5.000,00", "Salário", "-k", "income", "-c", "salary", "-d", "05/07/2026")
    run("add", "120,00", "Mercado", "-c", "groceries", "-d", "05/07/2026")
    run("transfer", "500,00", "--from", "corrente", "--to", "caixinha", "-d", "09/07/2026")
    out = run("account", "flow", "corrente", "-m", "2026-07")
    assert "05/07/2026" in out and "R$ 5.000,00" in out and "R$ 120,00" in out
    assert "R$ 5.880,00" in out and "R$ 5.380,00" in out  # running balance
    assert "Saldo inicial: R$ 1.000,00" in out
    assert "saldo indisponível" in run("account", "flow", "caixinha", "-m", "2026-07")


def test_failure_log_command() -> None:
    assert "Nenhuma falha registrada" in run("log", "show")
    from financas.container import build_container

    code = build_container().failures.record("cli", "cli_error", path="card", error="RuntimeError")
    out = run("log", "show")
    assert code in out and "Erro num comando" in out and "RuntimeError" in out


def test_account_add_with_an_opening_balance() -> None:
    run("init")
    run("institution", "add", "Banco")
    out = run(
        "account", "add", "Corrente", "-i", "banco", "--opening", "1.183,67",
        "--opening-date", "08/09/2026",
    )  # fmt: skip
    assert "Saldo inicial registrado: R$ 1.183,67" in out
    assert "R$ 1.183,67" in run("balance", "list")
    assert "valor e a data" in run(
        "account", "add", "Outra", "-i", "banco", "--opening", "10,00", ok=False
    )


def test_merchants_backfill_dry_run_then_apply_shows_counts_only(tmp_path: Path) -> None:
    import sqlite3

    setup_basic()
    path = tmp_path / "data" / "f.db"
    with sqlite3.connect(path) as db:
        account = db.execute(
            "select id from accounts where nickname = 'Conta Corrente'"
        ).fetchone()[0]
        category = db.execute("select id from categories where slug = 'uncategorized'").fetchone()[
            0
        ]
        for number, text in enumerate(("Mouse - Kabum", "PAG*MERCADOLIVRE 123", "Sem pista")):
            db.execute(
                "insert into transactions (id, account_id, posted_on, kind, category_id,"
                " amount_cents, description, description_search, is_recurring)"
                " values (?, ?, '2026-07-01', 'expense', ?, -1000, ?, ?, 0)",
                (f"t{number}".ljust(32, "0"), account, category, text, text.lower()),
            )
    dry = run("merchants", "backfill", "--dry-run")
    assert "3 despesas e estornos analisados; 2 mudariam." in dry
    assert "pelo sufixo da descrição: 1" in dry and "por um nome conhecido na descrição: 1" in dry
    assert "Nada foi gravado" in dry
    assert "Kabum" not in dry and "Mouse" not in dry  # counts only: never the text of an entry
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select count(*) from transactions where merchant is not null"
        ).fetchone() == (0,)
    done = run("merchants", "backfill")
    assert "2 mudaram." in done and "1 descrição(ões) perderam o sufixo" in done
    with sqlite3.connect(path) as db:
        rows = db.execute("select description, merchant from transactions order by id").fetchall()
    assert rows == [
        ("Mouse", "Kabum"),
        ("PAG*MERCADOLIVRE 123", "Mercado Livre"),
        ("Sem pista", None),
    ]
    assert "0 mudaram." in run("merchants", "backfill")  # nothing left to do


def test_reconcile_transfers_dry_run_changes_nothing_and_apply_links_the_pair() -> None:
    run("init")
    run("institution", "add", "Banco do Brasil")
    run("account", "add", "Inter", "-i", "banco")
    run("account", "add", "Nubank", "-i", "banco")
    run("add", "500,00", "PIX para Nubank", "-d", "06/07/2026", "-a", "inter")
    run(
        "add",
        "500,00",
        "PIX recebido",
        "-k",
        "income",
        "-c",
        "salary",
        "-d",
        "06/07/2026",
        "-a",
        "nubank",
    )
    dry = run("reconcile-transfers")
    assert "1 par(es) encontrado(s)" in dry and "Simulação: nada foi alterado" in dry
    assert "Nubank" in dry and "95%" in dry
    assert "1 par(es)" in run("reconcile-transfers", "--dry-run")  # still there: nothing was linked
    applied = run("reconcile-transfers", "--apply")
    assert "Backup criado" in applied and "1 transferência(s) ligada(s)" in applied
    assert "0 par(es) encontrado(s)" in run("reconcile-transfers")
    assert "transferência" in run("list", "-m", "2026-07").lower()
