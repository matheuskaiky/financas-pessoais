"""The web adapter end to end (synthetic data, temporary database)."""

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financas.container import Container
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings
from financas.interfaces.web.app import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
HEADERS = {"host": "localhost", "origin": "http://localhost"}


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'data' / 'f.db'}", data_dir=tmp_path / "data", _env_file=None
    )  # type: ignore[call-arg]
    c = Container(settings)
    c.migrate()
    seed_categories(c.uow)
    return c


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(
        create_app(container), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )


def setup_accounts(client: TestClient, container: Container) -> tuple[str, str]:
    assert client.post("/institutions", data={"name": "Banco do Brasil"}).status_code == 303
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    client.post("/accounts", data={"nickname": "Conta Corrente", "institution_id": inst.id})
    client.post(
        "/accounts", data={"nickname": "Caixinha", "institution_id": inst.id, "kind": "investment"}
    )
    with container.uow as work:
        by_name = {a.nickname: a.id for a in work.accounts.list_all()}
    return by_name["Conta Corrente"], by_name["Caixinha"]


def test_pages_render_in_portuguese(client: TestClient) -> None:
    for path, text in [
        ("/", "Taxa de poupança"),
        ("/entries", "Novo lançamento"),
        ("/accounts", "Instituições"),
        ("/categories", "Não categorizado"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert text in response.text
        assert '<html lang="pt-BR">' in response.text
        assert '<meta charset="utf-8">' in response.text


def test_security_headers_and_local_only_static_assets(client: TestClient) -> None:
    response = client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "http://" not in response.text.replace("http://localhost", "")
    assert "https://" not in response.text
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/chart.umd.min.js").status_code == 200


def test_rejects_foreign_host_and_cross_origin_posts(client: TestClient) -> None:
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400
    bad = client.post(
        "/institutions", data={"name": "X"}, headers={"origin": "http://evil.example"}
    )
    assert bad.status_code == 403
    assert (
        client.post("/institutions", data={"name": "X"}, headers={"sec-fetch-site": "cross-site"})
    ).status_code == 403


def test_entry_flow_and_dashboard(client: TestClient, container: Container) -> None:
    checking, savings = setup_accounts(client, container)
    today = dt.date.today().isoformat()
    response = client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": checking,
            "date": today,
            "amount": "1.234,56",
            "description": "Café São João",
            "recurring": "1",
        },
    )
    assert response.status_code == 303 and "ok=entry" in response.headers["location"]
    client.post(
        "/entries",
        data={
            "kind": "income",
            "account_id": checking,
            "date": today,
            "amount": "5000",
            "description": "Salário",
        },
    )
    client.post(
        "/transfers",
        data={"from_account": checking, "to_account": savings, "date": today, "amount": "100"},
    )
    entries = client.get("/entries?ok=entry")
    assert "Café São João" in entries.text and "Lançamento salvo." in entries.text
    assert "− R$ 1.234,56" in entries.text
    home = client.get("/")
    assert "R$ 5.000,00" in home.text and "R$ 1.234,56" in home.text
    assert "Recorrentes e fixos" in home.text and "Café São João" in home.text
    assert "Nenhuma despesa recorrente" not in home.text


def test_errors_are_rendered_in_portuguese_and_keep_the_form(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    response = client.post(
        "/entries",
        data={
            "kind": "income",
            "account_id": checking,
            "date": "2026-07-01",
            "amount": "abc",
            "description": "Teste",
        },
    )
    assert response.status_code == 400
    assert "Valor inválido" in response.text and 'value="Teste"' in response.text


def test_category_field_suggests_the_last_category(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    with container.uow as work:
        food = work.categories.get_by_slug("food")
    assert food
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": checking,
            "date": "2026-07-01",
            "amount": "10",
            "description": "Padaria",
            "category_id": food.id,
        },
    )
    response = client.get(
        "/entries/category-field", params={"kind": "expense", "description": "PADARIA"}
    )
    assert f'value="{food.id}" selected' in response.text and "sugerida" in response.text
    income = client.get(
        "/entries/category-field", params={"kind": "income", "description": "Padaria"}
    )
    assert "Salário" in income.text and "Alimentação" not in income.text
    assert client.get("/entries/category-field", params={"kind": "transfer"}).status_code == 400


