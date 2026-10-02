"""Front v3, existing screens restyled (package 3): markup contracts, states, responsive lists."""

import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financas.container import Container
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings
from financas.interfaces.web.app import create_app

HEADERS = {"host": "localhost", "origin": "http://localhost"}


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
    return TestClient(
        create_app(container), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )


def bank(client: TestClient, container: Container) -> tuple[str, str]:
    """An institution, a checking account and a card (due 5, closes 11 days before, R$ 1.000,00)."""
    client.post(
        "/institutions", data={"name": "Banco do Brasil", "color": "#0F5C45", "use_color": "1"}
    )
    with container.uow as work:
        inst = work.institutions.list_all()[0].id
    client.post("/accounts", data={"nickname": "Conta Corrente", "institution_id": inst})
    client.post(
        "/cards",
        data={
            "nickname": "Ourocard",
            "institution_id": inst,
            "closing_days_before_due": "11",
            "due_day": "5",
            "limit": "1.000,00",
        },
    )
    with container.uow as work:
        by_name = {a.nickname: a.id for a in work.accounts.list_all()}
    return by_name["Conta Corrente"], by_name["Ourocard"]


def test_the_screens_stylesheet_is_linked_and_served(client: TestClient) -> None:
    page = client.get("/more").text
    assert page.index("/static/app.css") < page.index("/static/screens.css")
    css = client.get("/static/screens.css")
    assert css.status_code == 200 and ".rtable" in css.text and "fonts.googleapis" not in css.text


def test_entries_page_uses_the_v3_layout(client: TestClient, container: Container) -> None:
    checking, _ = bank(client, container)
    client.post(
        "/entries",
        data={
            "kind": "income",
            "account_id": checking,
            "date": "2026-07-10",
            "amount": "1.000,00",
            "description": "Salário",
        },
    )
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": checking,
            "date": "2026-07-11",
            "amount": "40,00",
            "description": "Café São João",
        },
    )
    page = client.get("/entries?month=2026-07").text
    assert 'class="filter-bar"' in page and page.count("data-autosubmit") == 3
    assert re.search(r'data-odometer="100000"', page) and re.search(r'data-odometer="-4000"', page)
    assert 'class="day-head"' in page and 'id="lista"' in page
    # the quick form: a full-screen target on narrow screens, with a way back and a pair of fields
    assert 'id="form" class="panel quick"' in page and 'href="#lista"' in page
    assert 'class="pair"' in page and 'class="mobile-save"' in page
    assert (
        'class="only-transfer stack-form"' in page
    )  # no inline style that would defeat the toggle
    assert "onchange=" not in page and "onclick=" not in page


def test_entries_empty_states(client: TestClient, container: Container) -> None:
    bank(client, container)
    empty = client.get("/entries?month=2026-07").text
    assert "Nenhum lançamento neste mês." in empty and 'class="state' in empty
    filtered = client.get("/entries?month=2026-07&q=zzz").text
    assert "Nenhum lançamento com esses filtros." in filtered and "Limpar filtros" in filtered


def test_cards_page_shows_faces_gauge_ledger_and_plan_steps(
    client: TestClient, container: Container
) -> None:
    _, card = bank(client, container)
    today = dt.date.today().isoformat()
    client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": today,
            "description": "Fone",
            "amount": "300,00",
            "installments": "3",
        },
    )
    page = client.get(f"/cards?card={card}").text
    assert 'class="card-face on"' in page and "--face-bg: #0F5C45" in page
    assert re.search(r'class="sg sg-forest" role="img" aria-label="Comprometido 30,0%', page)
    assert 'class="ledger"' in page and 'class="ledger-line due"' in page
    assert 'class="plan-steps"' in page and page.count('class="next"') == 1
    assert 'class="bars-cols"' in page and "statstrip totals4" in page
    # without a limit the gauge is hatched and never reads 0%
    client.post(
        f"/cards/{card}/settings",
        data={"closing_days_before_due": "11", "due_day": "5", "limit": ""},
    )
    page = client.get(f"/cards?card={card}").text
    assert "sg-unset" in page and "Limite não informado" in page


def test_cards_empty_state(client: TestClient) -> None:
    page = client.get("/cards").text
    assert "Nenhum cartão cadastrado" in page and "Novo cartão" in page


