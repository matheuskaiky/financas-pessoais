"""Painel, Análises and the JSON behind the charts (design v3, package 2)."""

import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudgets
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
)
from financas.container import Container
from financas.domain.models import Account, AccountKind, TransactionKind
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings
from financas.interfaces.web import nav
from financas.interfaces.web.app import create_app

HEADERS = {"host": "localhost", "origin": "http://localhost"}
TODAY = dt.date.today()
FIRST = TODAY.replace(day=1)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'data' / 'f.db'}",
        data_dir=tmp_path / "data",
        _env_file=None,  # type: ignore[call-arg]
    )
    c = Container(settings)
    c.migrate()
    seed_categories(c.uow)
    return c


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(create_app(container), base_url="http://localhost", headers=HEADERS)


def account(
    c: Container,
    kind: AccountKind,
    name: str,
    closing_days_before_due: int | None = None,
    due_day: int | None = None,
    credit_limit_cents: int | None = None,
) -> Account:
    with c.uow as work:
        found = work.institutions.list_all()
    inst = (
        found[0]
        if found
        else CreateInstitution(c.uow).execute(CreateInstitutionCommand(name="Banco do Brasil"))
    )
    return CreateAccount(c.uow).execute(
        CreateAccountCommand(
            kind,
            inst.id,
            name,
            closing_days_before_due=closing_days_before_due,
            due_day=due_day,
            credit_limit_cents=credit_limit_cents,
        )
    )


def category(c: Container, slug: str) -> str:
    with c.uow as work:
        found = work.categories.get_by_slug(slug)
        assert found is not None
        return found.id


def spend(c: Container, acc: Account, day: dt.date, cents: int, slug: str = "groceries") -> None:
    RegisterTransaction(c.uow).execute(
        RegisterTransactionCommand(
            acc.id, day, TransactionKind.EXPENSE, cents, "Mercado São João", category(c, slug)
        )
    )


def body_of(text: str) -> str:
    return text[text.index("</head>") :]


# --- JSON on an empty database ----------------------------------------------------------------


def test_every_endpoint_answers_on_an_empty_database(client: TestClient) -> None:
    nw = client.get("/api/charts/net-worth").json()
    assert nw["points"] == [] and nw["partial"] is False and nw["first_date"] is None
    assert nw["today"] == TODAY.isoformat() and nw["min_points"] == 8
    pace = client.get("/api/charts/pace").json()
    assert pace["month"] == f"{TODAY:%Y-%m}" and pace["total_cents"] == 0
    assert pace["average_cents"] is None and pace["ceiling_cents"] is None
    assert pace["cumulative_cents"] == [0] * TODAY.day
    flow = client.get("/api/charts/cash-flow").json()
    assert flow["year"] == TODAY.year and len(flow["months"]) == TODAY.month
    assert all(m["income_cents"] == 0 and m["balance_cents"] == 0 for m in flow["months"])
    cats = client.get("/api/charts/categories").json()
    assert cats["slices"] == [] and cats["rest"] is None and cats["total_cents"] == 0
    comp = client.get("/api/charts/net-worth-composition").json()
    assert comp["net_worth_cents"] == 0 and comp["partial"] is False and comp["pending"] == []


def test_query_parameters_are_lenient(client: TestClient) -> None:
    for query in ("month=banana", "month=2026-13", "month=&year=x", "year=abc", "month=99&year=1"):
        assert client.get(f"/api/charts/pace?{query}").status_code == 200, query
        assert client.get(f"/api/charts/categories?{query}").status_code == 200, query
        assert client.get(f"/api/charts/cash-flow?{query}").status_code == 200, query
    assert client.get("/api/charts/pace?month=banana").json()["month"] == f"{TODAY:%Y-%m}"
    assert client.get("/api/charts/pace?year=2026&month=3").json()["month"] == "2026-03"
    assert client.get("/api/charts/cash-flow?year=2025").json()["months"][-1]["month"] == "2025-12"
    assert client.get("/api/charts/cash-flow?year=3000").json()["months"] == []


def test_json_carries_only_integer_cents_and_iso_dates(
    client: TestClient, container: Container
) -> None:
    checking = account(container, AccountKind.CHECKING, "Conta")
    RecordBalance(container.uow).execute(
        RecordBalanceCommand(checking.id, TODAY - dt.timedelta(days=40), 100_050)
    )
    nw = client.get("/api/charts/net-worth").json()
    assert all(
        isinstance(p["cents"], int) and re.fullmatch(r"\d{4}-\d\d-\d\d", p["date"])
        for p in nw["points"]
    )
    assert nw["points"][-1] == {"date": TODAY.isoformat(), "cents": 100_050}
    assert nw["first_date"] == (TODAY - dt.timedelta(days=40)).isoformat()