def test_delete_entry_via_htmx(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": checking,
            "date": "2026-07-01",
            "amount": "5",
            "description": "x",
        },
    )
    with container.uow as work:
        (row,) = work.transactions.list_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    response = client.post(f"/entries/{row.id}/delete", headers={"hx-request": "true"})
    assert response.status_code == 200 and response.headers["hx-refresh"] == "true"
    with container.uow as work:
        assert work.transactions.get(row.id) is None


def test_balance_shows_the_difference(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    first = client.post(
        f"/accounts/{checking}/balance", data={"date": "2026-07-01", "amount": "1000"}
    )
    assert first.headers["location"] == "/accounts?ok=balance"
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": checking,
            "date": "2026-07-05",
            "amount": "100",
            "description": "x",
        },
    )
    second = client.post(
        f"/accounts/{checking}/balance", data={"date": "2026-07-10", "amount": "850"}
    )
    page = client.get(second.headers["location"])
    assert "O sistema esperava R$ 900,00" in page.text and "-R$ 50,00" in page.text
    assert "Saldo indisponível" in page.text  # the savings account has no anchor


def test_appearance_upload_serving_and_rules(client: TestClient, container: Container) -> None:
    client.post(
        "/institutions",
        data={"name": "Nubank", "color": "#820ad1", "use_color": "1"},
        files={"image": ("logo.png", PNG, "image/png")},
    )
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    assert inst.color == "#820AD1" and inst.image_id
    image = client.get(f"/images/{inst.image_id}")
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert f"/images/{inst.image_id}" in client.get("/accounts").text
    assert client.get("/images/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/images/" + "a" * 32).status_code == 404
    svg = client.post(
        f"/appearance/institution/{inst.id}",
        data={"color": "#112233", "use_color": "1"},
        files={"image": ("x.png", b"<svg onload=alert(1)/>", "image/png")},
    )
    assert svg.status_code == 400 and "Formato de imagem não permitido" in svg.text
    big = client.post(
        f"/appearance/institution/{inst.id}",
        data={},
        files={"image": ("x.png", PNG + b"\x00" * (600 * 1024), "image/png")},
    )
    assert big.status_code == 400 and "grande demais" in big.text
    removed = client.post(f"/appearance/institution/{inst.id}", data={"remove_image": "1"})
    assert removed.status_code == 303
    assert client.get(f"/images/{inst.image_id}").status_code == 404


def test_category_color_and_creation(client: TestClient, container: Container) -> None:
    with container.uow as work:
        food = work.categories.get_by_slug("food")
    assert food
    client.post(f"/appearance/category/{food.id}", data={"color": "#aa0000", "use_color": "1"})
    with container.uow as work:
        updated = work.categories.get(food.id)
    assert updated and updated.color == "#AA0000"
    bad = client.post("/categories", data={"name": "Pets", "group": "income", "kind": "expense"})
    assert bad.status_code == 400 and "não combina" in bad.text
    ok = client.post(
        "/categories",
        data={"name": "Pets", "group": "non_essential", "kind": "expense", "budget": "300,00"},
    )
    assert ok.status_code == 303
    assert "Pets" in client.get("/categories").text


def test_backup_button_and_notice(client: TestClient, container: Container) -> None:
    assert "ainda não fez backup" in client.get("/").text
    assert client.post("/backup").headers["location"] == "/?ok=backup"
    assert "ainda não fez backup" not in client.get("/").text