def test_purchase_preview_is_a_receipt_with_the_limit_gauge(
    client: TestClient, container: Container
) -> None:
    _, card = bank(client, container)
    response = client.post(
        "/cards/purchase/preview",
        data={
            "account_id": card,
            "date": "2026-07-26",
            "description": "Fone",
            "amount": "300,00",
            "amount_mode": "total",
            "installments": "3",
        },
    )
    text = response.text
    assert 'class="receipt"' in text and 'data-odometer="30000"' in text
    assert 'class="sched-total"' in text and "Limite do cartão" in text
    assert (
        "--sg-add: 30.00%" in text
    )  # this purchase as the darker extra segment (R$ 300 of R$ 1.000)
    form = client.get(f"/cards/purchase?card={card}").text
    assert 'class="opt"' in form and 'name="amount_mode"' in form


def test_investments_hero_tables_and_states(client: TestClient, container: Container) -> None:
    empty = client.get("/investments").text
    assert "Nenhuma conta de investimento" in empty and 'class="hero inv-hero"' in empty
    client.post("/institutions", data={"name": "Corretora"})
    with container.uow as work:
        inst = work.institutions.list_all()[0].id
    client.post(
        "/accounts", data={"nickname": "Caixinha", "institution_id": inst, "kind": "investment"}
    )
    with container.uow as work:
        savings = work.accounts.list_all()[0].id
    client.post(f"/investments/{savings}/valuation", data={"date": "2026-01-05", "net": "1.000,00"})
    client.post(f"/investments/{savings}/valuation", data={"date": "2026-02-05", "net": "1.100,00"})
    page = client.get("/investments?year=2026").text
    assert 'data-odometer="110000"' in page  # the total, rolled by the odometer
    assert 'class="rtable"' in page and 'data-label="Valor atual"' in page
    assert (
        'class="stackbar"' in page and "dl-pos" in page
    )  # allocation in the hero, return as a chip


def test_budget_page_has_period_switch_over_chips_and_difference_bars(
    client: TestClient, container: Container
) -> None:
    checking, _ = bank(client, container)
    with container.uow as work:
        food = work.categories.get_by_slug("food")
    assert food
    client.post("/budget/goals", data={f"goal_{food.id}": "100,00"})
    today = dt.date.today()
    index = today.year * 12 + today.month - 2
    day = dt.date(index // 12, index % 12 + 1, 5)
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": checking,
            "date": day.isoformat(),
            "amount": "250,00",
            "description": "Delivery",
            "category_id": food.id,
        },
    )
    page = client.get("/budget").text
    assert 'class="seg-nav"' in page and 'class="statstrip budget-strip"' in page
    assert 'class="rtable matrix"' in page and 'class="dv"' in page and 'class="num over"' in page
    assert 'data-label="Média − meta"' in page


def test_remaining_pages_use_responsive_lists_and_states(
    client: TestClient, container: Container
) -> None:
    checking, _ = bank(client, container)
    assert 'class="rtable accounts-table"' in client.get("/accounts").text
    assert 'data-label="Orçamento mensal"' in client.get("/categories").text
    assert "Nenhuma falha registrada" in client.get("/diagnostics").text
    assert "Nenhum alerta" in client.get("/recurring").text
    assert "Nenhuma despesa recorrente nos últimos meses" in client.get("/recurring").text
    flow = client.get(f"/accounts/{checking}/flow?month=2026-07").text
    assert "Sem movimentos neste mês." in flow and 'class="statstrip totals4"' in flow
    assert "Saldo indisponível" in flow  # an account without a balance never reads zero
    more = client.get("/more").text
    assert 'class="more-row"' in more and "Diagnóstico" in more


def test_net_worth_hero_and_partial_state(client: TestClient, container: Container) -> None:
    bank(client, container)
    page = client.get("/networth").text
    assert 'class="hero nw-hero"' in page and 'class="nw-ledger"' in page
    assert "Parcial" in page and "O que falta para o total ficar completo" in page


def test_error_pages_use_the_error_state(client: TestClient) -> None:
    response = client.get("/nao-existe")
    assert response.status_code == 404
    assert 'class="state state-error"' in response.text and 'role="alert"' in response.text
    assert "Página não encontrada" in response.text