# --- data: net worth, pace, cash flow, categories ----------------------------------------------


def test_net_worth_series_ends_at_the_composition(client: TestClient, container: Container) -> None:
    checking = account(container, AccountKind.CHECKING, "Conta")
    savings = account(container, AccountKind.INVESTMENT, "Caixinha")
    start = TODAY - dt.timedelta(days=120)
    RecordBalance(container.uow).execute(RecordBalanceCommand(checking.id, start, 500_000))
    RecordBalance(container.uow).execute(RecordBalanceCommand(savings.id, start, 200_000))
    spend(container, checking, TODAY - dt.timedelta(days=3), 12_300)
    series = client.get("/api/charts/net-worth").json()
    comp = client.get("/api/charts/net-worth-composition").json()
    assert series["points"][-1]["cents"] == comp["net_worth_cents"] == 500_000 + 200_000 - 12_300
    assert comp["cash_cents"] == 487_700 and comp["investments_cents"] == 200_000
    assert len(series["points"]) >= 8 and series["partial"] is False


def test_partial_when_an_account_has_no_balance_and_the_page_says_so(
    client: TestClient, container: Container
) -> None:
    checking = account(container, AccountKind.CHECKING, "Conta")
    account(container, AccountKind.INVESTMENT, "Caixinha")
    RecordBalance(container.uow).execute(RecordBalanceCommand(checking.id, FIRST, 100_000))
    series = client.get("/api/charts/net-worth").json()
    assert series["partial"] is True and series["pending"][0]["name"] == "Caixinha"
    home = client.get("/").text
    assert "parcial · 1 item pendente" in home and "Sem saldo ou avaliação: Caixinha" in home


def test_pace_has_average_and_ceiling_only_when_they_exist(
    client: TestClient, container: Container
) -> None:
    checking = account(container, AccountKind.CHECKING, "Conta")
    previous = FIRST - dt.timedelta(days=1)
    spend(container, checking, previous.replace(day=5), 40_000)
    spend(container, checking, FIRST, 25_000)
    pace = client.get("/api/charts/pace").json()
    assert pace["cumulative_cents"][0] == 25_000 and pace["ceiling_cents"] is None
    assert pace["average_months"] == [f"{previous:%Y-%m}"] and pace["average_cents"][-1] == 40_000
    assert len(pace["average_cents"]) == pace["days"]
    SetCategoryBudgets(container.uow).execute(
        {category(container, "groceries"): 20_000, category(container, "health"): 5_000}
    )
    pace = client.get("/api/charts/pace").json()
    assert pace["ceiling_cents"] == 25_000 and pace["crossed_day"] is None  # touching is not over
    spend(container, checking, FIRST, 1)
    assert client.get("/api/charts/pace").json()["crossed_day"] == 1


def test_cash_flow_and_categories_with_accented_data(
    client: TestClient, container: Container
) -> None:
    checking = account(container, AccountKind.CHECKING, "Conta")
    RegisterTransaction(container.uow).execute(
        RegisterTransactionCommand(
            checking.id,
            FIRST,
            TransactionKind.INCOME,
            900_000,
            "Salário",
            category(container, "salary"),
        )
    )
    for n, slug in enumerate(
        ["groceries", "health", "transport", "telecom", "insurance", "home", "food"]
    ):
        spend(container, checking, FIRST, 10_000 - n * 1_000, slug)
    flow = client.get("/api/charts/cash-flow").json()["months"][-1]
    assert (
        flow["income_cents"] == 900_000
        and flow["expenses_cents"] == 10_000 + 9_000 + 8_000 + 7_000 + 6_000 + 5_000 + 4_000
    )
    assert flow["balance_cents"] == flow["income_cents"] - flow["expenses_cents"]
    cats = client.get("/api/charts/categories").json()
    assert [s["key"] for s in cats["slices"]] == [
        "groceries",
        "health",
        "transport",
        "telecom",
        "insurance",
    ]
    assert (
        cats["slices"][0]["name"] == "Supermercado"
        and cats["slices"][1]["group_label"] == "Essencial"
    )
    assert cats["rest"]["categories"] == 2 and cats["rest"]["cents"] == 9_000
    assert cats["category_count"] == 7 and cats["total_cents"] == 49_000
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", s["color"]) for s in cats["slices"])
    home = client.get("/").text
    assert (
        "Demais categorias" in home and "Supermercado" in home and "5 de 7 categorias somam" in home
    )