def test_entries_filters_search_and_load_more(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    base = {"account_id": checking, "date": "2026-07-10", "amount": "10"}
    client.post("/entries", data={**base, "kind": "expense", "description": "Café São João"})
    client.post("/entries", data={**base, "kind": "income", "description": "Salário"})
    page = client.get("/entries?month=2026-07&q=cafe%20sao")  # no accents, other case
    assert "<span>Café São João</span>" in page.text
    assert "<span>Salário</span>" not in page.text  # (the category menu also lists "Salário")
    by_kind = client.get("/entries?month=2026-07&kind=income")
    assert "<span>Salário</span>" in by_kind.text
    assert "<span>Café São João</span>" not in by_kind.text
    none = client.get("/entries?month=2026-07&q=inexistente")
    assert "Nenhum lançamento com esses filtros" in none.text
    assert "Mostrando 2 de 2 lançamentos do mês" in client.get("/entries?month=2026-07").text
    assert "Carregar mais" not in client.get("/entries?month=2026-07").text


def test_unified_quick_form_creates_transfers(client: TestClient, container: Container) -> None:
    checking, savings = setup_accounts(client, container)
    response = client.post(
        "/entries",
        data={
            "kind": "transfer",
            "from_account": checking,
            "to_account": savings,
            "date": "2026-07-10",
            "amount": "250,00",
        },
    )
    assert response.status_code == 303 and "ok=transfer" in response.headers["location"]
    with container.uow as work:
        legs = work.transactions.list_between(dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert sorted(t.amount_cents for t in legs) == [-25_000, 25_000]


def test_design_shell_and_font_are_served_locally(client: TestClient) -> None:
    home = client.get("/")
    assert 'class="sidebar"' in home.text and 'class="tabbar"' in home.text
    assert "Dados só neste computador" in home.text and "Nenhum backup ainda" in home.text
    css = client.get("/static/app.css")
    assert "IBM Plex Sans" in css.text and "fonts.googleapis" not in css.text
    font = client.get("/static/fonts/IBMPlexSans-latin.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    assert client.get("/static/..%2f..%2fapp.py").status_code in {400, 404}


# --- cards (Phase 2) -----------------------------------------------------------------------------


def make_card(client: TestClient, container: Container, **extra: str) -> tuple[str, str]:
    """A checking account and a card (closing day 25, due day 5, limit R$ 12.000,00)."""
    checking, _ = setup_accounts(client, container)
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    data = {
        "nickname": "Nubank",
        "institution_id": inst.id,
        "closing_day": "25",
        "due_day": "5",
        "limit": "12.000,00",
        **extra,
    }
    assert client.post("/cards", data=data).status_code == 303
    with container.uow as work:
        card = next(a for a in work.accounts.list_all() if a.nickname == "Nubank")
    return checking, card.id


def test_cards_page_empty_state_and_navigation(client: TestClient) -> None:
    page = client.get("/cards")
    assert page.status_code == 200 and "Novo cartão" in page.text
    assert 'href="/cards"' in page.text and "Cartões" in page.text


def test_new_card_with_invalid_days_shows_a_portuguese_error(
    client: TestClient, container: Container
) -> None:
    setup_accounts(client, container)
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    bad = client.post(
        "/cards",
        data={"nickname": "X", "institution_id": inst.id, "closing_day": "40", "due_day": "5"},
    )
    assert bad.status_code == 400 and "Dia inválido" in bad.text


def test_purchase_preview_explains_the_statement_and_shows_the_schedule(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    response = client.post(
        "/cards/purchase/preview",
        data={
            "account_id": card,
            "date": "2026-07-26",
            "description": "Fone",
            "amount": "301,00",
            "amount_mode": "total",
            "installments": "3",
        },
    )
    text = response.text
    assert "Compra em 26/07/2026, depois do fechamento do dia 25" in text
    assert "fatura de ago/2026 (fecha 25/08 · vence 05/09)" in text
    assert "R$ 100,34" in text and "R$ 100,33" in text and "R$ 301,00" in text
    assert "Melhor dia de compra neste cartão: dia 26" in text
    with container.uow as work:
        assert work.transactions.list_by_account(card) == []  # a preview writes nothing


def test_purchase_preview_problems_are_shown_not_raised(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    response = client.post(
        "/cards/purchase/preview",
        data={"account_id": card, "date": "2026-07-26", "amount": "", "installments": "3"},
    )
    assert response.status_code == 200 and "Informe o valor" in response.text
    garbage = client.post("/cards/purchase/preview", data={"account_id": card, "installments": "x"})
    assert garbage.status_code == 200 and "Confira os campos" in garbage.text


def test_purchase_save_creates_every_installment_and_the_statement_page(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    response = client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": "2026-07-26",
            "description": "Fone de ouvido",
            "amount": "301,00",
            "amount_mode": "total",
            "installments": "3",
        },
    )
    assert response.status_code == 303
    assert (
        "month=2026-08" in response.headers["location"]
        and "ok=purchase" in response.headers["location"]
    )
    page = client.get(response.headers["location"])
    assert (
        "Compra salva" in page.text and "Fatura ago/2026 · fecha 25/08 · vence 05/09" in page.text
    )
    assert "Fone de ouvido" in page.text and "1/3" in page.text and "R$ 100,34" in page.text
    assert "Parcelas ativas" in page.text and "Parcelas por mês" in page.text
    with container.uow as work:
        assert len(work.transactions.list_by_account(card)) == 3


def test_purchase_errors_keep_the_form(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container)
    response = client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": "2026-07-26",
            "description": "Teste",
            "amount": "10",
            "installments": "3",
            "current_installment": "3",
        },
    )
    assert response.status_code == 400 and "informe a fatura da parcela atual" in response.text
    assert 'value="Teste"' in response.text


def test_running_purchase_with_an_explicit_statement(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    response = client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "description": "Geladeira",
            "amount": "61,88",
            "amount_mode": "installment",
            "installments": "10",
            "current_installment": "3",
            "statement_month": "2026-09",
        },
    )
    assert response.status_code == 303 and "month=2026-09" in response.headers["location"]
    with container.uow as work:
        rows = work.transactions.list_by_account(card)
    assert len(rows) == 8 and sorted(t.installment_number or 0 for t in rows) == list(range(3, 11))


def test_statement_payment_reconciliation_and_dates(
    client: TestClient, container: Container
) -> None:
    checking, card = make_card(client, container)
    client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": "2026-07-20",
            "description": "Mercado",
            "amount": "100,00",
        },
    )
    with container.uow as work:
        statement = work.statements.list_for_card(card)[0]
    sid = statement.id
    assert (
        "Total informado"
        in client.get(
            client.post(f"/statements/{sid}/informed", data={"amount": "103,80"}).headers[
                "location"
            ]
        ).text
    )
    page = client.get(f"/cards?card={card}&month=2026-07")
    assert "Conferência com o banco" in page.text and "R$ 103,80" in page.text
    assert "Lançar diferença como “Não categorizado”" in page.text
    client.post(f"/statements/{sid}/difference")
    with container.uow as work:
        assert len(work.transactions.list_by_statement(sid)) == 2
    paid = client.post(
        f"/statements/{sid}/pay",
        data={"from_account": checking, "date": "2026-08-01", "amount": "50,00"},
    )
    assert paid.status_code == 303 and "ok=payment" in paid.headers["location"]
    assert "R$ 53,80" in client.get(paid.headers["location"]).text  # still to pay
    moved = client.post(
        f"/statements/{sid}/dates", data={"closing": "2026-07-24", "due": "2026-08-06"}
    )
    assert moved.status_code == 303
    bad = client.post(
        f"/statements/{sid}/dates", data={"closing": "2026-07-24", "due": "2026-07-24"}
    )
    assert bad.status_code == 400 and "O vencimento precisa ser depois do fechamento" in bad.text


def test_adjust_and_delete_a_purchase(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container)
    client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": "2099-01-26",  # far future: nothing is paid
            "description": "Fone",
            "amount": "300,00",
            "installments": "3",
        },
    )
    with container.uow as work:
        entries = work.transactions.list_by_account(card)
        plan = work.plans.list_all()[0]
    target = next(t for t in entries if t.installment_number == 2)
    adjusted = client.post(f"/entries/{target.id}/amount", data={"amount": "99,00"})
    assert adjusted.status_code == 303
    with container.uow as work:
        assert work.transactions.get(target.id).amount_cents == -9_900  # type: ignore[union-attr]
    assert client.post(f"/plans/{plan.id}/delete").status_code == 303
    with container.uow as work:
        assert work.transactions.list_by_account(card) == [] and work.plans.list_all() == []


def test_cards_overview_limit_and_dashboard(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container, limit="1.000,00")
    today = dt.date.today()
    client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": today.isoformat(),
            "description": "Notebook",
            "amount": "850,00",
            "installments": "1",
        },
    )
    page = client.get(f"/cards?card={card}")
    assert "Atenção · 80%+" in page.text and "Comprometido R$ 850,00" in page.text
    assert "Limite R$ 1.000,00" in page.text
    home = client.get("/")
    assert "Compras do mês" in home.text and "Notebook" in home.text
    assert "do limite comprometido" in home.text  # the attention panel
    assert "R$ 850,00" in home.text  # card expense counts in its statement month
    client.post(f"/cards/{card}/settings", data={"closing_day": "10", "due_day": "17", "limit": ""})
    assert "Limite não informado" in client.get(f"/cards?card={card}").text