def test_card_entries_count_in_the_statement_month_in_the_pace(
    client: TestClient, container: Container
) -> None:
    card = account(
        container, AccountKind.CREDIT_CARD, "Cartão",
        closing_days_before_due=11, due_day=5, credit_limit_cents=500_000,
    )  # fmt: skip
    RegisterCardPurchase(container.uow).execute(
        CardPurchaseCommand(card.id, "Notebook", FIRST, total_cents=30_000)
    )
    with container.uow as work:
        month = work.statements.list_for_card(card.id)[0].month
    pace = client.get(f"/api/charts/pace?month={month}").json()
    assert pace["total_cents"] == 30_000 and pace["entry_count"] == 1


# --- pages -------------------------------------------------------------------------------------


def test_dashboard_and_analyses_render_the_chart_islands(client: TestClient) -> None:
    for path, charts in (
        ("/", ("pace", "flow")),
        ("/analises", ("pace", "flow")),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        text = response.text
        body = body_of(text)
        assert '<script type="module" src="/static/fin-charts.js">' in text  # pace, flow, donut
        assert '<script type="module" src="/static/charts.js"></script>' in text  # <fp-patrimonio>
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", text)
        assert all('type="application/json"' in tag for tag in inline), "only data blocks (CSP)"
        assert not re.search(r"\son\w+=", text)
        for kind in charts:
            assert f'data-chart="{kind}"' in body, (path, kind)
        assert (
            "<fp-patrimonio" in body
            and 'private="networth"' in body
            and 'data-chart="networth"' not in body
        )
        for state in ("skeleton", "sparse", "error"):
            assert f'data-tpl="{state}"' in body
        assert (
            'empty-label="Registrar um saldo"' in body
        )  # the range selector is drawn by the island
        assert "Chart(" not in text and "chart.umd" not in text
    assert 'data-chart="donut"' not in client.get("/analises").text  # no spending: empty state
    assert "Nenhuma despesa neste período" in client.get("/analises").text


def test_dashboard_month_stepper_and_legacy_parameters(client: TestClient) -> None:
    page = client.get("/?month=2026-08").text
    assert (
        "agosto 2026" in page
        and 'href="/?month=2026-07"' in page
        and 'href="/?month=2026-09"' in page
    )
    assert "Saldo de agosto" in page
    current = client.get("/").text
    assert f"/?month={FIRST.replace(day=1) - dt.timedelta(days=1):%Y-%m}" in current
    assert "Próximo mês (indisponível)" in current  # nothing after the current month
    legacy = client.get("/?year=2026&month=7").text
    assert "julho 2026" in legacy
    assert client.get("/?month=garbage").status_code == 200


def test_analyses_is_in_the_navigation(client: TestClient) -> None:
    page = client.get("/").text
    assert re.search(r'class="item" href="/analises"', page) and "Análises" in page
    assert re.search(
        r'class="item" href="/analises"\s+aria-current="page"', client.get("/analises").text
    )
    entry = next(e for e in nav.entries() if e.id == "analises")
    assert (entry.group, entry.order) == ("main", 20)


def test_letter_pill_only_while_the_carta_entry_exists(container: Container) -> None:
    original = next((e for e in nav.entries() if e.id == "carta"), None)
    try:
        client = TestClient(create_app(container), base_url="http://localhost", headers=HEADERS)
        nav.unregister("carta")
        assert "pronta" not in client.get("/").text
        nav.register(
            nav.NavEntry("carta", "Carta do mês", nav.ICONS["carta"], "/carta", "main", 30)
        )
        page = client.get("/").text
        previous = FIRST - dt.timedelta(days=1)
        months = (
            "janeiro",
            "fevereiro",
            "março",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
        )
        assert f"Carta de {months[previous.month - 1]} pronta" in page and 'href="/carta"' in page
    finally:
        nav.unregister("carta")
        if original is not None:
            nav.register(original)


def test_chart_js_library_is_gone_and_the_assets_are_local(client: TestClient) -> None:
    assert client.get("/static/chart.umd.min.js").status_code == 404
    assert client.get("/static/dashboard.js").status_code == 404
    for asset in ("fin-charts.js", "fin-charts.css", "charts.js", "charts.css", "fp-money.js"):
        assert client.get(f"/static/{asset}").status_code == 200
    js = client.get("/static/fin-charts.js").text
    assert "https://" not in js and "FinCharts" in js and 'from "./fp-money.js"' in js
    assert "http://" not in js.replace("http://www.w3.org/2000/svg", "")  # the SVG namespace only
    island = client.get("/static/charts.js").text
    assert "fp-patrimonio" in island and "https://" not in island
    assert 'from "./fp-money.js"' in island  # same directory, same URL as the head loads
    for sheet in ("fin-charts.css", "charts.css"):
        css = client.get(f"/static/{sheet}").text
        assert "http://" not in css and "https://" not in css