def test_card_entries_through_the_quick_form_and_accounts_page(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": card,
            "date": "2026-07-26",
            "amount": "45,90",
            "description": "Padaria",
        },
    )
    with container.uow as work:
        (row,) = work.transactions.list_by_account(card)
    assert row.statement_id is not None
    income = client.post(
        "/entries",
        data={
            "kind": "income",
            "account_id": card,
            "date": "2026-07-26",
            "amount": "5",
            "description": "x",
        },
    )
    assert income.status_code == 400 and "não aceita esse lançamento" in income.text
    accounts = client.get("/accounts")
    assert (
        "Conta Corrente" in accounts.text
        and "Nubank" not in accounts.text.split("Contas e investimentos")[1].split("Nova conta")[0]
    )
    entries = client.get("/entries?month=2026-07")
    assert "Nubank · cartão" in entries.text


def test_cards_pass_the_csp_and_have_no_inline_handlers(
    client: TestClient, container: Container
) -> None:
    make_card(client, container)
    for path in ("/", "/entries", "/accounts", "/categories", "/cards", "/cards/purchase"):
        text = client.get(path).text
        assert "onchange=" not in text and "onclick=" not in text and "onsubmit=" not in text, path
        assert "<script>" not in text, path
    assert client.get("/static/app.js").status_code == 200


# --- investments and net worth (Phase 3a) --------------------------------------------------------


def make_investment(client: TestClient, container: Container) -> tuple[str, str]:
    checking, savings = setup_accounts(client, container)  # savings is an investment account
    return checking, savings


def test_investments_page_empty_and_navigation(client: TestClient) -> None:
    page = client.get("/investments")
    assert page.status_code == 200 and "Nenhuma conta de investimento" in page.text
    assert "o sistema não calcula imposto" in page.text
    home = client.get("/")
    assert 'href="/investments"' in home.text and 'href="/networth"' in home.text
    assert 'href="/more"' in home.text and client.get("/more").status_code == 200


def test_valuation_flow_yield_and_year_position(client: TestClient, container: Container) -> None:
    checking, savings = make_investment(client, container)
    first = client.post(
        f"/investments/{savings}/valuation",
        data={"date": "2025-12-31", "net": "10.000,00", "gross": "10.200,00"},
    )
    assert first.status_code == 303 and first.headers["location"] == "/investments?ok=valuation"
    flow = client.post(
        "/investments/flow",
        data={
            "investment_account_id": savings,
            "direction": "contribution",
            "other_account_id": checking,
            "amount": "500,00",
            "date": "2026-03-15",
        },
    )
    assert flow.status_code == 303 and "ok=flow" in flow.headers["location"]
    second = client.post(
        f"/investments/{savings}/valuation", data={"date": "2026-06-30", "net": "10.700,00"}
    )
    assert "ok=valuation_yield" in second.headers["location"]
    notice = client.get(second.headers["location"])
    assert "Rendimento desde a avaliação anterior" in notice.text and "+ R$ 200,00" in notice.text
    assert "não é renda" in notice.text
    page = client.get("/investments?year=2026")
    assert "R$ 10.700,00" in page.text  # current value
    assert "Aportes líquidos em 2026" in page.text and "R$ 500,00" in page.text
    assert "Rendimento capitalizado em 2026" in page.text and "retorno simples" in page.text
    assert "Posição em 31/12/2026" in page.text
    assert 'value="other" selected' in page.text  # default class of a new investment account
    with container.uow as work:
        anchors = work.anchors.list_for_account(savings)
    assert [a.gross_balance_cents for a in anchors] == [1_020_000, None]


def test_investment_errors_in_portuguese(client: TestClient, container: Container) -> None:
    checking, savings = make_investment(client, container)
    bad_gross = client.post(
        f"/investments/{savings}/valuation",
        data={"date": "2026-07-01", "net": "100", "gross": "90"},
    )
    assert bad_gross.status_code == 400 and "valor bruto não pode ser menor" in bad_gross.text
    wrong = client.post(
        "/investments/flow",
        data={
            "investment_account_id": checking,
            "direction": "contribution",
            "amount": "10",
            "date": "2026-07-01",
        },
    )
    assert wrong.status_code == 400 and "exige uma conta de investimento" in wrong.text


def test_investment_settings_and_stale_flag(client: TestClient, container: Container) -> None:
    _, savings = make_investment(client, container)
    old = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    client.post(f"/investments/{savings}/valuation", data={"date": old, "net": "1.000,00"})
    client.post(
        f"/investments/{savings}/settings", data={"asset_class": "fixed_income", "emergency": "1"}
    )
    page = client.get("/investments")
    assert "Desatualizada · 60 dias" in page.text
    assert 'value="fixed_income" selected' in page.text and "· reserva" in page.text
    with container.uow as work:
        account = work.accounts.get(savings)
    assert account and account.is_emergency_fund and account.asset_class.value == "fixed_income"  # type: ignore[union-attr]


def test_net_worth_page_is_partial_until_everything_is_informed(
    client: TestClient, container: Container
) -> None:
    checking, savings = make_investment(client, container)
    page = client.get("/networth")
    assert (
        "Parcial · 2 pendente(s)" in page.text
        and "O que falta para o total ficar completo" in page.text
    )
    client.post(f"/accounts/{checking}/balance", data={"date": "2026-01-01", "amount": "1.000,00"})
    client.post(f"/investments/{savings}/valuation", data={"date": "2026-01-01", "net": "9.000,00"})
    page = client.get("/networth")
    assert "R$ 10.000,00" in page.text and "Parcial" not in page.text
    home = client.get("/")
    assert "Patrimônio líquido" in home.text and "R$ 10.000,00" in home.text
    assert "Aportes líquidos" in home.text


def test_net_worth_subtracts_card_statements(client: TestClient, container: Container) -> None:
    checking, card = make_card(client, container)
    client.post(f"/accounts/{checking}/balance", data={"date": "2026-01-01", "amount": "5.000,00"})
    client.post(
        "/cards/purchase",
        data={"account_id": card, "date": "2026-02-10", "description": "TV", "amount": "1.200,00"},
    )
    page = client.get("/networth")
    assert "R$ 3.800,00" in page.text  # 5.000 - 1.200 (closed statement, long past)
    assert "Faturas fechadas a pagar" in page.text and "− R$ 1.200,00" in page.text


# --- fixed-income holdings (Phase 3b) ------------------------------------------------------------


def make_broker(client: TestClient, container: Container) -> tuple[str, str, str]:
    """A checking account, a holdings-level investment account and the issuer institution."""
    checking, savings = setup_accounts(client, container)
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    switched = client.post(
        f"/investments/{savings}/settings",
        data={"asset_class": "fixed_income", "tracking": "holdings"},
    )
    assert switched.status_code == 303
    return checking, savings, inst.id


def new_holding(client: TestClient, account: str, issuer: str, **extra: str) -> object:
    data = {
        "account_id": account,
        "name": "CDB Banco X 110% CDI",
        "instrument_type": "cdb",
        "issuer_id": issuer,
        "applied_on": "2026-03-01",
        "principal": "10.000,00",
        "liquidity": "at_maturity",
        "maturity_on": "2028-03-01",
        "indexer": "cdi",
        "rate_mode": "percent_of_index",
        "rate": "110",
        **extra,
    }
    return client.post("/investments/holdings", data=data)


def test_holdings_page_guides_the_switch_and_creates_a_holding(
    client: TestClient, container: Container
) -> None:
    _, savings = setup_accounts(client, container)
    assert "Por aplicação" in client.get("/investments").text  # the switch is in the settings
    with container.uow as work:
        issuer = work.institutions.list_all()[0].id
        checking = next(a.id for a in work.accounts.list_all() if a.nickname == "Conta Corrente")
    client.post(
        f"/investments/{savings}/settings",
        data={"asset_class": "fixed_income", "tracking": "holdings"},
    )
    response = new_holding(client, savings, issuer, contribute="1", from_account_id=checking)
    assert response.status_code == 303 and "ok=holding" in response.headers["location"]  # type: ignore[attr-defined]
    page = client.get("/investments")
    assert "CDB Banco X 110% CDI" in page.text and "110% do CDI" in page.text
    assert "01/03/2028" in page.text and "No vencimento" in page.text
    assert "Sem avaliação" in page.text  # nothing valued yet
    with container.uow as work:
        (holding,) = work.holdings.list_all()
        legs = work.transactions.list_by_account(savings)
    assert holding.fgc_covered is True and [leg.holding_id for leg in legs] == [holding.id]


def test_holding_errors_in_portuguese(client: TestClient, container: Container) -> None:
    _, savings, issuer = make_broker(client, container)
    for extra, text in [
        ({"maturity_on": ""}, "Informe o vencimento"),
        ({"rate": ""}, "Taxa inválida"),
        ({"maturity_on": "2026-01-01"}, "depois da data de aplicação"),
        ({"principal": "0"}, "maior que zero"),
    ]:
        bad = new_holding(client, savings, issuer, **extra)
        assert bad.status_code == 400 and text in bad.text, text  # type: ignore[attr-defined]


def test_holding_valuation_flow_flags_and_redemption(
    client: TestClient, container: Container
) -> None:
    checking, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    with container.uow as work:
        (holding,) = work.holdings.list_all()
    first = client.post(
        f"/investments/holdings/{holding.id}/valuation",
        data={"date": "2026-09-30", "net": "10.500,00", "gross": "10.700,00"},
    )
    assert "ok=valuation" in first.headers["location"]
    flow = client.post(
        "/investments/flow",
        data={
            "target": f"h:{holding.id}",
            "direction": "contribution",
            "other_account_id": checking,
            "amount": "500,00",
            "date": "2026-10-01",
        },
    )
    assert flow.status_code == 303
    second = client.post(
        f"/investments/holdings/{holding.id}/valuation",
        data={"date": "2026-10-31", "net": "11.100,00"},
    )
    assert "ok=valuation_yield" in second.headers["location"]
    assert "+ R$ 100,00" in client.get(second.headers["location"]).text
    page = client.get("/investments")
    assert "R$ 11.100,00" in page.text  # holdings-level account value = sum of holdings
    blocked = client.post(
        f"/investments/{savings}/valuation", data={"date": "2026-10-31", "net": "1,00"}
    )
    assert blocked.status_code == 400 and "controlada por aplicação" in blocked.text
    assert (
        client.post(
            f"/investments/holdings/{holding.id}/flags", data={"emergency": "1"}
        ).status_code
        == 303
    )
    with container.uow as work:
        got = work.holdings.get(holding.id)
    assert got and got.fgc_covered is False and got.is_emergency_fund is True
    redeemed = client.post(
        f"/investments/holdings/{holding.id}/redeem",
        data={"date": "2027-03-01", "amount": "11.800,00", "to_account_id": checking},
    )
    assert redeemed.status_code == 303 and "ok=redeemed" in redeemed.headers["location"]
    page = client.get("/investments")
    assert "Resgatada" in page.text
    again = client.post(
        f"/investments/holdings/{holding.id}/redeem", data={"date": "2027-03-02", "amount": "1"}
    )
    assert again.status_code == 400 and "já foi resgatada" in again.text


def test_brazilian_views_on_the_page(client: TestClient, container: Container) -> None:
    checking, savings, issuer = make_broker(client, container)
    client.post(f"/accounts/{checking}/balance", data={"date": "2026-09-01", "amount": "1.320,00"})
    new_holding(client, savings, issuer, name="CDB reserva", emergency="1")
    new_holding(
        client,
        savings,
        issuer,
        name="Tesouro IPCA",
        instrument_type="treasury_ipca",
        liquidity="daily",
        maturity_on="2035-03-01",
        indexer="ipca",
        rate_mode="spread_over_index",
        rate="6,5",
    )
    with container.uow as work:
        holdings = {h.name: h for h in work.holdings.list_all()}
    for name, net in (("CDB reserva", "20.000,00"), ("Tesouro IPCA", "5.000,00")):
        client.post(
            f"/investments/holdings/{holdings[name].id}/valuation",
            data={"date": "2026-09-30", "net": net},
        )
    page = client.get("/investments")
    assert (
        "Escada de vencimentos" in page.text and "mar/2028" in page.text and "mar/2035" in page.text
    )
    assert "não projeta valor no vencimento" in page.text
    assert "Disponível hoje" in page.text and "Mais tarde" in page.text
    assert "Exposição ao FGC por grupo" in page.text and "fgc.org.br" in page.text
    assert "R$ 21.320,00" in page.text  # the covered CDB (the Treasury is not covered) + checking
    assert "IPCA + 6,50%" in page.text
    assert "R$ 20.000,00 marcados como reserva" in page.text
    assert "Sem gasto essencial nos últimos 3 meses fechados" in page.text


def test_net_worth_lists_holdings_without_valuation(
    client: TestClient, container: Container
) -> None:
    _, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    page = client.get("/networth")
    assert "CDB Banco X 110% CDI" in page.text and "Informar avaliação" in page.text
    assert "Parcial" in page.text
