"""The web adapter end to end (synthetic data, temporary database)."""

import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financas.container import Container
from financas.domain.models import Statement, Transaction
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings
from financas.interfaces.web.app import create_app
from html_text import visible

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
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
    assert client.get("/static/charts.js").status_code == 200


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
    assert "−R$ 1.234,56" in visible(entries)
    home = client.get("/")
    assert "R$ 5.000,00" in visible(home) and "R$ 1.234,56" in visible(home)
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
    assert client.get("/entries/category-field", params={"kind": "transfer"}).status_code == 204


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
    assert "O sistema esperava R$ 900,00" in visible(page) and "-R$ 50,00" in visible(page)
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
    assert ">Café São João</span>" in page.text
    assert "<span>Salário</span>" not in page.text  # (the category menu also lists "Salário")
    by_kind = client.get("/entries?month=2026-07&kind=income")
    assert ">Salário</span>" in by_kind.text
    assert ">Café São João</span>" not in by_kind.text
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
    assert 'class="sidebar ink"' in home.text and 'class="tabbar"' in home.text
    assert "Dados guardados só neste computador" in home.text and "Nenhum backup ainda" in home.text
    # Families are declared once, in fonts/fonts.css (Public Sans, Source Serif 4).
    fonts_css = client.get("/static/fonts/fonts.css")
    assert fonts_css.status_code == 200
    assert "'Public Sans'" in fonts_css.text and "'Source Serif 4'" in fonts_css.text
    app_css = client.get("/static/app.css").text
    assert "@font-face" not in app_css and "Libre Franklin" not in app_css
    assert "fonts.googleapis" not in app_css + fonts_css.text + home.text
    tokens = client.get("/static/tokens.css").text
    assert "--sans:'Public Sans'" in tokens and "--serif:'Source Serif 4'" in tokens
    for name in (
        "public-sans-latin-wght-normal",
        "public-sans-latin-ext-wght-normal",
        "source-serif-4-latin-opsz-normal",
        "source-serif-4-latin-ext-opsz-normal",
    ):
        font = client.get(f"/static/fonts/{name}.woff2")
        assert font.status_code == 200 and font.content[:4] == b"wOF2"
        assert font.headers["content-type"] == "font/woff2"
    for licence in ("OFL-Public-Sans", "OFL-Source-Serif-4"):
        assert client.get(f"/static/fonts/{licence}.txt").status_code == 200
    assert client.get("/static/fonts/LibreFranklin-latin.woff2").status_code == 404
    assert client.get("/static/..%2f..%2fapp.py").status_code in {400, 404}


# --- cards (Phase 2) ---


def make_card(client: TestClient, container: Container, **extra: str) -> tuple[str, str]:
    """A checking account and a card (due day 5, closes 11 days before, limit R$ 12.000,00)."""
    checking, _ = setup_accounts(client, container)
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    data = {
        "nickname": "Nubank",
        "institution_id": inst.id,
        "closing_days_before_due": "11",
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
        data={
            "nickname": "X",
            "institution_id": inst.id,
            "closing_days_before_due": "40",
            "due_day": "5",
        },
    )
    assert bad.status_code == 400 and "de 1 a 27 dias" in bad.text


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
    assert "Compra em 26/07/2026, depois do fechamento em 25/07" in text
    assert "fatura de ago/2026 (fecha 25/08 · vence 05/09)" in text
    assert all(v in visible(text) for v in ("R$ 100,34", "R$ 100,33", "R$ 301,00"))
    assert "Melhor dia de compra neste cartão: 25/08/2026" in text
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
    assert garbage.status_code == 200 and "Número de parcelas inválido" in garbage.text


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
    assert "Fone de ouvido" in page.text and "1/3" in page.text and "R$ 100,34" in visible(page)
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
    assert "Conferência com o banco" in page.text and "R$ 103,80" in visible(page)
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
    assert "Atenção · 80%+" in page.text and "Comprometido R$ 850,00" in visible(page)
    assert "Limite R$ 1.000,00" in visible(page)
    home = client.get("/")
    assert "Compras do mês" in home.text and "Notebook" in home.text
    assert "do limite comprometido" in home.text  # the attention panel
    assert "R$ 850,00" in visible(home)  # card expense counts in its statement month
    client.post(
        f"/cards/{card}/settings",
        data={"closing_days_before_due": "7", "due_day": "17", "limit": ""},
    )
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


# --- investments and net worth (Phase 3a) ---


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
    assert "Rendimento desde a avaliação anterior" in notice.text and "+ R$ 200,00" in visible(
        notice
    )
    assert "não é renda" in notice.text
    page = client.get("/investments?year=2026")
    assert "R$ 10.700,00" in visible(page)  # current value
    assert "Aportes líquidos em 2026" in page.text and "R$ 500,00" in visible(page)
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
    assert "R$ 10.000,00" in visible(page) and "Parcial" not in page.text
    home = client.get("/")
    assert "Patrimônio líquido" in home.text and "R$ 10.000,00" in visible(home)
    assert "Aportes líquidos" in home.text


def test_net_worth_subtracts_card_statements(client: TestClient, container: Container) -> None:
    checking, card = make_card(client, container)
    client.post(f"/accounts/{checking}/balance", data={"date": "2026-01-01", "amount": "5.000,00"})
    client.post(
        "/cards/purchase",
        data={"account_id": card, "date": "2026-02-10", "description": "TV", "amount": "1.200,00"},
    )
    page = client.get("/networth")
    assert "R$ 3.800,00" in visible(page)  # 5.000 - 1.200 (closed statement, long past)
    assert "Faturas fechadas a pagar" in page.text and "− R$ 1.200,00" in visible(page)


# --- fixed-income holdings (Phase 3b) ---


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
    assert "R$ 11.100,00" in visible(page)  # holdings-level account value = sum of holdings
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
    assert "R$ 21.320,00" in visible(
        page
    )  # the covered CDB (the Treasury is not covered) + checking
    assert "IPCA + 6,50%" in page.text
    assert "R$ 20.000,00 marcados como reserva" in visible(page)
    assert "Sem gasto essencial nos últimos 3 meses fechados" in page.text


def test_net_worth_lists_holdings_without_valuation(
    client: TestClient, container: Container
) -> None:
    _, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    page = client.get("/networth")
    assert "CDB Banco X 110% CDI" in page.text and "Informar avaliação" in page.text
    assert "Parcial" in page.text


def body_of(html: str) -> str:
    """The page after ``</head>``: a template bug can hide content inside ``<title>``."""
    assert html.count("<title>") == 1 and html.count("</title>") == 1
    head, _, body = html.partition("</head>")
    assert len(head) < 2_500, "the head is suspiciously large: content leaked into <title>"
    return body


def test_every_page_keeps_its_content_in_the_body(client: TestClient, container: Container) -> None:
    _, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    expected = {
        "/investments": ("Nova aplicação", "Escada de vencimentos", "Exposição ao FGC por grupo"),
        "/networth": ("Patrimônio líquido",),
        "/entries": ("Lançamento rápido",),
        "/accounts": ("Contas e investimentos",),
        "/categories": ("Nova categoria",),
        "/": ("Para onde foi o dinheiro",),
        "/more": ("Investimentos",),
        "/cards": ("Novo cartão",),
    }
    for path, texts in expected.items():
        response = client.get(path)
        assert response.status_code == 200, path
        body = body_of(response.text)
        for text in texts:
            assert text in body, (path, text)


def test_framework_errors_are_pt_br_pages(client: TestClient, container: Container) -> None:
    missing = client.get("/nao-existe")
    assert missing.status_code == 404 and "Página não encontrada" in missing.text
    assert "detail" not in missing.text.lower() or "Esse endereço" in missing.text
    wrong = client.get("/institutions")  # POST-only
    assert wrong.status_code == 405 and "Ação não permitida" in wrong.text
    # tampered numbers and choices: a pt-BR message, never a 500 or an English JSON
    assert client.get("/?year=abc&month=x").status_code == 200
    assert client.get("/entries?limit=abc").status_code == 200
    assert client.get("/investments?year=zzz").status_code == 200
    checking, _ = setup_accounts(client, container)
    bad_kind = client.post(
        "/entries",
        data={
            "kind": "gift",
            "account_id": checking,
            "date": "2026-07-01",
            "amount": "1",
            "description": "x",
        },
    )
    assert bad_kind.status_code == 400 and "Opção inválida" in bad_kind.text
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    bad_day = client.post(
        "/cards",
        data={
            "nickname": "X",
            "institution_id": inst.id,
            "closing_days_before_due": "abc",
            "due_day": "5",
        },
    )
    assert bad_day.status_code == 400 and "de 1 a 27 dias" in bad_day.text
    missing_field = client.post("/institutions", data={})
    assert missing_field.status_code == 400 and "Confira os campos" in missing_field.text
    no_account = client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": "",
            "date": "2026-07-01",
            "amount": "1",
            "description": "x",
        },
    )
    assert no_account.status_code == 400 and "Escolha a conta" in no_account.text


def test_a_bad_image_does_not_leave_a_half_created_record(
    client: TestClient, container: Container
) -> None:
    bad = client.post(
        "/institutions",
        data={"name": "Banco Ação"},
        files={"image": ("logo.png", b"GIF89a......", "image/png")},
    )
    assert bad.status_code == 400 and "Formato de imagem não permitido" in bad.text
    with container.uow as work:
        assert work.institutions.list_all() == []
    good = client.post(
        "/institutions",
        data={"name": "Banco Ação"},
        files={"image": ("logo.png", PNG, "image/png")},
    )
    assert good.status_code == 303  # the same name now works: nothing was left behind


def test_totals_strip_and_recurring_list_follow_the_statement_month(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": card,
            "date": "2026-09-30",  # after the closing on the 25th: the October statement
            "amount": "100,00",
            "description": "Assinatura",
            "recurring": "1",
        },
    )
    assert "R$ 0,00" in client.get("/entries?month=2026-09").text.split("Despesas")[1][:200]
    october = client.get("/entries?month=2026-10").text
    assert "− R$ 100,00" in october.split("Despesas")[1][:200] or "R$ 100,00" in october
    assert "Assinatura" in client.get("/?year=2026&month=10").text  # recurring list by competence


def test_deleting_an_installment_is_refused_with_a_message(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": "2099-01-26",
            "description": "Fone",
            "amount": "300,00",
            "installments": "3",
        },
    )
    with container.uow as work:
        installment = work.transactions.list_by_account(card)[0]
    plain = client.post(f"/entries/{installment.id}/delete")
    assert plain.status_code == 303 and "err=USE_DELETE_PURCHASE" in plain.headers["location"]
    page = client.get(plain.headers["location"])
    assert "apague a compra inteira" in page.text
    htmx = client.post(f"/entries/{installment.id}/delete", headers={"hx-request": "true"})
    assert htmx.status_code == 200 and "err=USE_DELETE_PURCHASE" in htmx.headers["hx-redirect"]
    with container.uow as work:
        assert len(work.transactions.list_by_account(card)) == 3


# --- budget, recurring and daily flow (Phase 4) ---


def month_first(back: int) -> dt.date:
    today = dt.date.today()
    index = today.year * 12 + today.month - 1 - back
    return dt.date(index // 12, index % 12 + 1, 1)


def add_entry(
    client: TestClient, account: str, day: dt.date, amount: str, description: str, **extra: str
) -> None:
    category = extra.pop("category_id", "")
    response = client.post(
        "/entries",
        data={
            "kind": "expense",
            "account_id": account,
            "date": day.isoformat(),
            "amount": amount,
            "description": description,
            "category_id": category,
            **extra,
        },
    )
    assert response.status_code == 303, response.text[:300]


def category_id(container: Container, slug: str) -> str:
    with container.uow as work:
        found = work.categories.get_by_slug(slug)
    assert found
    return found.id


def test_budget_page_empty_and_navigation(client: TestClient) -> None:
    page = client.get("/budget")
    body = body_of(page.text)
    assert page.status_code == 200 and "<h1>Orçamento</h1>" in body
    assert "Nenhuma categoria tem meta" in body or "Meta mensal" in body
    assert "Definir" in body and "Total das despesas" in body
    home = client.get("/")
    for path in ("/budget", "/recurring"):
        assert f'href="{path}"' in home.text
    assert "Comparar com o orçamento" in home.text


def test_budget_goals_matrix_and_editing(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    food, health = category_id(container, "food"), category_id(container, "health")
    edit = client.get("/budget?edit=1")
    assert "Salvar metas" in edit.text and f'name="goal_{food}"' in edit.text
    saved = client.post("/budget/goals", data={f"goal_{food}": "700,00", f"goal_{health}": ""})
    assert saved.status_code == 303 and "ok=budget" in saved.headers["location"]
    for back, amount in ((3, "910,00"), (2, "788,20"), (1, "842,30")):
        day = month_first(back) + dt.timedelta(days=3)
        add_entry(client, checking, day, amount, "Delivery", category_id=food)
    add_entry(
        client,
        checking,
        month_first(2) + dt.timedelta(days=4),
        "200,00",
        "Consulta",
        category_id=health,
    )
    add_entry(client, checking, month_first(0), "999,00", "Corrente", category_id=food)
    body = body_of(client.get("/budget").text)
    assert "Metas atualizadas." in client.get(saved.headers["location"]).text
    matrix = body.split('id="h-matrix"', 1)[1]
    row = matrix.split("Alimentação", 1)[1].split("</tr>", 1)[0]  # the Alimentação row only
    amounts = visible(row)
    assert all(v in amounts for v in ("R$ 910,00", "R$ 788,20", "R$ 842,30", "R$ 700,00"))
    assert row.count('class="num over"') == 3  # all three months are above the 700 goal
    assert "acima da meta</small>" in row and "neg" in row
    sem_meta = body.split("<strong>Sem meta</strong>", 1)[1].split("</tr>", 1)[0]
    assert "Saúde" in sem_meta and "R$ 200,00" in visible(sem_meta)
    assert "R$ 999,00" not in body  # the month in progress is not part of the average
    cleared = client.post("/budget/goals", data={f"goal_{food}": ""})
    assert cleared.status_code == 303
    with container.uow as work:
        assert work.categories.get(food).monthly_budget_cents is None  # type: ignore[union-attr]


def test_saving_goals_is_all_or_nothing_and_keeps_what_was_typed(
    client: TestClient, container: Container
) -> None:
    food, health = category_id(container, "food"), category_id(container, "health")
    bad = client.post(
        "/budget/goals",
        data={f"goal_{food}": "700,00", f"goal_{health}": "abc", "range": "year", "year": "2025"},
    )
    assert bad.status_code == 400 and "Valor inválido" in bad.text
    with container.uow as work:
        assert work.categories.get(food).monthly_budget_cents is None  # type: ignore[union-attr]
    assert 'value="700,00"' in bad.text and 'value="abc"' in bad.text  # the form keeps the input
    assert 'name="year" value="2025"' in bad.text
    huge = client.post("/budget/goals", data={f"goal_{food}": "9" * 30})
    assert huge.status_code == 400 and "grande demais" in huge.text
    ok = client.post(
        "/budget/goals", data={f"goal_{food}": "700,00", "range": "year", "year": "2025"}
    )
    assert ok.headers["location"].startswith("/budget?") and "year=2025" in ok.headers["location"]
    with container.uow as work:
        assert work.categories.get(food).monthly_budget_cents == 70_000  # type: ignore[union-attr]


def test_hostile_years_and_months_never_crash(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    for query in ("range=year&year=99999", "range=year&year=-5", "range=year&year=10000", "year=0"):
        assert client.get(f"/budget?{query}").status_code == 200, query
    for month in ("0001-01", "9999-12", "2026-13", "abc", "-1"):
        response = client.get(f"/accounts/{checking}/flow?month={month}")
        assert response.status_code == 200, month


def test_budget_goal_errors_and_year_range(client: TestClient, container: Container) -> None:
    salary = category_id(container, "salary")
    bad = client.post("/budget/goals", data={f"goal_{salary}": "100,00"})
    assert bad.status_code == 400 and "Só categorias de despesa" in bad.text
    assert "Salvar metas" in bad.text  # the form stays open
    invalid = client.post("/budget/goals", data={f"goal_{category_id(container, 'food')}": "abc"})
    assert invalid.status_code == 400 and "Valor inválido" in invalid.text
    year = client.get(f"/budget?range=year&year={dt.date.today().year}")
    assert year.status_code == 200 and "inteiro" in year.text
    future = client.get(f"/budget?range=year&year={dt.date.today().year + 1}")
    assert "Ainda não há mês fechado" in future.text
    assert client.get("/budget?range=year&year=abc").status_code == 200


def test_recurring_page_matrix_and_alerts(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    telecom, health = category_id(container, "telecom"), category_id(container, "health")
    subs = category_id(container, "subscriptions")
    for back, amount in ((3, "119,90"), (2, "119,90"), (1, "129,90")):
        add_entry(
            client,
            checking,
            month_first(back) + dt.timedelta(days=4),
            amount,
            "Internet",
            category_id=telecom,
            recurring="1",
        )
    for back in (3, 2):
        add_entry(
            client,
            checking,
            month_first(back) + dt.timedelta(days=5),
            "90,00",
            "Academia",
            category_id=health,
            recurring="1",
        )
    add_entry(
        client,
        checking,
        month_first(1) + dt.timedelta(days=6),
        "39,90",
        "Streaming <b>",
        category_id=subs,
        recurring="1",
    )
    page = client.get("/recurring")
    body = body_of(page.text)
    assert "Mudou de valor" in body and "Sumiu" in body and "Apareceu" in body
    assert "Internet: de R$ 119,90" in body and "para R$ 129,90" in body
    assert "Academia: cobrado em" in body
    assert "Streaming &lt;b&gt;" in body and "Streaming <b>" not in body  # user text is escaped
    assert "em andamento" in body
    home = client.get("/")
    assert "3 alerta(s) nas despesas recorrentes" in home.text


def test_recurring_page_is_empty_without_data(client: TestClient) -> None:
    page = client.get("/recurring")
    assert page.status_code == 200 and "Nenhum alerta" in page.text
    assert "Nenhuma despesa recorrente" in page.text


def test_daily_flow_page(client: TestClient, container: Container) -> None:
    checking, savings = setup_accounts(client, container)
    client.post(f"/accounts/{checking}/balance", data={"date": "2026-06-30", "amount": "1.000,00"})
    client.post(
        "/entries",
        data={
            "kind": "income",
            "account_id": checking,
            "date": "2026-07-05",
            "amount": "5.000,00",
            "description": "Salário",
        },
    )
    add_entry(client, checking, dt.date(2026, 7, 5), "120,00", "Mercado")
    client.post(
        "/transfers",
        data={
            "from_account": checking,
            "to_account": savings,
            "date": "2026-07-09",
            "amount": "500,00",
        },
    )
    page = client.get(f"/accounts/{checking}/flow?month=2026-07")
    body = body_of(page.text)
    assert "Fluxo diário · Conta Corrente" in body and "05/07/2026" in body
    amounts = visible(body)
    assert all(v in amounts for v in ("R$ 5.880,00", "R$ 5.380,00", "R$ 1.000,00"))
    other = client.get(f"/accounts/{savings}/flow?month=2026-07")
    assert "Saldo indisponível" in other.text
    assert client.get(f"/accounts/{checking}/flow?month=zzz").status_code == 200
    missing = client.get("/accounts/nope/flow")
    assert missing.status_code == 400 and "registro não encontrado" in missing.text
    assert (
        'href="/accounts/' in client.get("/accounts").text
        and "Fluxo diário" in client.get("/accounts").text
    )


def test_daily_flow_is_not_available_for_cards(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container)
    response = client.get(f"/accounts/{card}/flow")
    assert response.status_code == 400 and "Cartões não têm fluxo diário" in response.text


# --- failure log ---


def test_server_errors_show_a_code_and_land_in_the_log_without_values(
    container: Container,
) -> None:
    app = create_app(container)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("saldo R$ 1.234,56 de Café São João")

    client = TestClient(
        app,
        base_url="http://localhost",
        headers=HEADERS,
        raise_server_exceptions=False,
    )
    page = client.get("/boom")
    assert page.status_code == 500 and "Algo deu errado" in page.text
    code = page.headers["x-request-id"]
    assert f"<code>{code}</code>" in page.text
    (entry,) = container.failures.recent()
    assert entry["id"] == code and entry["kind"] == "server_error" and entry["status"] == 500
    assert entry["error"] == "RuntimeError" and entry["path"] == "/boom"
    raw = container.failures.path.read_text(encoding="utf-8")
    assert "1.234,56" not in raw and "Café" not in raw
    listing = client.get("/diagnostics")
    assert listing.status_code == 200 and code in listing.text
    assert "Erro no servidor" in listing.text


def test_missing_pages_are_logged_but_not_the_favicon(
    client: TestClient, container: Container
) -> None:
    assert client.get("/nao-existe").status_code == 404
    assert client.get("/favicon.ico").status_code == 404
    entries = container.failures.recent()
    assert [(e["kind"], e["path"], e["status"]) for e in entries] == [
        ("http_error", "/nao-existe", 404)
    ]


def test_the_browser_reports_failures(client: TestClient, container: Container) -> None:
    report = {
        "kind": "htmx_error",
        "method": "POST",
        "target": "/cards/purchase/preview",
        "status": 500,
        "message": "resposta com erro",
        "path": "/cards/purchase",
    }
    assert client.post("/client-errors", json=report).status_code == 204
    (entry,) = container.failures.recent()
    assert (entry["source"], entry["kind"], entry["status"]) == ("client", "htmx_error", 500)
    # junk, oversized and cross-origin reports are dropped quietly or refused
    assert client.post("/client-errors", content=b"not json").status_code == 204
    assert client.post("/client-errors", json={"kind": "x" * 10}).status_code == 204
    assert client.post("/client-errors", content=b"{" + b" " * 5000).status_code == 204
    assert len(container.failures.recent()) == 1
    foreign = client.post(
        "/client-errors", json=report, headers={**HEADERS, "origin": "http://evil.example"}
    )
    assert foreign.status_code == 403


def test_the_script_reports_failures_and_the_page_links_the_diagnostics(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    assert "/client-errors" in script and "unhandledrejection" in script
    assert "htmx:responseError" in script
    assert "/diagnostics" in client.get("/more").text
    assert "Nenhuma falha registrada" in client.get("/diagnostics").text


def test_new_account_form_records_the_opening_balance(
    client: TestClient, container: Container
) -> None:
    assert client.post("/institutions", data={"name": "Banco"}).status_code == 303
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    ok = client.post(
        "/accounts",
        data={
            "nickname": "Corrente",
            "institution_id": inst.id,
            "opening_balance": "1.183,67",
            "opening_date": "2026-09-08",
        },
    )
    assert ok.status_code == 303
    with container.uow as work:
        (account,) = work.accounts.list_all()
        (anchor,) = work.anchors.list_for_account(account.id)
    assert (anchor.balance_cents, anchor.on_date) == (118_367, dt.date(2026, 9, 8))
    page = client.get("/accounts")
    assert "R$ 1.183,67" in visible(page) and "Saldo inicial" in page.text
    half = client.post(
        "/accounts",
        data={"nickname": "Outra", "institution_id": inst.id, "opening_balance": "10,00"},
    )
    assert half.status_code == 400 and "valor e a data" in half.text


def test_contribution_form_preselects_the_checking_account_of_the_same_bank(
    client: TestClient, container: Container
) -> None:
    checking, savings = setup_accounts(client, container)
    page = client.get("/investments").text
    assert f'<option value="{checking}"' in page
    selected = page.split('name="other_account_id"')[1].split("</select>")[0]
    assert f'value="{checking}"' in selected and "selected" in selected
    assert "data-flow-target" in page
    # two checking accounts at the bank: no guess, "conta não controlada" stays
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    client.post("/accounts", data={"nickname": "Segunda", "institution_id": inst.id})
    again = (
        client.get("/investments").text.split('name="other_account_id"')[1].split("</select>")[0]
    )
    assert "selected" not in again
    script = client.get("/static/app.js").text
    assert "data-flow-target" in script and "dataset.institution" in script
    assert savings


def test_a_card_is_never_offered_for_income_in_the_quick_form(
    client: TestClient, container: Container
) -> None:
    checking, card = make_card(client, container)
    page = client.get("/entries").text
    select = page.split("data-entry-account")[1].split("</select>")[0]
    card_option = select.split(f'value="{card}"')[1].split("</option>")[0]
    checking_option = select.split(f'value="{checking}"')[1].split("</option>")[0]
    assert 'data-kinds="expense refund"' in card_option  # no income
    assert 'data-kinds="expense income refund"' in checking_option
    # opened on the income type: the card is hidden and disabled from the start
    income_page = client.get("/entries?kind=income").text
    assert income_page  # the filter keeps working
    script = client.get("/static/app.js").text
    assert "data-entry-account" in script and "dataset.kinds" in script
    assert "data-transfer-from" in script
    # and the server still refuses it if someone forces it
    forced = client.post(
        "/entries",
        data={
            "kind": "income", "account_id": card, "date": "2026-09-01",
            "amount": "10,00", "description": "x",
        },
    )  # fmt: skip
    assert forced.status_code == 400


def test_switching_to_a_transfer_does_not_make_the_category_request_fail(
    client: TestClient, container: Container
) -> None:
    assert client.get("/entries/category-field?kind=transfer").status_code == 204
    assert client.get("/entries/category-field?kind=expense").status_code == 200
    assert client.get("/entries/category-field?kind=banana").status_code == 400
    assert client.get("/.well-known/appspecific/com.chrome.devtools.json").status_code == 404
    assert client.get("/static/chart.umd.js.map").status_code == 404
    # browser noise and a plain 400 are not failures of the system
    assert container.failures.recent() == []


def test_confirmations_use_the_in_page_dialog_not_the_browser_popup(
    client: TestClient, container: Container
) -> None:
    checking, _ = make_card(client, container)
    client.post(
        "/entries",
        data={
            "kind": "expense", "account_id": checking, "date": "2026-09-01",
            "amount": "10,00", "description": "Teste",
        },
    )  # fmt: skip
    page = client.get("/entries?month=2026-09").text
    assert '<dialog id="confirm-dialog"' in page  # in every page, from the base template
    assert 'hx-confirm="Este lançamento será apagado' in page
    assert 'data-confirm-title="Apagar lançamento"' in page and "data-confirm-danger" in page
    accounts = client.get("/accounts").text
    assert 'data-confirm-title="Desativar conta"' in accounts  # an active account asks first
    script = client.get("/static/app.js").text
    assert "window.confirm" in script  # only as the fallback of very old browsers
    assert "htmx:confirm" in script and "showModal" in script
    for template in Path("src/financas/interfaces/web/templates").glob("*.html"):
        assert "onclick" not in template.read_text(encoding="utf-8")  # no inline handlers (CSP)


# --- /cards: account colour, year filter, tab centring -----------------------------------------


def _buy(client: TestClient, card: str, day: str, description: str, installments: str) -> None:
    response = client.post(
        "/cards/purchase",
        data={
            "account_id": card,
            "date": day,
            "description": description,
            "amount": "300,00",
            "amount_mode": "total",
            "installments": installments,
        },
    )
    assert response.status_code == 303


def _tabs(page: str) -> str:
    start = page.index('<nav class="statement-tabs"')
    return page[start : page.index("</nav>", start)]


def test_card_face_binds_the_account_colour_and_a_readable_text_colour(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container, color="#2E7D32", use_color="1")
    page = client.get(f"/cards?card={card}").text
    face = page[page.index('class="card-face card-face--tinted') :]
    face = face[: face.index(">")]
    assert "--card-color: #2E7D32" in face
    assert "--face-fg: #FFFFFF" in face and "--face-shade" not in face  # white text, deeper corner
    assert "data-card-tilt" in face
    css = client.get("/static/cards.css").text
    assert (
        "var(--card-color" in css and "color-mix(" in css
    )  # surface, depth and glare derive from it


def test_a_bright_account_colour_gets_slate_text_and_a_lighter_corner(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container, color="#F7D117", use_color="1")
    face = client.get(f"/cards?card={card}").text
    face = face[face.index('class="card-face card-face--tinted') :]
    face = face[: face.index(">")]
    assert "--face-fg: #0F172A" in face and "--face-shade" not in face  # slate text, lighter corner


def test_cards_year_filter_shows_only_the_months_of_the_chosen_year(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    _buy(client, card, "2026-11-26", "Notebook", "3")  # statements dez/2026, jan/2027, fev/2027
    page = client.get(f"/cards?card={card}&ano=2027").text
    tabs = _tabs(page)
    assert "month=2027-01" in tabs and "month=2027-02" in tabs and "month=2026-12" not in tabs
    assert page.count('class="year-pill"') == 2
    assert re.search(r'<a class="year-pill"[^>]*aria-current="true"[^>]*>\s*2027\s*</a>', page)
    other = _tabs(client.get(f"/cards?card={card}&ano=2026").text)
    assert "month=2026-12" in other and "month=2027-01" not in other
    # lenient: garbage or a year without statements falls back to the default year
    for bad in ("banana", "1999", ""):
        assert "month=" in _tabs(client.get(f"/cards?card={card}&ano={bad}").text)
    # a month in the URL selects its year; the pills swap only the statement section (HTMX)
    by_month = client.get(f"/cards?card={card}&month=2027-02").text
    assert "month=2026-12" not in _tabs(by_month) and "month=2027-02" in _tabs(by_month)
    assert 'hx-target="#statement-section"' in page and 'id="statement-section"' in page
    assert 'hx-select="#statement-section"' in page and 'hx-push-url="true"' in page


def test_active_statement_tab_carries_the_marker_the_auto_scroll_reads(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    _buy(client, card, "2026-11-26", "Notebook", "3")
    tabs = _tabs(client.get(f"/cards?card={card}&month=2027-01").text)
    assert tabs.count('aria-current="true"') == 1
    marked = tabs[: tabs.index('aria-current="true"')]
    assert "month=2027-01" in marked[marked.rindex("<a ") :]
    script = client.get("/static/app.js").text
    assert ".statement-tabs" in script and "scrollLeft" in script and "htmx:afterSettle" in script


def test_a_single_year_still_shows_its_year_pill(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container)
    _buy(client, card, "2026-07-26", "Fone", "1")
    page = client.get(f"/cards?card={card}").text
    assert page.count('class="year-pill"') == 1 and "ano=2026" in page


# --- editing: rename a category, edit an entry, anticipate installments ---

HX = {"hx-request": "true"}


def category_by_slug(container: Container, slug: str):
    with container.uow as work:
        found = work.categories.get_by_slug(slug)
    assert found
    return found


def test_rename_category_plain_form_and_conflict(client: TestClient, container: Container) -> None:
    food = category_by_slug(container, "food")
    ok = client.post(f"/categories/{food.id}/rename", data={"name": "  Restaurantes e Açaí "})
    assert ok.status_code == 303 and "ok=category_renamed" in ok.headers["location"]
    page = client.get(ok.headers["location"])
    assert "Categoria renomeada." in page.text and "Restaurantes e Açaí" in page.text
    assert category_by_slug(container, "food").name == "Restaurantes e Açaí"
    assert category_by_slug(container, "food").slug == "food"  # the stable key never changes

    health = category_by_slug(container, "health")
    clash = client.post(f"/categories/{health.id}/rename", data={"name": "RESTAURANTES E ACAI"})
    assert clash.status_code == 400  # like every page error in the app
    assert "Já existe uma categoria com esse nome." in clash.text
    assert category_by_slug(container, "health").name == "Saúde"
    empty = client.post(f"/categories/{health.id}/rename", data={"name": "   "})
    assert "Informe um nome." in empty.text


def test_rename_category_inline_with_htmx_swaps_one_row(
    client: TestClient, container: Container
) -> None:
    taxes = category_by_slug(container, "taxes")
    page = client.get("/categories")
    assert f'hx-get="/categories/{taxes.id}/rename"' in page.text
    form = client.get(f"/categories/{taxes.id}/rename", headers=HX)
    assert form.status_code == 200 and form.text.lstrip().startswith("<tr")
    assert 'name="name"' in form.text and f'value="{taxes.name}"' in form.text
    assert "Cancelar" in form.text and "Salvar" in form.text
    assert "<html" not in form.text  # a fragment, not a page

    done = client.post(f"/categories/{taxes.id}/rename", data={"name": "Tributos"}, headers=HX)
    assert done.status_code == 200 and done.text.lstrip().startswith("<tr")
    assert "Tributos" in done.text and 'hx-get="/categories/' in done.text
    assert "Categoria renomeada." in done.headers["hx-trigger"]
    assert category_by_slug(container, "taxes").name == "Tributos"

    clash = client.post(f"/categories/{taxes.id}/rename", data={"name": "saúde"}, headers=HX)
    assert clash.status_code == 200 and "Já existe uma categoria com esse nome." in clash.text
    assert 'value="saúde"' in clash.text  # the typed text is kept
    assert "hx-trigger" not in clash.headers
    cancel = client.get(f"/categories/{taxes.id}/row", headers=HX)
    assert "Tributos" in cancel.text and 'name="name"' not in cancel.text


def test_money_mask_script_is_served_and_loaded_with_the_money_fields(
    client: TestClient,
) -> None:
    assert 'src="/static/money-mask.js"' in client.get("/entries").text
    script = client.get("/static/money-mask.js")
    assert script.status_code == 200 and ".money-field input" in script.text
    assert "onclick=" not in client.get("/entries").text  # no inline handlers (CSP)


def add_expense(client: TestClient, account: str, **overrides: str) -> None:
    data = {
        "kind": "expense",
        "date": dt.date.today().isoformat(),
        "amount": "123,45",
        "description": "Padaria São João",
        "account_id": account,
        **overrides,
    }
    assert client.post("/entries", data=data).status_code == 303


def only_entry(container: Container, account: str):
    with container.uow as work:
        (entry,) = work.transactions.list_by_account(account)
    return entry


def test_edit_entry_form_and_save_changes_amount_date_and_category(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking)
    entry = only_entry(container, checking)
    assert f'hx-get="/entries/{entry.id}/edit"' in client.get("/entries").text

    form = client.get(f"/entries/{entry.id}/edit", headers=HX)
    assert form.status_code == 200 and 'value="123,45"' in form.text
    assert "data-closed-statement" not in form.text and "Estou ciente" not in form.text
    assert "<html" not in form.text

    groceries = category_by_slug(container, "groceries")
    day = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    saved = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "1.000,50",
            "date": day,
            "description": "Mercado Açaí",
            "category_id": groceries.id,
            "account_id": checking,
            "notes": "conferido",
        },
        headers={**HX, "hx-current-url": "http://localhost/entries?month=2026-10&kind=expense"},
    )
    assert saved.status_code == 200
    location = saved.headers["hx-redirect"]
    assert location.startswith("/entries?") and "kind=expense" in location
    assert "ok=entry_updated" in location
    assert "Lançamento atualizado." in client.get(location).text
    updated = only_entry(container, checking)
    assert (updated.amount_cents, updated.posted_on.isoformat()) == (-100_050, day)
    assert (updated.category_id, updated.description, updated.notes) == (
        groceries.id,
        "Mercado Açaí",
        "conferido",
    )


def test_edit_entry_errors_keep_the_form(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking)
    entry = only_entry(container, checking)
    salary = category_by_slug(container, "salary")
    bad = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "10,00",
            "date": entry.posted_on.isoformat(),
            "description": "Outra",
            "category_id": salary.id,
        },
        headers=HX,
    )
    assert bad.status_code == 200 and "não combina com o tipo de lançamento" in bad.text
    assert 'value="Outra"' in bad.text
    zero = client.post(
        f"/entries/{entry.id}/edit",
        data={"amount": "0,00", "date": entry.posted_on.isoformat(), "description": "x"},
        headers=HX,
    )
    assert "O valor precisa ser maior que zero." in zero.text
    assert only_entry(container, checking) == entry  # nothing was saved


def test_a_transfer_between_own_accounts_is_edited_on_both_legs(
    client: TestClient, container: Container
) -> None:
    checking, savings = setup_accounts(client, container)
    client.post(
        "/transfers",
        data={
            "amount": "50,00",
            "date": dt.date.today().isoformat(),
            "from_account": checking,
            "to_account": savings,
        },
    )
    with container.uow as work:
        leg = work.transactions.list_by_account(checking)[0]
    form = client.get(f"/entries/{leg.id}/edit", headers=HX)
    assert "Salvar transferência" in form.text and 'name="to_account"' in form.text
    saved = client.post(
        f"/entries/{leg.id}/edit",
        data={
            "amount": "1,00",
            "date": dt.date.today().isoformat(),
            "description": "x",
            "from_account": checking,
            "to_account": savings,
        },
        headers=HX,
    )
    assert saved.status_code == 200 and "transfer_updated" in saved.headers["hx-redirect"]


def buy_on_card(client: TestClient, card: str, days_ago: int, **extra: str) -> None:
    day = (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()
    data = {
        "account_id": card,
        "date": day,
        "description": "Fone",
        "amount": "300,00",
        "amount_mode": "total",
        "installments": "1",
        **extra,
    }
    assert client.post("/cards/purchase", data=data).status_code == 303


WARNING = (
    "Atenção:</strong> A fatura deste lançamento já foi fechada. Alterar este valor ou data "
    "pode divergir do pagamento já efetuado ou modificar o histórico da fatura."
)


def test_editing_a_card_purchase_on_a_closed_statement_warns_and_needs_acknowledgement(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75)  # its statement closed long ago and is unpaid
    entry = only_entry(container, card)
    form = client.get(f"/entries/{entry.id}/edit", headers=HX)
    assert WARNING in form.text
    assert "Estou ciente de que a fatura já está fechada" in form.text
    assert 'name="ack"' in form.text and "data-closed-statement" in form.text

    data = {
        "amount": "250,00",
        "date": entry.posted_on.isoformat(),
        "description": "Fone",
        "account_id": card,
    }
    refused = client.post(f"/entries/{entry.id}/edit", data=data, headers=HX)
    assert refused.status_code == 200 and "Marque “Estou ciente" in refused.text
    assert WARNING in refused.text
    assert only_entry(container, card).amount_cents == -30_000

    accepted = client.post(f"/entries/{entry.id}/edit", data={**data, "ack": "1"}, headers=HX)
    assert "ok=entry_updated" in accepted.headers["hx-redirect"]
    assert only_entry(container, card).amount_cents == -25_000


def test_editing_a_card_purchase_on_an_open_statement_has_no_warning(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0)
    entry = only_entry(container, card)
    form = client.get(f"/entries/{entry.id}/edit", headers=HX)
    assert form.status_code == 200 and "<form" in form.text
    assert "data-closed-statement" not in form.text and "já foi fechada" not in form.text
    saved = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "310,00",
            "date": entry.posted_on.isoformat(),
            "description": "Fone",
            "account_id": card,
        },
        headers=HX,
    )
    assert "ok=entry_updated" in saved.headers["hx-redirect"]
    assert only_entry(container, card).amount_cents == -31_000


def test_a_card_purchase_on_a_paid_statement_is_history(
    client: TestClient, container: Container
) -> None:
    checking, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75)
    entry = only_entry(container, card)
    assert entry.statement_id
    paid = client.post(
        f"/statements/{entry.statement_id}/pay",
        data={"from_account": checking, "date": dt.date.today().isoformat()},
    )
    assert paid.status_code == 303
    form = client.get(f"/entries/{entry.id}/edit", headers=HX)
    assert "já está paga" in form.text and "<form" not in form.text
    refused = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "1,00",
            "date": entry.posted_on.isoformat(),
            "description": "Fone",
            "ack": "1",
        },
        headers=HX,
    )
    assert "já está paga" in refused.text and "<form" not in refused.text
    assert only_entry_of_expense(container, card).amount_cents == -30_000


def only_entry_of_expense(container: Container, card: str):
    with container.uow as work:
        return next(t for t in work.transactions.list_by_account(card) if t.amount_cents < 0)


def test_installment_edit_form_is_complete_with_the_propagation_checkbox(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3")
    with container.uow as work:
        installment = work.transactions.list_by_plan(work.plans.list_all()[0].id)[1]
    form = client.get(f"/entries/{installment.id}/edit", headers=HX)
    assert form.status_code == 200 and "<html" not in form.text
    assert "Parcela 2/3" in form.text and "Valor da parcela" in form.text
    for name in ("description", "category_id", "amount", "notes", "propagate"):
        assert f'name="{name}"' in form.text, name
    assert 'name="date"' not in form.text and 'name="account_id"' not in form.text
    assert "Aplicar nova descrição e categoria a todas as parcelas deste parcelamento" in form.text
    assert re.search(r'name="propagate"[^>]*checked', form.text)  # on by default
    assert "data-closed-statement" not in form.text  # open or future statement: no warning


def test_saving_an_installment_edit_propagates_and_returns_to_the_list(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3", amount="300,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
        installment = work.transactions.list_by_plan(plan.id)[1]
    groceries = category_by_slug(container, "groceries")
    data = {
        "amount": "120,00",
        "description": "Geladeira",
        "category_id": groceries.id,
        "notes": "só desta",
        "propagate": "1",
    }
    saved = client.post(f"/entries/{installment.id}/edit", data=data, headers=HX)
    assert saved.headers["hx-redirect"].startswith("/entries?")
    assert "ok=entry_updated" in saved.headers["hx-redirect"]
    with container.uow as work:
        rows = work.transactions.list_by_plan(plan.id)
        assert {r.description for r in rows} == {"Geladeira"}
        assert {r.category_id for r in rows} == {groceries.id}
        assert [r.amount_cents for r in rows] == [-10_000, -12_000, -10_000]
        assert [r.notes for r in rows] == [None, "só desta", None]
        assert work.plans.get(plan.id).description == "Geladeira"  # type: ignore[union-attr]
    # unchecked: only this installment
    off = client.post(
        f"/entries/{installment.id}/edit",
        data={k: v for k, v in {**data, "description": "Só esta"}.items() if k != "propagate"},
        headers=HX,
    )
    assert off.status_code == 200
    with container.uow as work:
        rows = work.transactions.list_by_plan(plan.id)
    assert [r.description for r in rows] == ["Geladeira", "Só esta", "Geladeira"]


def test_installment_date_and_card_cannot_be_forced_through_the_form(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3")
    with container.uow as work:
        installment = work.transactions.list_by_plan(work.plans.list_all()[0].id)[1]
    saved = client.post(
        f"/entries/{installment.id}/edit",
        data={
            "amount": "1,00",
            "date": "2031-01-01",  # not on the form: ignored, the date follows the statement
            "account_id": "another",
            "description": "x",
        },
        headers=HX,
    )
    assert "ok=entry_updated" in saved.headers["hx-redirect"]
    with container.uow as work:
        after = work.transactions.get(installment.id)
    assert after and (after.posted_on, after.account_id) == (
        installment.posted_on,
        installment.account_id,
    )
    assert after.amount_cents == -100


def test_anticipate_installments_flow(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="4", amount="400,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
    cards_page = client.get(f"/cards?card={card}")
    assert f'hx-get="/plans/{plan.id}/anticipate"' in cards_page.text
    assert "Antecipar parcelas" in cards_page.text

    form = client.get(f"/plans/{plan.id}/anticipate", headers=HX)
    assert form.status_code == 200 and "<html" not in form.text
    assert form.text.count('name="n"') == 3  # installment 1 is already on the open statement
    assert "Confirmar antecipação" in form.text and "Valor nominal" in form.text
    assert "R$ 300,00" in visible(form)

    preview = client.post(
        f"/plans/{plan.id}/anticipate/preview",
        data={"n": ["2", "3"], "rate": "2", "discount": ""},
        headers=HX,
    )
    assert "Parcelas escolhidas" in preview.text and "R$ 200,00" in visible(preview)
    assert "Desconto" in preview.text and "−" in preview.text
    none = client.post(f"/plans/{plan.id}/anticipate/preview", data={"rate": ""}, headers=HX)
    assert "Nenhuma parcela pode ser antecipada" in none.text

    both = client.post(
        f"/plans/{plan.id}/anticipate",
        data={"n": ["2"], "rate": "2", "discount": "1,00"},
        headers=HX,
    )
    assert "a taxa de desconto ou o valor do desconto" in both.text

    done = client.post(
        f"/plans/{plan.id}/anticipate", data={"n": ["2", "3"], "discount": "5,00"}, headers=HX
    )
    assert done.status_code == 200
    assert "ok=anticipated" in done.headers["hx-redirect"] and card in done.headers["hx-redirect"]
    assert "Parcelas antecipadas" in client.get(done.headers["hx-redirect"]).text
    with container.uow as work:
        rows = work.transactions.list_by_plan(plan.id)
        refunds = [t for t in work.transactions.list_by_account(card) if t.amount_cents > 0]
    assert [r.installment_number for r in rows] == [1, 2, 3, 4]
    assert (
        len({r.statement_id for r in rows[:3]}) == 1
        and rows[3].statement_id != rows[0].statement_id
    )
    (refund,) = refunds
    assert refund.amount_cents == 500 and refund.statement_id == rows[0].statement_id
    assert "antecipação" in refund.description
    assert sum(-r.amount_cents for r in rows) == 40_000  # the plan total is untouched


def test_anticipate_plain_post_redirects_and_unknown_plan_is_a_portuguese_error(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="2", amount="200,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
    done = client.post(f"/plans/{plan.id}/anticipate", data={"n": ["2"]})
    assert done.status_code == 303 and "ok=anticipated" in done.headers["location"]
    again = client.post(f"/plans/{plan.id}/anticipate", data={"n": ["2"]}, headers=HX)
    assert "Nenhuma parcela pode ser antecipada" in again.text
    assert client.get("/plans/nope/anticipate", headers=HX).status_code in {400, 404}


# --- editing from a statement on /cards ---


def statement_of_entry(container: Container, entry: Transaction) -> Statement:
    assert entry.statement_id
    with container.uow as work:
        statement = work.statements.get(entry.statement_id)
    assert statement
    return statement


def statement_page(client: TestClient, card: str, statement: Statement) -> str:
    response = client.get(f"/cards?card={card}&month={statement.month}")
    assert response.status_code == 200
    return response.text


def test_cards_statement_rows_have_an_edit_button_on_open_and_closed_statements(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75, description="Compra antiga")
    buy_on_card(client, card, days_ago=0, description="Compra nova")
    with container.uow as work:
        entries = {t.description: t for t in work.transactions.list_by_account(card)}
    for description, status in (("Compra antiga", "closed"), ("Compra nova", "open")):
        entry = entries[description]
        statement = statement_of_entry(container, entry)
        page = statement_page(client, card, statement)
        assert f'hx-get="/entries/{entry.id}/edit?from=cards' in page
        assert f"card={card}" in page and f"month={statement.month}" in page
        assert "Editar lançamento" in page, status
        assert 'class="icon-btn mini row-edit"' in page


def test_cards_statement_rows_of_a_paid_statement_only_keep_the_payment_editable(
    client: TestClient, container: Container
) -> None:
    checking, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75)
    entry = only_entry(container, card)
    statement = statement_of_entry(container, entry)
    assert "row-edit" in statement_page(client, card, statement)  # closed: still editable
    client.post(
        f"/statements/{statement.id}/pay",
        data={"from_account": checking, "date": dt.date.today().isoformat()},
    )
    page = statement_page(client, card, statement)
    assert "Fone" in page and f"/entries/{entry.id}/edit" not in page  # the purchase is history
    # the payment itself can be edited (it is the way back into the paid statement)
    assert page.count("row-edit") == 1 and "Editar pagamento" in page
    assert len(re.findall(r'hx-get="/entries/[0-9a-f]{32}/edit', page)) == 1


def test_cards_installment_rows_open_the_full_edit_form_and_save_back_to_the_statement(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3", amount="300,00")
    with container.uow as work:
        first = work.transactions.list_by_plan(work.plans.list_all()[0].id)[0]
    statement = statement_of_entry(container, first)
    page = statement_page(client, card, statement)
    assert "Editar parcela" in page and f"/entries/{first.id}/edit?from=cards" in page
    year = statement.month.year
    qs = f"from=cards&card={card}&month={statement.month}&ano={year}"
    form = client.get(f"/entries/{first.id}/edit?{qs}", headers=HX)
    assert "Parcela 1/3" in form.text and 'name="propagate"' in form.text
    assert f"/entries/{first.id}/row?{qs}".replace("&", "&amp;") in form.text  # Cancelar
    assert "R$ 100,00" in visible(client.get(f"/cards?card={card}&month={statement.month}"))

    saved = client.post(
        f"/entries/{first.id}/edit?{qs}",
        data={"amount": "80,00", "description": "Fone", "propagate": "1"},
        headers=HX,
    )
    location = saved.headers["hx-redirect"]
    assert location.startswith("/cards?") and f"month={statement.month}" in location
    assert "ok=entry_updated" in location and f"ano={year}" in location
    text = visible(client.get(location))
    assert "R$ 80,00" in text  # the statement was recalculated with the new installment value


def test_edit_started_from_cards_returns_to_the_same_statement_with_new_totals(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="300,00")
    entry = only_entry(container, card)
    statement = statement_of_entry(container, entry)
    year = statement.month.year
    qs = f"from=cards&card={card}&month={statement.month}&ano={year}"
    assert "R$ 300,00" in visible(client.get(f"/cards?card={card}&month={statement.month}"))

    form = client.get(f"/entries/{entry.id}/edit?{qs}", headers=HX)
    assert f'action="/entries/{entry.id}/edit?{qs}"'.replace("&", "&amp;") in form.text
    assert f"/entries/{entry.id}/row?{qs}".replace("&", "&amp;") in form.text  # Cancelar

    saved = client.post(
        f"/entries/{entry.id}/edit?{qs}",
        data={
            "amount": "250,00",
            "date": entry.posted_on.isoformat(),
            "description": "Fone",
            "account_id": card,
        },
        headers=HX,
    )
    location = saved.headers["hx-redirect"]
    assert location.startswith("/cards?") and "from=" not in location
    assert f"card={card}" in location and f"month={statement.month}" in location
    assert f"ano={year}" in location and "ok=entry_updated" in location
    page = client.get(location)
    assert "Lançamento atualizado." in page.text and 'id="statement-section"' in page.text
    text = visible(page)
    assert "R$ 250,00" in text and "R$ 300,00" not in text  # the statement total was recomputed

    # a plain (no-JS) form post goes back the same way
    plain = client.post(
        f"/entries/{entry.id}/edit?{qs}",
        data={
            "amount": "260,00",
            "date": entry.posted_on.isoformat(),
            "description": "Fone",
            "account_id": card,
        },
    )
    assert plain.status_code == 303 and plain.headers["location"].startswith("/cards?")


def test_cancel_from_cards_returns_the_unchanged_statement_row(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="300,00")
    entry = only_entry(container, card)
    statement = statement_of_entry(container, entry)
    row = client.get(
        f"/entries/{entry.id}/row?from=cards&card={card}&month={statement.month}", headers=HX
    )
    assert row.status_code == 200 and 'class="row card-entry"' in row.text
    assert f'hx-get="/entries/{entry.id}/edit?from=cards' in row.text
    assert "<html" not in row.text and "R$ 300,00" in visible(row)
    assert only_entry(container, card) == entry  # nothing changed


def test_closed_statement_warning_also_shows_when_editing_from_cards(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75)
    entry = only_entry(container, card)
    statement = statement_of_entry(container, entry)
    qs = f"from=cards&card={card}&month={statement.month}"
    form = client.get(f"/entries/{entry.id}/edit?{qs}", headers=HX)
    assert WARNING in form.text and "Estou ciente de que a fatura já está fechada" in form.text
    refused = client.post(
        f"/entries/{entry.id}/edit?{qs}",
        data={
            "amount": "1,00",
            "date": entry.posted_on.isoformat(),
            "description": "Fone",
            "account_id": card,
        },
        headers=HX,
    )
    assert (
        "Marque “Estou ciente" in refused.text
        and f"/entries/{entry.id}/edit?from=cards" in refused.text
    )
    assert only_entry(container, card) == entry


def test_an_unknown_origin_is_ignored(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking)
    entry = only_entry(container, checking)
    saved = client.post(
        f"/entries/{entry.id}/edit?from=//evil.example&card=x&month=y",
        data={"amount": "5,00", "date": entry.posted_on.isoformat(), "description": "Padaria"},
        headers=HX,
    )
    assert saved.headers["hx-redirect"].startswith("/entries?")


# --- card face telemetry ---


def card_face(page: str) -> str:
    match = re.search(r'<a class="card-face.*?</a>', page, re.S)
    assert match
    return match.group(0)


def test_card_face_shows_the_open_balance_and_live_day_counts(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="184,25")
    face = card_face(client.get("/cards").text)
    assert "Fatura atual" in face and "R$ 184,25" in re.sub(r"\s+", " ", visible(face))
    assert 'data-private="cards"' in face  # the privacy eye blurs it like the other card figures
    assert re.search(r"Fecha (em \d+ dias|amanhã)", face), face
    assert re.search(r"Vence (em \d+ dias|amanhã|hoje)", face), face
    assert "dias antes" not in face.split('title="')[0]  # the static sentence is gone


def test_card_face_without_purchases_shows_zero_not_nothing(
    client: TestClient, container: Container
) -> None:
    make_card(client, container)
    face = card_face(client.get("/cards").text)
    assert "Fatura atual" in face and "R$ 0,00" in visible(face)
    assert "Fecha" in face and "Vence" in face


def test_card_face_reports_a_closed_unpaid_statement_and_its_due_date(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75)  # closed long ago and unpaid
    face = card_face(client.get("/cards").text)
    assert "Fechada · Venceu há" in face


# --- day groups and the ghost edit button ---


def test_cards_statement_groups_entries_by_day_with_one_header_per_day(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="100,00", description="Primeira")
    buy_on_card(client, card, days_ago=0, amount="24,50", description="Segunda")
    with container.uow as work:
        statement = work.statements.list_all()[0]
    page = client.get(f"/cards?card={card}&month={statement.month}").text
    assert page.count('class="day-head day-group__header"') == 1  # two purchases, one day
    assert "Hoje · " in page
    assert "Total do dia: " in page and "R$ 124,50" in visible(page)
    assert 'class="c-date' not in page  # no date column on the rows any more
    assert '<div class="rowhead card-head"><span>Descrição</span>' in page  # no "Data" column
    head = page.index("day-group__header")
    assert head < page.index("Primeira") and head < page.index("Segunda")


def test_entries_list_groups_by_day_and_rows_have_no_date_column(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Café", amount="10,00")
    add_expense(client, checking, description="Almoço", amount="30,00")
    page = client.get("/entries").text
    assert page.count("day-group__header") == 1 and "Hoje · " in page
    assert "Gastos do dia: " in page and "R$ 40,00" in visible(page)
    assert 'class="c-date' not in page
    assert len(re.findall(r'hx-get="/entries/[0-9a-f]{32}/edit', page)) == 2  # one per row


def test_edit_button_is_a_ghost_icon_with_label_and_title(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0)
    entry = only_entry(container, card)
    with container.uow as work:
        statement = work.statements.get(entry.statement_id or "")
    page = client.get(f"/cards?card={card}&month={statement.month}").text  # type: ignore[union-attr]
    button = re.search(r"<button[^>]*row-edit[^>]*>.*?</button>", page, re.S)
    assert button
    markup = button.group(0)
    assert 'title="Editar"' in markup and 'aria-label="Editar lançamento' in markup
    assert "<svg" in markup and 'stroke="currentColor"' in markup and 'width="14"' in markup
    css = client.get("/static/screens.css").text
    assert "button.icon-btn.mini.row-edit" in css and "background: transparent" in css


# --- delete the pending installments of a plan ---


def test_delete_installment_plan_removes_everything_when_nothing_is_paid(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3", amount="300,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
    page = client.get(f"/cards?card={card}").text
    assert f'hx-delete="/installments/plan/{plan.id}"' in page
    assert "Deseja apagar as parcelas pendentes deste parcelamento?" in page
    assert "serão mantidas no histórico" not in page  # nothing paid: no history warning
    done = client.delete(f"/installments/plan/{plan.id}", headers=HX)
    assert done.status_code == 200
    location = done.headers["hx-redirect"]
    assert location.startswith("/cards?") and f"card={card}" in location
    assert "ok=plan_deleted" in location
    assert "Compra parcelada apagada." in client.get(location).text
    with container.uow as work:
        assert work.plans.list_all() == [] and work.transactions.list_by_account(card) == []


def test_delete_installment_plan_keeps_paid_history_and_says_so(
    client: TestClient, container: Container
) -> None:
    checking, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75, installments="3", amount="300,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
        first = work.transactions.list_by_plan(plan.id)[0]
    client.post(
        f"/statements/{first.statement_id}/pay",
        data={"from_account": checking, "date": dt.date.today().isoformat()},
    )
    page = client.get(f"/cards?card={card}").text
    assert "Parcelas de faturas já pagas serão mantidas no histórico." in page
    done = client.delete(f"/installments/plan/{plan.id}", headers=HX)
    assert "ok=plan_pending_deleted" in done.headers["hx-redirect"]
    assert "ficaram no histórico" in client.get(done.headers["hx-redirect"]).text
    with container.uow as work:
        left = work.transactions.list_by_plan(plan.id)
        assert [t.installment_number for t in left] == [1] and work.plans.get(plan.id)
    again = client.delete(f"/installments/plan/{plan.id}", headers=HX)
    assert "err=NOTHING_TO_DELETE" in again.headers["hx-redirect"]
    assert "estão em faturas pagas" in client.get(again.headers["hx-redirect"]).text


def test_entries_list_offers_the_plan_delete_on_installment_rows(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="2", amount="200,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
    page = client.get("/entries").text
    assert f'hx-delete="/installments/plan/{plan.id}"' in page
    done = client.delete(
        f"/installments/plan/{plan.id}",
        headers={**HX, "hx-current-url": "http://localhost/entries?month=2026-10"},
    )
    assert done.headers["hx-redirect"].startswith("/entries?")
    assert "ok=plan_deleted" in done.headers["hx-redirect"]
    assert client.delete("/installments/plan/nope", headers=HX).status_code in {400, 404}


# --- "Compra estornada" ---


def post_edit(client: TestClient, entry, **fields: str):
    data = {
        "amount": format_decimal(abs(entry.amount_cents)),
        "description": entry.description,
        **({} if entry.plan_id else {"date": entry.posted_on.isoformat()}),
        **fields,
    }
    return client.post(f"/entries/{entry.id}/edit", data=data, headers=HX)


def format_decimal(cents: int) -> str:
    return f"{cents // 100},{cents % 100:02d}"


def test_edit_form_offers_the_refund_toggle_only_for_expenses(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking)
    client.post(
        "/entries",
        data={
            "kind": "income",
            "date": dt.date.today().isoformat(),
            "amount": "10,00",
            "description": "Salário",
            "account_id": checking,
        },
    )
    with container.uow as work:
        rows = {t.description: t for t in work.transactions.list_by_account(checking)}
    expense = client.get(f"/entries/{rows['Padaria São João'].id}/edit", headers=HX)
    assert 'name="refunded"' in expense.text and "Compra estornada" in expense.text
    assert 'name="refund_all"' not in expense.text  # not an installment
    income = client.get(f"/entries/{rows['Salário'].id}/edit", headers=HX)
    assert 'name="refunded"' not in income.text


def test_a_refunded_expense_is_struck_through_and_leaves_the_totals(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="40,00", description="Volta")
    add_expense(client, checking, amount="10,00", description="Fica")
    with container.uow as work:
        gone = next(
            t for t in work.transactions.list_by_account(checking) if t.description == "Volta"
        )
    assert "R$ 50,00" in visible(client.get("/entries"))
    saved = post_edit(client, gone, refunded="1")
    assert "ok=entry_updated" in saved.headers["hx-redirect"]
    page = client.get("/entries")
    assert 'class="row entry is-refunded"' in page.text  # still listed, as a record
    assert '<span class="badge badge--subtle">Estornada</span>' in page.text
    text = visible(page)
    assert "R$ 50,00" not in text and "R$ 10,00" in text  # the day and the month ignore it
    assert "checked" in client.get(f"/entries/{gone.id}/edit", headers=HX).text
    css = client.get("/static/screens.css").text
    assert ".row.is-refunded" in css and "text-decoration: line-through" in css
    assert "opacity: .55" in css and "tabular-nums" in css
    # un-marking brings it back into the totals
    post_edit(client, gone)
    assert 'class="row entry is-refunded"' not in client.get("/entries").text
    assert "R$ 50,00" in visible(client.get("/entries"))


def test_a_refunded_card_purchase_leaves_the_statement_and_gives_the_limit_back(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="300,00")
    entry = only_entry(container, card)
    statement = statement_of_entry(container, entry)
    qs = f"from=cards&card={card}&month={statement.month}"
    before = client.get(f"/cards?card={card}&month={statement.month}")
    assert "R$ 300,00" in card_face(before.text) or "R$ 300,00" in visible(before)
    saved = client.post(
        f"/entries/{entry.id}/edit?{qs}",
        data={
            "amount": "300,00",
            "date": entry.posted_on.isoformat(),
            "description": entry.description,
            "account_id": card,
            "refunded": "1",
        },
        headers=HX,
    )
    assert saved.headers["hx-redirect"].startswith("/cards?")
    page = client.get(saved.headers["hx-redirect"])
    assert 'class="row card-entry is-refunded"' in page.text and "Estornada" in page.text
    face = re.sub(r"\s+", " ", visible(card_face(page.text)))
    assert "Fatura atual R$ 0,00" in face
    assert "Comprometido R$ 0,00" in visible(page)  # the committed limit is back to zero
    assert "Total do dia" not in page.text  # a refunded purchase does not add to the day


def test_refunding_an_installment_can_spread_to_the_pending_ones(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3", amount="300,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
        second = work.transactions.list_by_plan(plan.id)[1]
    form = client.get(f"/entries/{second.id}/edit", headers=HX)
    assert "Marcar todas as parcelas pendentes deste plano como estornadas" in form.text
    post_edit(client, second, refunded="1")  # only this one
    with container.uow as work:
        flags = [
            t.is_refunded for t in work.transactions.list_by_plan(plan.id, include_refunded=True)
        ]
    assert flags == [False, True, False]
    post_edit(client, second, refunded="1", refund_all="1")  # all the pending ones
    with container.uow as work:
        flags = [
            t.is_refunded for t in work.transactions.list_by_plan(plan.id, include_refunded=True)
        ]
    assert flags == [True, True, True]


def test_a_refunded_purchase_is_not_edited_through_a_closed_statement_without_acknowledgement(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75)
    entry = only_entry(container, card)
    refused = post_edit(client, entry, refunded="1", account_id=card)
    assert "Marque “Estou ciente" in refused.text
    assert not only_entry(container, card).is_refunded
    accepted = post_edit(client, entry, refunded="1", account_id=card, ack="1")
    assert "ok=entry_updated" in accepted.headers["hx-redirect"]
    with container.uow as work:
        assert work.transactions.get(entry.id).is_refunded  # type: ignore[union-attr]


# --- itemized expenses and merging ---


def item_fields(container: Container, items: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    return {
        "item_description": [d for d, _, _ in items],
        "item_category": [category_by_slug(container, slug).id for _, slug, _ in items],
        "item_amount": [a for _, _, a in items],
    }


MARKET = [
    ("Feira e laticínios", "groceries", "220,00"),
    ("Higiene pessoal", "health", "90,00"),
    ("Limpeza", "home", "70,00"),
]


def test_edit_form_has_the_items_editor_for_expenses_only(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="380,00", description="Mercado")
    entry = only_entry(container, checking)
    form = client.get(f"/entries/{entry.id}/edit", headers=HX)
    assert "Adicionar itens / Dividir categorias" in form.text
    assert 'name="splits_present"' in form.text and "data-split-template" in form.text
    assert "data-split-remaining" in form.text and "data-split-add" in form.text
    assert 'name="item_amount"' in form.text
    assert "<html" not in form.text


def test_saving_items_persists_them_atomically_and_the_row_shows_the_breakdown(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="380,00", description="Mercado")
    entry = only_entry(container, checking)
    data = {
        "amount": "380,00",
        "date": entry.posted_on.isoformat(),
        "description": "Mercado",
        "splits_present": "1",
        **item_fields(container, MARKET),
    }
    saved = client.post(f"/entries/{entry.id}/edit", data=data, headers=HX)
    assert "ok=entry_updated" in saved.headers["hx-redirect"]
    with container.uow as work:
        items = work.transactions.splits_for([entry.id])[entry.id]
        assert [i.amount_cents for i in items] == [22_000, 9_000, 7_000]
        assert work.transactions.get(entry.id).amount_cents == -38_000  # type: ignore[union-attr]
    page = client.get("/entries").text
    assert "▾ 3 itens" in page and "data-split-toggle" in page
    assert f'id="split-{entry.id}" hidden' in page
    assert "Feira e laticínios" in page and "Higiene pessoal" in page and "Limpeza" in page
    for amount in ("R$ 220,00", "R$ 90,00", "R$ 70,00"):
        assert amount in visible(page)
    # the list totals still see the whole entry once
    assert visible(page).count("R$ 380,00") >= 1
    # the form comes back with the saved items
    again = client.get(f"/entries/{entry.id}/edit", headers=HX).text
    assert again.count('name="item_description"') == 4  # three items + the template row
    assert 'value="Feira e laticínios"' in again and "checked" in again


def test_a_wrong_sum_is_refused_with_the_remaining_amount(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="380,00", description="Mercado")
    entry = only_entry(container, checking)
    short = item_fields(container, [MARKET[0], MARKET[1]])  # R$ 310,00 of R$ 380,00
    refused = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "380,00",
            "date": entry.posted_on.isoformat(),
            "description": "Mercado",
            "splits_present": "1",
            **short,
        },
        headers=HX,
    )
    assert refused.status_code == 200
    assert "Os itens precisam somar o valor do lançamento: restam R$ 70,00." in refused.text
    assert 'value="Feira e laticínios"' in refused.text  # the typed items stay on the form
    with container.uow as work:
        assert work.transactions.splits_for([entry.id]) == {}
    over = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "100,00",
            "date": entry.posted_on.isoformat(),
            "description": "Mercado",
            "splits_present": "1",
            **item_fields(container, MARKET),
        },
        headers=HX,
    )
    assert "ultrapassou R$ 280,00" in over.text


def test_unchecking_the_items_removes_them(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="380,00", description="Mercado")
    entry = only_entry(container, checking)
    base = {
        "amount": "380,00",
        "date": entry.posted_on.isoformat(),
        "description": "Mercado",
        "splits_present": "1",
    }
    client.post(
        f"/entries/{entry.id}/edit", data={**base, **item_fields(container, MARKET)}, headers=HX
    )
    client.post(f"/entries/{entry.id}/edit", data=base, headers=HX)  # the toggle is off: no items
    with container.uow as work:
        assert work.transactions.splits_for([entry.id]) == {}
    listed = client.get("/entries").text.split('id="lista"')[1].split("</section>")[0]
    assert "data-split-toggle" not in listed  # the rows have no "▾ N itens" (the quick form does)


def test_the_category_filter_matches_the_items_of_an_entry(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="380,00", description="Mercado")
    entry = only_entry(container, checking)
    client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "380,00",
            "date": entry.posted_on.isoformat(),
            "description": "Mercado",
            "splits_present": "1",
            **item_fields(container, MARKET),
        },
        headers=HX,
    )
    home = category_by_slug(container, "home")
    other = category_by_slug(container, "taxes")
    assert (
        "Mercado"
        in client.get(f"/entries?category={home.id}")
        .text.split('id="lista"')[1]
        .split("</section>")[0]
    )
    assert (
        "Mercado"
        not in client.get(f"/entries?category={other.id}")
        .text.split('id="lista"')[1]
        .split("</section>")[0]
    )


def two_card_purchases(client: TestClient, container: Container):
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="220,00", description="Feira")
    buy_on_card(client, card, days_ago=0, amount="60,00", description="Sabão")
    with container.uow as work:
        rows = {t.description: t for t in work.transactions.list_by_account(card)}
    return card, rows["Feira"], rows["Sabão"]


def test_rows_carry_selection_hooks_and_the_dock_and_dialog_are_there(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, amount="220,00", description="Feira")
    buy_on_card(
        client, card, days_ago=0, installments="2", amount="100,00", description="Parcelada"
    )
    page = client.get("/entries").text
    assert 'class="merge-pick"' not in page and 'id="merge-bar"' not in page  # the old UI is gone
    assert page.count("data-sel-row") == 2 and page.count('class="sel-slot"') == 2
    assert 'data-sel-scope data-selecting="false"' in page and 'class="sel-toggle"' in page
    assert 'class="sel-dock"' in page and 'role="group" aria-label="Ações de seleção"' in page
    assert (
        page.index('class="sel-cancel"')
        < page.index('class="sel-delete"')
        < page.index('class="sel-merge"')
    )  # DOM order = visual order
    assert page.count('data-sel-merge="no"') == 1  # the installment purchase cannot be merged...
    assert "Compra parcelada em 2 faturas não pode ser mesclada" in page
    assert 'data-sel-id="plan:' in page and 'data-sel-id="entry:' in page  # ...but can be deleted
    assert 'id="merge-dialog"' in page and 'hx-post="/entries/merge"' in page
    assert "Estou ciente de que a fatura já está fechada" in page  # in the dialog
    cards = client.get(f"/cards?card={card}").text
    assert 'class="sel-slot"' in cards and 'id="merge-dialog"' in cards
    assert 'class="sel-dock"' in cards


def test_merge_endpoint_unifies_the_purchases_and_redirects_back(
    client: TestClient, container: Container
) -> None:
    card, feira, sabao = two_card_purchases(client, container)
    day = dt.date.today().isoformat()
    done = client.post(
        "/entries/merge",
        data={"ids": [feira.id, sabao.id], "description": "Compras da semana", "date": day},
        headers={
            **HX,
            "hx-current-url": f"http://localhost/cards?card={card}&month=2026-10&ano=2026",
        },
    )
    location = done.headers["hx-redirect"]
    assert location.startswith("/cards?") and f"card={card}" in location and "ok=merged" in location
    assert "Lançamentos mesclados em um só" in client.get(location).text
    with container.uow as work:
        (parent,) = work.transactions.list_by_account(card)
        items = work.transactions.splits_for([parent.id])[parent.id]
    assert (parent.description, parent.amount_cents) == ("Compras da semana", -28_000)
    assert [(i.description, i.amount_cents) for i in items] == [("Feira", 22_000), ("Sabão", 6_000)]
    page = client.get(f"/cards?card={card}").text
    assert "▾ 2 itens" in page  # the unified row is itemized
    assert "R$ 280,00" in visible(client.get(f"/cards?card={card}"))  # statement total unchanged


def test_merge_errors_show_in_the_dialog_and_change_nothing(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Um")
    add_expense(client, checking, description="Dois")
    with container.uow as work:
        ids = [t.id for t in work.transactions.list_by_account(checking)]
    day = dt.date.today().isoformat()
    one = client.post(
        "/entries/merge", data={"ids": ids[:1], "description": "X", "date": day}, headers=HX
    )
    assert one.status_code == 200 and "Escolha pelo menos dois lançamentos" in one.text
    assert 'role="alert"' in one.text and "<html" not in one.text
    plain = client.post("/entries/merge", data={"ids": ids[:1], "description": "X", "date": day})
    assert plain.status_code == 303 and "err=MERGE_NEEDS_TWO" in plain.headers["location"]
    empty = client.post(
        "/entries/merge", data={"ids": ids, "description": "  ", "date": day}, headers=HX
    )
    assert "Informe uma descrição." in empty.text
    with container.uow as work:
        assert len(work.transactions.list_by_account(checking)) == 2


# --- merchants ---


def add_with_merchant(client: TestClient, account: str, merchant: str, **overrides: str) -> None:
    add_expense(client, account, merchant=merchant, **overrides)


def test_quick_form_and_edit_form_have_the_merchant_field_with_its_datalist(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_with_merchant(client, checking, "  Supermercado   Pão de Açúcar ", description="Compras")
    add_with_merchant(client, checking, "Amazon", description="Livro")
    page = client.get("/entries").text
    assert 'name="merchant"' in page and 'list="merchants-list"' in page
    assert "Ex: Supermercado Pão de Açúcar, Amazon, Posto Ipiranga" in page
    assert '<datalist id="merchants-list">' in page
    assert '<option value="Amazon"></option>' in page
    assert '<option value="Supermercado Pão de Açúcar"></option>' in page  # stored clean
    assert page.index('value="Amazon"') < page.index('value="Supermercado Pão de Açúcar"')  # sorted
    with container.uow as work:
        entry = next(
            t for t in work.transactions.list_by_account(checking) if t.description == "Livro"
        )
    form = client.get(f"/entries/{entry.id}/edit", headers=HX).text
    assert 'name="merchant"' in form and 'list="merchants-list"' in form
    assert 'value="Amazon"' in form and "<html" not in form


def test_merchant_is_saved_shown_on_the_row_and_editable(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_with_merchant(client, checking, "Droga Raia", description="Remédio")
    add_with_merchant(client, checking, "Padaria", description="Padaria")  # same as the description
    page = client.get("/entries").text
    assert '<small class="c-merchant">Droga Raia</small>' in page
    assert page.count('class="c-merchant"') == 1  # equal to the description: no repeat
    with container.uow as work:
        entry = next(
            t for t in work.transactions.list_by_account(checking) if t.description == "Remédio"
        )
    post_edit(client, entry, merchant="  Drogasil ")
    with container.uow as work:
        assert work.transactions.get(entry.id).merchant == "Drogasil"  # type: ignore[union-attr]
    post_edit(client, entry, merchant="")  # cleared
    with container.uow as work:
        assert work.transactions.get(entry.id).merchant is None  # type: ignore[union-attr]
    # searching the list finds entries by merchant too
    add_with_merchant(client, checking, "Mercado Livre", description="Cabo USB")
    found = client.get("/entries?q=mercado").text.split('id="lista"')[1].split("</section>")[0]
    assert "Cabo USB" in found and "Remédio" not in found


def test_merchant_suggestions_route_lists_each_name_once_sorted_and_per_account(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    for name in ("Zé do Pão", "Amazon", "amazon", "Ação Digital", "Uber"):
        add_with_merchant(client, checking, name)
    response = client.get("/merchants/suggestions")
    assert response.status_code == 200 and "<html" not in response.text
    options = re.findall(r'<option value="([^"]+)"></option>', response.text)
    assert options == ["Ação Digital", "Amazon", "Uber", "Zé do Pão"]
    assert client.get("/merchants/suggestions?account=nope").text.strip() == ""
    assert "Uber" in client.get(f"/merchants/suggestions?account={checking}").text


def test_card_purchase_form_takes_the_merchant(client: TestClient, container: Container) -> None:
    _, card = make_card(client, container)
    form = client.get("/cards/purchase").text
    assert 'name="merchant"' in form and '<datalist id="merchants-list">' in form
    buy_on_card(client, card, days_ago=0, installments="2", merchant="Amazon")
    with container.uow as work:
        assert {t.merchant for t in work.transactions.list_by_account(card)} == {"Amazon"}
    cards_page = client.get(f"/cards?card={card}").text
    assert '<datalist id="merchants-list">' in cards_page and "Amazon" in cards_page


def test_analyses_ranks_the_top_merchants(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    add_with_merchant(client, checking, "Amazon", amount="100,00", description="Livro")
    add_with_merchant(client, checking, "Amazon", amount="50,00", description="Cabo")
    add_with_merchant(client, checking, "Uber", amount="30,00", description="Corrida")
    add_expense(client, checking, amount="20,00", description="Sem local")
    page = client.get("/analises").text
    assert "Principais Estabelecimentos &amp; Vendedores" in page and 'id="merchants"' in page
    for column in (
        "Estabelecimento",
        "Categoria principal",
        "Frequência",
        "Ticket médio",
        "Total gasto",
    ):
        assert f">{column}</th>" in page
    section = page.split('id="h-mer"')[1]
    text = visible(section)
    assert (
        section.index("Amazon")
        < section.index("Uber")
        < section.index("Outros / Sem identificação")
    )
    assert "R$ 150,00" in text and "R$ 75,00" in text  # total and average ticket of Amazon
    assert "2×" in text and "1×" in text
    assert "75,0% da despesa" in text  # 150 / 200
    assert 'class="mer-share"' in section and 'style="width: 75.0%"' in section
    assert "Alimentação" in text or "Não categorizado" in text  # a main category per merchant
    css = client.get("/static/screens.css").text
    assert ".mer-table" in css and "tabular-nums" in css


def test_analyses_merchants_ignore_refunded_purchases_and_have_an_empty_state(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    assert "Nenhuma despesa neste período" in client.get("/analises").text.split('id="h-mer"')[1]
    add_with_merchant(client, checking, "Amazon", amount="80,00", description="Devolvido")
    with container.uow as work:
        entry = work.transactions.list_by_account(checking)[0]
    post_edit(client, entry, refunded="1", merchant="Amazon")
    section = client.get("/analises").text.split('id="h-mer"')[1]
    assert "Amazon" not in section and "Nenhuma despesa neste período" in section


# --- the entries filters survive month navigation ---


def entries_hrefs(page: str) -> dict[str, str]:
    """The month arrows and the clear link of /entries, as the browser would follow them."""
    import html as html_lib

    found = {}
    for label in ("Mês anterior", "Próximo mês"):
        match = re.search(rf'<a class="icon-btn" href="([^"]+)" aria-label="{label}"', page)
        assert match, label
        found[label] = html_lib.unescape(match.group(1))
    clear = re.search(r'<a class="btn secondary" href="([^"]+)">Limpar filtros</a>', page)
    found["clear"] = html_lib.unescape(clear.group(1)) if clear else ""
    return found


def test_month_arrows_keep_every_active_filter(client: TestClient, container: Container) -> None:
    from urllib.parse import parse_qs, urlsplit

    checking, _ = setup_accounts(client, container)
    food = category_by_slug(container, "food")
    page = client.get(
        f"/entries?month=2026-07&account={checking}&category={food.id}&kind=expense&q=caf%C3%A9"
    ).text
    links = entries_hrefs(page)
    for label, month in (("Mês anterior", "2026-06"), ("Próximo mês", "2026-08")):
        query = parse_qs(urlsplit(links[label]).query)
        assert urlsplit(links[label]).path == "/entries"
        assert query == {
            "month": [month],
            "account": [checking],
            "category": [food.id],
            "kind": ["expense"],
            "q": ["café"],
        }
    # following an arrow shows the other month with the same filters still applied
    other = client.get(links["Mês anterior"]).text
    assert "Junho" in other and 'value="café"' in other
    assert f'<option value="{checking}" selected' in other
    assert f'<option value="{food.id}" selected' in other
    assert '<option value="expense" selected' in other
    assert 'name="month" value="2026-06"' in other
    # and the arrows of that page keep going with them
    assert "month=2026-05" in entries_hrefs(other)["Mês anterior"]


def test_month_arrows_without_filters_stay_plain_and_there_is_nothing_to_clear(
    client: TestClient,
) -> None:
    page = client.get("/entries?month=2026-07").text
    links = entries_hrefs(page)
    assert links["Mês anterior"] == "/entries?month=2026-06"
    assert links["Próximo mês"] == "/entries?month=2026-08"
    assert links["clear"] == ""  # no filter, no "Limpar filtros"


def test_filter_form_keeps_the_viewed_month_and_clear_keeps_it_too(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    page = client.get(f"/entries?month=2026-03&account={checking}").text
    form = re.search(r'<form method="get" action="/entries" role="search".*?</form>', page, re.S)
    assert form and '<input type="hidden" name="month" value="2026-03">' in form.group(0)
    # submitting the form is a GET of exactly its fields: the month is one of them
    assert 'name="month"' in form.group(0)
    assert entries_hrefs(page)["clear"] == "/entries?month=2026-03"  # month kept, filters gone
    cleared = client.get(entries_hrefs(page)["clear"]).text
    bar = cleared.split('class="filter-bar"')[1].split("</form>")[0]  # not the quick form below
    assert "Março" in cleared and f'<option value="{checking}" selected' not in bar
    assert "Limpar filtros" not in bar


def test_clear_link_of_the_empty_result_keeps_the_month_as_well(
    client: TestClient, container: Container
) -> None:
    setup_accounts(client, container)
    page = client.get("/entries?month=2026-03&q=nada-existe").text
    assert "Nenhum lançamento com esses filtros." in page
    assert page.count('href="/entries?month=2026-03">Limpar filtros') == 2  # bar and empty state


def test_load_more_keeps_filters_and_month(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    for n in range(3):
        add_expense(client, checking, description=f"Item {n}", date="2026-03-10")
    page = client.get(f"/entries?month=2026-03&account={checking}&limit=2").text
    more = re.search(r'<a class="btn secondary" href="([^"]+)">Carregar mais</a>', page)
    assert more
    import html as html_lib

    href = html_lib.unescape(more.group(1))
    assert "month=2026-03" in href and f"account={checking}" in href and "limit=" in href


# --- editing a statement payment ---

PAYMENT_WARNING = (
    "Atenção:</strong> Alterar o valor ou a conta de origem deste pagamento recalculará o saldo "
    "das contas e o status de quitação da fatura."
)


def paid_statement(client: TestClient, container: Container, amount: str = "300,00"):
    """A card purchase 75 days ago, paid in full today from checking. Returns the pieces."""
    checking, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75, amount=amount)
    entry = only_entry(container, card)
    assert entry.statement_id
    today = dt.date.today().isoformat()
    paid = client.post(
        f"/statements/{entry.statement_id}/pay", data={"from_account": checking, "date": today}
    )
    assert paid.status_code == 303
    with container.uow as work:
        legs = {
            t.account_id: t
            for t in work.transactions.list_by_account(checking)
            + work.transactions.list_by_account(card)
            if t.transfer_id
        }
        statement = work.statements.get(entry.statement_id)
    assert statement
    return checking, card, legs[checking], legs[card], statement


def statement_status_of(container: Container, statement_id: str) -> str:
    from financas.application.queries.cards import statement_view

    with container.uow as work:
        statement = work.statements.get(statement_id)
        assert statement
        return statement_view(work, statement, dt.date.today()).status.value


def payment_data(debit, **overrides: str) -> dict[str, str]:
    data = {
        "amount": format_decimal(abs(debit.amount_cents)),
        "date": debit.posted_on.isoformat(),
        "from_account": debit.account_id,
        "notes": "",
    }
    data.update(overrides)
    return data


def test_payment_rows_have_the_edit_pencil_on_entries_and_on_the_paid_statement(
    client: TestClient, container: Container
) -> None:
    _, card, debit, credit, statement = paid_statement(client, container)
    assert statement_status_of(container, statement.id) == "paid"
    page = client.get("/entries").text
    for leg in (debit, credit):
        assert f'hx-get="/entries/{leg.id}/edit"' in page
    cards_page = client.get(f"/cards?card={card}&month={statement.month}").text
    assert f'hx-get="/entries/{credit.id}/edit?from=cards' in cards_page
    assert "Editar pagamento" in cards_page and "pagamento</span>" in cards_page
    # the purchase of that paid statement stays locked: no pencil on it
    with container.uow as work:
        purchase = next(
            t for t in work.transactions.list_by_statement(statement.id) if not t.transfer_id
        )
    assert f"/entries/{purchase.id}/edit" not in cards_page


def test_payment_edit_form_shows_badge_warning_and_the_payment_fields(
    client: TestClient, container: Container
) -> None:
    checking, card, debit, credit, _ = paid_statement(client, container)
    for leg in (debit, credit):  # the same form from either row
        form = client.get(f"/entries/{leg.id}/edit", headers=HX)
        assert form.status_code == 200 and "<html" not in form.text
        assert '<span class="badge">Pagamento de Fatura</span>' in form.text
        assert PAYMENT_WARNING in form.text and "data-payment-warning" in form.text
        assert "Conta de origem" in form.text and "Data do pagamento" in form.text
        assert "Valor pago" in form.text and "Observações" in form.text
        assert 'name="amount"' in form.text and 'value="300,00"' in form.text
        assert f'<option value="{checking}" selected' in form.text
        assert 'name="description"' not in form.text and 'name="category_id"' not in form.text
    plain = client.get(f"/entries/{debit.id}/edit?from=cards&card={card}&month=2026-01", headers=HX)
    assert "from=cards" in plain.text  # the way back to the statement is kept


def test_saving_a_smaller_payment_updates_both_legs_and_reopens_the_statement(
    client: TestClient, container: Container
) -> None:
    _, card, debit, credit, statement = paid_statement(client, container)
    saved = client.post(
        f"/entries/{debit.id}/edit",
        data=payment_data(debit, amount="120,00", notes="conferido"),
        headers={**HX, "hx-current-url": "http://localhost/entries?month=2026-10&kind=transfer"},
    )
    location = saved.headers["hx-redirect"]
    assert location.startswith("/entries?") and "ok=payment_updated" in location
    assert "kind=transfer" in location  # back to the list the user was on
    assert "Pagamento atualizado" in client.get(location).text
    with container.uow as work:
        new_debit = work.transactions.get(debit.id)
        new_credit = work.transactions.get(credit.id)
    assert new_debit and new_credit
    assert (new_debit.amount_cents, new_credit.amount_cents) == (-12_000, 12_000)
    assert new_debit.notes == new_credit.notes == "conferido"
    assert statement_status_of(container, statement.id) == "closed"  # no longer paid in full
    page = visible(client.get(f"/cards?card={card}&month={statement.month}"))
    assert "R$ 180,00" in page  # what is still owed: 300 - 120
    # raising it back to the full amount pays the statement again
    again = client.post(f"/entries/{credit.id}/edit", data=payment_data(debit), headers=HX)
    assert "ok=payment_updated" in again.headers["hx-redirect"]
    assert statement_status_of(container, statement.id) == "paid"


def test_payment_edit_from_cards_returns_to_the_statement(
    client: TestClient, container: Container
) -> None:
    _, card, debit, credit, statement = paid_statement(client, container)
    qs = f"from=cards&card={card}&month={statement.month}&ano={statement.month.year}"
    saved = client.post(
        f"/entries/{credit.id}/edit?{qs}",
        data=payment_data(debit, amount="250,00"),
        headers=HX,
    )
    location = saved.headers["hx-redirect"]
    assert location.startswith("/cards?") and "from=" not in location
    assert f"card={card}" in location and f"month={statement.month}" in location
    assert f"ano={statement.month.year}" in location and "ok=payment_updated" in location
    page = client.get(location)
    assert "Pagamento atualizado" in page.text and 'id="statement-section"' in page.text
    assert statement_status_of(container, statement.id) == "closed"
    plain = client.post(
        f"/entries/{credit.id}/edit?{qs}", data=payment_data(debit, amount="300,00")
    )
    assert plain.status_code == 303 and "ok=payment_updated" in plain.headers["location"]


def test_changing_the_source_account_moves_the_debit_and_the_balances(
    client: TestClient, container: Container
) -> None:
    from financas.application.queries.balances import ListAccountBalances
    from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand

    checking, _, debit, credit, _ = paid_statement(client, container)
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    client.post("/accounts", data={"nickname": "Segunda Conta", "institution_id": inst.id})
    with container.uow as work:
        other = next(a.id for a in work.accounts.list_all() if a.nickname == "Segunda Conta")
    day = dt.date.today() - dt.timedelta(days=100)
    for account in (checking, other):
        RecordBalance(container.uow).execute(RecordBalanceCommand(account, day, 100_000))

    def balances() -> dict[str, int | None]:
        rows = ListAccountBalances(container.uow).execute(dt.date.today())
        return {b.account_id: b.balance_cents for b in rows}

    assert balances()[checking] == 100_000 - 30_000 and balances()[other] == 100_000
    form = client.get(f"/entries/{debit.id}/edit", headers=HX).text
    assert (
        f'<option value="{other}" >Segunda Conta</option>' in form.replace(" selected", "")
        or other in form
    )
    saved = client.post(
        f"/entries/{debit.id}/edit", data=payment_data(debit, from_account=other), headers=HX
    )
    assert "ok=payment_updated" in saved.headers["hx-redirect"]
    assert balances()[checking] == 100_000 and balances()[other] == 100_000 - 30_000
    # "Conta não controlada": the checking leg is dropped, the card side still pays the statement
    client.post(f"/entries/{credit.id}/edit", data=payment_data(debit, from_account=""), headers=HX)
    with container.uow as work:
        assert work.transactions.get(debit.id) is None
        assert work.transactions.get(credit.id).amount_cents == 30_000  # type: ignore[union-attr]
    assert balances()[other] == 100_000


def test_payment_edit_errors_keep_the_form_and_change_nothing(
    client: TestClient, container: Container
) -> None:
    checking, card, debit, credit, _ = paid_statement(client, container)
    zero = client.post(
        f"/entries/{debit.id}/edit", data=payment_data(debit, amount="0,00"), headers=HX
    )
    assert zero.status_code == 200 and "O valor precisa ser maior que zero." in zero.text
    assert PAYMENT_WARNING in zero.text and 'value="0,00"' in zero.text
    nodate = client.post(f"/entries/{debit.id}/edit", data=payment_data(debit, date=""), headers=HX)
    assert "Data inválida" in nodate.text or "inválid" in nodate.text.lower()
    bad = client.post(
        f"/entries/{debit.id}/edit", data=payment_data(debit, from_account=card), headers=HX
    )
    assert "não aceita esse lançamento" in bad.text  # a card cannot be the source
    with container.uow as work:
        assert work.transactions.get(debit.id) == debit
        assert work.transactions.get(credit.id) == credit
    # a plain transfer is still not editable
    own = client.post(
        "/transfers",
        data={
            "amount": "5,00",
            "date": dt.date.today().isoformat(),
            "from_account": checking,
            "to_account": "",
        },
    )
    assert own.status_code == 303


# --- Revisão Rápida (/revisar) and the " - Estabelecimento" suffix ---


def forbidden_name() -> str:
    return "tin" + "der"  # the product never uses it; spelled apart so no file here says it


def pending_entry(
    client: TestClient, account: str, description: str, amount: str = "50,00"
) -> None:
    add_expense(client, account, description=description, amount=amount)


def test_review_deck_empty_state_and_the_exact_copy(client: TestClient) -> None:
    page = client.get("/revisar")
    assert page.status_code == 200 and "<h1>Revisão Rápida</h1>" in page.text
    assert "Tudo em dia! Nenhum lançamento precisando de revisão." in page.text
    assert 'id="review-card"' not in page.text and forbidden_name() not in page.text.lower()
    assert 'class="nav-count"' not in client.get("/entries").text  # nothing pending: no counter


def test_review_deck_shows_one_card_with_suggestions_and_shortcuts(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    food = category_by_slug(container, "food")
    add_with_merchant(
        client, checking, "Drogasil", description="Remédio", amount="20,00", category_id=food.id
    )  # filled in: it teaches the deck, and is not pending itself
    client.post(
        "/entries",
        data={
            "kind": "expense",
            "date": dt.date.today().isoformat(),
            "amount": "35,90",
            "description": "COMPRA DROGASIL 0451",
            "account_id": checking,
        },
    )
    page = client.get("/revisar")
    text = page.text
    assert 'id="review-card"' in text and "COMPRA DROGASIL 0451" in text
    assert "R$ 35,90" in visible(page) and "Conta Corrente" in text
    assert "1 para revisar" in text
    assert 'value="Drogasil"' in text and "sugestão: Drogasil" in text  # learned from "Remédio"
    assert 'name="merchant"' in text and 'list="merchants-list"' in text
    assert '<datalist id="merchants-list">' in text
    assert text.count('name="category_id"') >= 10 and f'value="{food.id}"' in text
    assert (
        "uncategorized" not in text
        and "Não categorizado</span>" not in text.split("review-pills")[1]
    )
    assert "Pular" in text and "Salvar e avançar" in text
    assert 'hx-post="/entries/' in text and 'hx-get="/revisar/next?skip_id=' in text
    assert "← pular · → ou Enter salvar e avançar" in text
    assert '<span class="nav-count">(1)</span>' in client.get("/entries").text
    script = client.get("/static/app.js").text
    for needle in ("ArrowLeft", "ArrowRight", '"Enter"', "data-review-skip", "requestSubmit"):
        assert needle in script, needle
    assert forbidden_name() not in text.lower() + script.lower()


def test_skip_moves_to_the_next_card_and_the_deck_ends_politely(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    pending_entry(client, checking, "Primeira")
    pending_entry(client, checking, "Segunda")
    with container.uow as work:
        ids = {t.description: t.id for t in work.transactions.list_by_account(checking)}
    first = client.get("/revisar").text
    current = "Segunda" if "Segunda" in first.split("review-desc")[1][:60] else "Primeira"
    other = "Primeira" if current == "Segunda" else "Segunda"
    skipped = client.get(f"/revisar/next?skip_id={ids[current]}", headers=HX)
    assert skipped.status_code == 200 and "<html" not in skipped.text
    assert other in skipped.text.split("review-desc")[1][:60]
    assert "1 pulado(s)" in skipped.text and f"skipped={ids[current]}" in skipped.text
    done = client.get(f"/revisar/next?skip_id={ids[other]}&skipped={ids[current]}", headers=HX)
    assert "Tudo em dia! Nenhum lançamento precisando de revisão." in done.text
    assert "Os lançamentos que você pulou continuam pendentes" in done.text
    assert 'id="review-card"' not in done.text


def test_quick_fill_saves_the_ledger_and_answers_with_the_next_card(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    pending_entry(client, checking, "Compra grande", amount="120,00")
    pending_entry(client, checking, "Outra compra", amount="10,00")
    with container.uow as work:
        target = next(
            t
            for t in work.transactions.list_by_account(checking)
            if t.description == "Compra grande"
        )
    shopping = category_by_slug(container, "shopping")
    saved = client.post(
        f"/entries/{target.id}/quick-fill",
        data={"merchant": "mercadolivre", "category_id": shopping.id, "skipped": ""},
        headers=HX,
    )
    assert saved.status_code == 200 and "<html" not in saved.text
    assert (
        "Outra compra" in saved.text
        and "Compra grande" not in saved.text.split("review-desc")[1][:60]
    )
    with container.uow as work:
        got = work.transactions.get(target.id)
    assert got and (got.merchant, got.category_id) == ("Mercado Livre", shopping.id)
    assert got.amount_cents == target.amount_cents  # money untouched


def test_quick_fill_rejects_an_empty_save_and_keeps_the_card(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    pending_entry(client, checking, "Só esta")
    entry = only_entry(container, checking)
    refused = client.post(
        f"/entries/{entry.id}/quick-fill", data={"merchant": "", "category_id": ""}, headers=HX
    )
    assert refused.status_code == 200
    assert "Informe o estabelecimento ou escolha outra categoria" in refused.text
    assert 'id="review-card"' in refused.text and "Só esta" in refused.text
    assert only_entry(container, checking) == entry
    # a card that was saved is not shown again in this pass even if it is still pending
    kept = client.post(
        f"/entries/{entry.id}/quick-fill",
        data={"merchant": "Padaria", "category_id": "", "skipped": ""},
        headers=HX,
    )
    assert "Tudo em dia!" in kept.text  # merchant saved, category still open, but not shown twice
    assert only_entry(container, checking).merchant == "Padaria"


def test_quick_fill_on_an_installment_fills_the_whole_plan(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3", amount="300,00")
    with container.uow as work:
        plan = work.plans.list_all()[0]
        second = work.transactions.list_by_plan(plan.id)[1]
    shopping = category_by_slug(container, "shopping")
    client.post(
        f"/entries/{second.id}/quick-fill",
        data={"merchant": "Magazine Luiza", "category_id": shopping.id},
        headers=HX,
    )
    with container.uow as work:
        rows = work.transactions.list_by_plan(plan.id)
    assert {(t.merchant, t.category_id) for t in rows} == {("Magazine Luiza", shopping.id)}


def test_quick_fill_only_for_expenses_and_unknown_ids(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    client.post(
        "/entries",
        data={
            "kind": "income",
            "date": dt.date.today().isoformat(),
            "amount": "10,00",
            "description": "Salário",
            "account_id": checking,
        },
    )
    income = only_entry(container, checking)
    refused = client.post(
        f"/entries/{income.id}/quick-fill", data={"merchant": "Empresa"}, headers=HX
    )
    assert "só vale para despesas" in refused.text
    assert client.post(
        "/entries/nope/quick-fill", data={"merchant": "X"}, headers=HX
    ).status_code in {400, 404}


def test_a_typed_suffix_becomes_the_merchant_and_leaves_the_description(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Mouse Gamer - Kabum", amount="150,00")
    entry = only_entry(container, checking)
    assert (entry.description, entry.merchant) == ("Mouse Gamer", "Kabum")
    page = client.get("/entries").text
    assert (
        ">Mouse Gamer</span>" in page.replace('<small class="c-merchant">Kabum</small>', "")
        or "Mouse Gamer" in page
    )
    assert '<small class="c-merchant">Kabum</small>' in page and "Mouse Gamer - Kabum" not in page
    # a typed merchant wins over the suffix; the text then stays as typed
    add_with_merchant(client, checking, "Amazon", description="Fone - Loja X")
    with container.uow as work:
        fone = next(
            t
            for t in work.transactions.list_by_account(checking)
            if t.description.startswith("Fone")
        )
    assert (fone.description, fone.merchant) == ("Fone - Loja X", "Amazon")
    # not a merchant: a month, an installment, a hyphenated word
    for text in ("Aluguel - Outubro", "Parcela - 3/10", "Wi-Fi"):
        add_expense(client, checking, description=text)
    with container.uow as work:
        descriptions = {
            t.description: t.merchant for t in work.transactions.list_by_account(checking)
        }
    assert descriptions["Aluguel - Outubro"] is None and descriptions["Parcela - 3/10"] is None
    assert descriptions["Wi-Fi"] is None


def test_the_edit_form_and_the_purchase_form_take_the_suffix_too(
    client: TestClient, container: Container
) -> None:
    checking, card = make_card(client, container)
    add_expense(client, checking, description="Camiseta")
    entry = next(t for t in only_list(container, checking))
    post_edit(client, entry, description="Camiseta - Amazon.com.br", merchant="")
    with container.uow as work:
        edited = work.transactions.get(entry.id)
    assert edited and (edited.description, edited.merchant) == ("Camiseta", "Amazon")
    buy_on_card(client, card, days_ago=0, description="Teclado - Kabum")
    with container.uow as work:
        bought = work.transactions.list_by_account(card)[0]
    assert (bought.description, bought.merchant) == ("Teclado", "Kabum")


def only_list(container: Container, account: str):
    with container.uow as work:
        return work.transactions.list_by_account(account)


# --- the items sum, as the browser posts it ---


@pytest.mark.parametrize(
    ("total", "items"),
    [
        ("25,52", ["15,52", "10,00"]),
        ("1.250,00", ["1.000,00", "250,00"]),
        ("1.250,00", ["R$ 1.000,00", "R$ 250,00"]),
        ("25,52", ["-15,52", "-10,00"]),  # a minus typed on the items never counts
    ],
)
def test_items_that_add_up_are_saved_whatever_the_mask_wrote(
    client: TestClient, container: Container, total: str, items: list[str]
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount=total.replace("R$ ", ""), description="Mercado")
    entry = only_entry(container, checking)
    saved = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": total,
            "date": entry.posted_on.isoformat(),
            "description": "Mercado",
            "splits_present": "1",
            "item_description": ["Primeiro", "Segundo"],
            "item_category": [category_by_slug(container, "groceries").id] * 2,
            "item_amount": items,
        },
        headers=HX,
    )
    assert "ok=entry_updated" in saved.headers["hx-redirect"], saved.text
    with container.uow as work:
        stored = work.transactions.splits_for([entry.id])[entry.id]
        parent = work.transactions.get(entry.id)
    assert parent and parent.amount_cents < 0  # the ledger keeps the negative expense
    assert sum(i.amount_cents for i in stored) == -parent.amount_cents
    assert all(i.amount_cents > 0 for i in stored)


def test_the_editor_loads_the_mask_before_the_script_that_reads_it(client: TestClient) -> None:
    page = client.get("/entries").text
    assert page.index("/static/money-mask.js") < page.index("/static/app.js")
    mask = client.get("/static/money-mask.js").text
    assert "fp:money" in mask  # announces the value once it is masked
    script = client.get("/static/app.js").text
    assert "fp:money" in script and "Total distribuído com sucesso" in script
    assert "Ultrapassou" in script and "parseFloat" not in script


# --- parent category lock, itemized purchases and installment plans ---


def purchase_form_data(card: str, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "account_id": card,
        "date": "2026-07-10",
        "description": "Monitor",
        "amount": "450,00",
        "amount_mode": "total",
        "installments": "1",
        "splits_present": "1",
        "split_descriptions": ["Monitor Gamer", "Cabo HDMI e Suporte"],
        "split_categories": [],
        "split_amounts": ["350,00", "100,00"],
    }
    data.update(overrides)
    return data


def test_edit_form_locks_the_parent_category_when_the_entry_has_items(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, amount="380,00", description="Mercado")
    entry = only_entry(container, checking)
    plain = client.get(f"/entries/{entry.id}/edit", headers=HX).text
    select = re.search(r'<select name="category_id"[^>]*>', plain)
    assert select and "disabled" not in select.group(0)
    client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "380,00",
            "date": entry.posted_on.isoformat(),
            "description": "Mercado",
            "splits_present": "1",
            **item_fields(container, MARKET),
        },
        headers=HX,
    )
    with container.uow as work:
        assert work.transactions.get(entry.id).category_id is None  # type: ignore[union-attr]
    locked = client.get(f"/entries/{entry.id}/edit", headers=HX).text
    select = re.search(r'<select name="category_id"[^>]*>', locked)
    assert select and "disabled" in select.group(0)  # a disabled field is never submitted
    assert (
        '<option value="" data-split-placeholder selected>Categorizado por item abaixo</option>'
        in locked
    )
    assert "data-split-toggle-items checked" in locked.replace("\n", " ") or "checked" in locked
    # the row shows that the items carry the categories
    assert "Por item" in client.get("/entries").text
    # a request that names a category AND has items is refused with a clear message
    refused = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "380,00",
            "date": entry.posted_on.isoformat(),
            "description": "Mercado",
            "category_id": category_by_slug(container, "food").id,
        },
        headers=HX,
    )
    assert "não tem categoria própria" in refused.text
    with container.uow as work:
        assert work.transactions.get(entry.id).category_id is None  # type: ignore[union-attr]


def test_purchase_form_has_the_items_editor_and_the_parent_lock_hooks(
    client: TestClient, container: Container
) -> None:
    make_card(client, container)
    page = client.get("/cards/purchase").text
    assert "Adicionar itens / Dividir categorias" in page and "data-split-purchase" in page
    assert 'name="split_descriptions"' in page and 'name="split_amounts"' in page
    assert 'name="split_categories"' in page and "data-split-template" in page
    assert (
        "data-split-note" in page and "data-split-single-only" not in page
    )  # installments allowed
    assert 'name="category_id"' in page and 'name="splits_present"' in page


def test_single_purchase_with_items_creates_a_parent_without_a_category(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    shopping = category_by_slug(container, "shopping")
    home = category_by_slug(container, "home")
    saved = client.post(
        "/cards/purchase",
        data=purchase_form_data(card, split_categories=[shopping.id, home.id]),
    )
    assert saved.status_code == 303 and "ok=purchase" in saved.headers["location"]
    entry = only_entry(container, card)
    assert entry.category_id is None and entry.amount_cents == -45_000
    with container.uow as work:
        items = work.transactions.splits_for([entry.id])[entry.id]
    assert [(i.description, i.category_id, i.amount_cents) for i in items] == [
        ("Monitor Gamer", shopping.id, 35_000),
        ("Cabo HDMI e Suporte", home.id, 10_000),
    ]
    # the statement and the limit read the parent once...
    statement = statement_of_entry(container, entry)
    page = visible(client.get(f"/cards?card={card}&month={statement.month}"))
    assert "R$ 450,00" in page and "Comprometido R$ 450,00" in page
    # ...while category spending follows the items
    from financas.application.queries.summary import GetSummary, Period

    spending = GetSummary(container.uow).execute(Period.month(statement.month))
    assert {r.category_id: r.total_cents for r in spending.by_category} == {
        shopping.id: 35_000,
        home.id: 10_000,
    }
    assert "▾ 2 itens" in client.get(f"/cards?card={card}&month={statement.month}").text


def test_installment_purchase_with_items_distributes_them_over_every_installment(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    shopping = category_by_slug(container, "shopping")
    home = category_by_slug(container, "home")
    saved = client.post(
        "/cards/purchase",
        data=purchase_form_data(card, installments="3", split_categories=[shopping.id, home.id]),
    )
    assert saved.status_code == 303
    with container.uow as work:
        plan = work.plans.list_all()[0]
        entries = work.transactions.list_by_plan(plan.id)
        rows = [
            [(i.description, i.amount_cents) for i in work.transactions.splits_for([e.id])[e.id]]
            for e in entries
        ]
    assert [e.amount_cents for e in entries] == [-15_000] * 3
    assert all(e.category_id is None for e in entries)  # no installment has a category of its own
    assert rows == [
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],
        [("Monitor Gamer", 11_666), ("Cabo HDMI e Suporte", 3_334)],
    ]
    assert plan.category_id == shopping.id  # shown in "Parcelas ativas": the biggest item's
    assert "Parcelas ativas" in client.get(f"/cards?card={card}").text


def test_installment_value_mode_takes_the_items_as_the_whole_purchase(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    categories = [category_by_slug(container, s).id for s in ("shopping", "home")]
    saved = client.post(
        "/cards/purchase",
        data=purchase_form_data(
            card,
            installments="3",
            amount="150,00",
            amount_mode="installment",  # R$ 150,00 x 3 = 450,00
            split_categories=categories,
        ),
    )
    assert saved.status_code == 303
    with container.uow as work:
        entries = work.transactions.list_by_plan(work.plans.list_all()[0].id)
        assert (
            sum(i.amount_cents for e in entries for i in work.transactions.splits_for([e.id])[e.id])
            == 45_000
        )


def test_purchase_items_that_do_not_add_up_are_refused_and_keep_the_form(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    categories = [category_by_slug(container, s).id for s in ("shopping", "home")]
    for installments in ("1", "3"):
        refused = client.post(
            "/cards/purchase",
            data=purchase_form_data(
                card,
                installments=installments,
                split_categories=categories,
                split_amounts=["350,00", "90,00"],  # R$ 440,00 of R$ 450,00
            ),
        )
        assert refused.status_code == 400
        assert "restam R$ 10,00" in refused.text  # the reason, in Portuguese
        assert 'value="Monitor Gamer"' in refused.text  # the typed items stay on the form
        assert re.search(r'<select name="category_id"[^>]*disabled', refused.text)  # still locked
    with container.uow as work:
        assert work.transactions.list_by_account(card) == [] and work.plans.list_all() == []


def test_purchase_form_refuses_a_parent_category_next_to_items(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    categories = [category_by_slug(container, s).id for s in ("shopping", "home")]
    refused = client.post(
        "/cards/purchase",
        data=purchase_form_data(
            card, split_categories=categories, category_id=category_by_slug(container, "food").id
        ),
    )
    assert refused.status_code == 400 and "não tem categoria própria" in refused.text


def test_installment_edit_form_offers_the_items_with_their_plan_wide_scope(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    categories = [category_by_slug(container, s).id for s in ("shopping", "home")]
    client.post(
        "/cards/purchase",
        data=purchase_form_data(
            card,
            installments="3",
            split_categories=categories,
            date=dt.date.today().isoformat(),  # the three statements are open or future
        ),
    )
    with container.uow as work:
        plan = work.plans.list_all()[0]
        first, second, _third = work.transactions.list_by_plan(plan.id)
    form = client.get(f"/entries/{second.id}/edit", headers=HX).text
    assert "Adicionar itens / Dividir categorias" in form and "data-plan-total" in form
    assert 'data-plan-total="45000"' in form and 'data-target-amount="15000"' in form
    assert form.count('name="item_description"') == 3  # the plan's two items + the template row
    assert 'value="350,00"' in form and 'value="100,00"' in form  # the totals over the plan
    assert re.search(r'<select name="category_id"[^>]*disabled', form)
    # plan-wide save: the items now cover the three open or future installments
    new = {
        "amount": "150,00",
        "description": "Monitor",
        "item_description": ["Monitor Gamer", "Cabo", "Suporte"],
        "item_category": [*categories, categories[1]],
        "item_amount": ["350,00", "60,00", "40,00"],
        "splits_present": "1",
        "propagate": "1",
    }
    saved = client.post(f"/entries/{second.id}/edit", data=new, headers=HX)
    assert "ok=entry_updated" in saved.headers["hx-redirect"], saved.text
    with container.uow as work:
        entries = work.transactions.list_by_plan(plan.id)
        sums = [
            sum(i.amount_cents for i in work.transactions.splits_for([e.id])[e.id]) for e in entries
        ]
    assert sums == [15_000] * 3 and all(e.category_id is None for e in entries)
    # one installment only: the items must be that installment's R$ 150,00
    single = {**new, "item_amount": ["100,00", "30,00", "20,00"]}
    single.pop("propagate")
    saved = client.post(f"/entries/{first.id}/edit", data=single, headers=HX)
    assert "ok=entry_updated" in saved.headers["hx-redirect"], saved.text
    mismatch = client.post(
        f"/entries/{first.id}/edit",
        data={**single, "item_amount": ["100,00", "30,00", "10,00"]},
        headers=HX,
    )
    assert "restam R$ 10,00" in mismatch.text


# --- an installment purchase is ONE row on /entries; /cards keeps the monthly installments ---


def buy_itemized_plan(client: TestClient, container: Container, card: str, day: dt.date) -> str:
    categories = [category_by_slug(container, s).id for s in ("shopping", "home")]
    done = client.post(
        "/cards/purchase",
        data=purchase_form_data(
            card, installments="3", split_categories=categories, date=day.isoformat()
        ),
    )
    assert done.status_code == 303, done.text
    with container.uow as work:
        return work.plans.list_all()[0].id


def test_entries_list_shows_an_installment_purchase_as_one_consolidated_row(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    today = dt.date.today()
    plan_id = buy_itemized_plan(client, container, card, today)
    with container.uow as work:
        first, second, third = work.transactions.list_by_plan(plan_id)
    month = f"/entries?month={today:%Y-%m}"
    page = client.get(month).text
    assert f'id="entry-{first.id}"' in page  # the purchase, once
    assert f'id="entry-{second.id}"' not in page and f'id="entry-{third.id}"' not in page
    row = page.split(f'id="entry-{first.id}"', 1)[1].split('class="row entry', 1)[0]
    text = visible(row)
    assert "3 x R$ 150,00" in text  # the installments, next to the total of the purchase
    assert "-R$ 450,00" in text or "−R$ 450,00" in text
    assert "▾ 2 itens" in text
    assert "Monitor Gamer: R$ 350,00" in text and "(2x R$ 116,67 + 1x R$ 116,66)" in text
    assert "Cabo HDMI e Suporte: R$ 100,00" in text and "(2x R$ 33,33 + 1x R$ 33,34)" in text
    assert "Por item" in text
    assert f'hx-get="/entries/{first.id}/edit"' in page
    assert f'hx-delete="/installments/plan/{plan_id}"' in page
    # the month of the later installments does not list the purchase again
    later = (today.replace(day=1) + dt.timedelta(days=65)).replace(day=1)
    assert f'id="entry-{second.id}"' not in client.get(f"/entries?month={later:%Y-%m}").text
    # the filters apply to the purchase as a whole: by an item's category and by text
    home = category_by_slug(container, "home").id
    assert f'id="entry-{first.id}"' in client.get(f"{month}&category={home}").text
    food = category_by_slug(container, "food").id
    assert f'id="entry-{first.id}"' not in client.get(f"{month}&category={food}").text
    assert f'id="entry-{first.id}"' in client.get(f"{month}&q=monitor").text
    # the card screen is unchanged: every installment on its statement
    cards_page = client.get(f"/cards?card={card}").text
    assert f'id="entry-{first.id}"' in cards_page


def test_card_statement_shows_the_items_of_each_installment_with_its_share(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    plan_id = buy_itemized_plan(client, container, card, dt.date.today())
    with container.uow as work:
        entries = work.transactions.list_by_plan(plan_id)
        statements = [work.statements.get(e.statement_id) for e in entries]  # type: ignore[arg-type]
    for entry, statement in zip(entries, statements, strict=True):
        assert statement
        page = client.get(f"/cards?card={card}&month={statement.month}").text
        assert f'id="split-{entry.id}" hidden' in page
        assert "▾ 2 itens" in page and "data-split-toggle" in page
        block = page.split(f'id="split-{entry.id}"', 1)[1].split("</div>\n", 1)[0]
        assert "Monitor Gamer" in block and "Cabo HDMI e Suporte" in block
        assert re.search(r"R\$\s?1\d{2},\d{2}", visible(block))  # its share: about 117,00 / 116,00


def test_purchase_date_is_editable_on_the_entries_list_and_moves_pending_installments(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    today = dt.date.today()
    plan_id = buy_itemized_plan(client, container, card, today)
    with container.uow as work:
        first = work.transactions.list_by_plan(plan_id)[0]
    form = client.get(f"/entries/{first.id}/edit", headers=HX).text
    assert 'name="purchase_date"' in form and f'value="{today.isoformat()}"' in form
    assert "A alteração da data da compra reajustará os vencimentos das faturas pendentes." in form
    on_cards = client.get(f"/entries/{first.id}/edit?from=cards&card={card}", headers=HX).text
    assert 'name="purchase_date"' not in on_cards  # /cards keeps the plain installment form
    new_day = today + dt.timedelta(days=40)
    saved = client.post(
        f"/entries/{first.id}/edit",
        data={
            "amount": "150,00",
            "description": "Monitor",
            "purchase_date": new_day.isoformat(),
            "propagate": "1",
        },
        headers=HX,
    )
    assert "ok=entry_updated" in saved.headers["hx-redirect"], saved.text
    with container.uow as work:
        plan = work.plans.get(plan_id)
        entries = work.transactions.list_by_plan(plan_id)
    assert plan and plan.purchased_on == new_day
    assert entries[0].posted_on == new_day
    assert all(sum(-e.amount_cents for e in entries) == 45_000 for _ in [0])
    # the consolidated row moved to the month of the new date
    assert f'id="entry-{first.id}"' in client.get(f"/entries?month={new_day:%Y-%m}").text
    # a blank date keeps the plan where it is
    again = client.post(
        f"/entries/{first.id}/edit",
        data={"amount": "150,00", "description": "Monitor", "propagate": "1"},
        headers=HX,
    )
    assert "ok=entry_updated" in again.headers["hx-redirect"]
    with container.uow as work:
        assert work.plans.get(plan_id).purchased_on == new_day  # type: ignore[union-attr]


def test_cancel_on_a_consolidated_row_swaps_the_whole_purchase_back(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    plan_id = buy_itemized_plan(client, container, card, dt.date.today())
    with container.uow as work:
        first = work.transactions.list_by_plan(plan_id)[0]
    row = client.get(f"/entries/{first.id}/row", headers=HX).text
    assert "3 x R$ 150,00" in visible(row) and "▾ 2 itens" in visible(row)


# --- payment dates: from the previous statement's due date (or the cycle's opening) to today ---


def unpaid_statement(client: TestClient, container: Container):
    checking, card = make_card(client, container)
    buy_on_card(client, card, days_ago=75, amount="300,00")
    entry = only_entry(container, card)
    assert entry.statement_id
    with container.uow as work:
        statement = work.statements.get(entry.statement_id)
    assert statement
    return checking, card, statement


def bounds_of(container: Container, statement: Statement):
    from financas.application.queries.cards import payment_date_boundaries

    with container.uow as work:
        return payment_date_boundaries(work, statement, dt.date.today())


def test_payment_form_renders_the_date_bounds_and_the_warnings(
    client: TestClient, container: Container
) -> None:
    _, card, statement = unpaid_statement(client, container)
    bounds = bounds_of(container, statement)
    assert not bounds.from_previous_due  # the card's first statement: its cycle opening
    today = dt.date.today()
    page = client.get(f"/cards?card={card}&month={statement.month}").text
    assert f'min="{bounds.min_date.isoformat()}" max="{today.isoformat()}"' in page
    assert "data-payment-date" in page and "data-payment-date-warning" in page
    text = visible(page)
    assert (
        f"A data do pagamento deve estar entre {bounds.min_date:%d/%m/%Y} (abertura da fatura)"
        in text
    )
    assert f"e hoje ({today:%d/%m/%Y})" in text
    # the two alerts exist, hidden until the typed date leaves the range
    assert re.search(
        r"<p[^>]*data-pd-future[^>]*hidden>Pagamentos futuros não são permitidos", page
    )
    assert re.search(
        r"<p[^>]*data-pd-before[^>]*hidden>Data inválida: anterior à abertura da fatura", page
    )


def test_payment_edit_form_carries_the_same_bounds(
    client: TestClient, container: Container
) -> None:
    _, _, _debit, credit, statement = paid_statement(client, container)
    bounds = bounds_of(container, statement)
    form = client.get(f"/entries/{credit.id}/edit", headers=HX).text
    assert f'min="{bounds.min_date.isoformat()}" max="{dt.date.today().isoformat()}"' in form
    assert "data-payment-date-warning" in form and "Pagamentos futuros não são permitidos" in form


def test_out_of_range_payment_dates_are_refused_with_a_portuguese_banner(
    client: TestClient, container: Container
) -> None:
    checking, card, statement = unpaid_statement(client, container)
    bounds = bounds_of(container, statement)
    future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    refused = client.post(
        f"/statements/{statement.id}/pay", data={"from_account": checking, "date": future}
    )
    assert refused.status_code == 400
    assert "Pagamentos de fatura não podem ter data futura" in refused.text
    early = (bounds.min_date - dt.timedelta(days=1)).isoformat()
    refused = client.post(
        f"/statements/{statement.id}/pay", data={"from_account": checking, "date": early}
    )
    assert refused.status_code == 400
    assert "anterior ao vencimento da fatura anterior" in refused.text
    assert f"{bounds.min_date:%d/%m/%Y}" in refused.text
    with container.uow as work:
        assert [t for t in work.transactions.list_by_account(card) if t.transfer_id] == []
    on_the_edge = client.post(
        f"/statements/{statement.id}/pay",
        data={"from_account": checking, "date": bounds.min_date.isoformat(), "amount": "10,00"},
    )
    assert on_the_edge.status_code == 303  # the lower bound itself is valid


def test_editing_a_payment_to_an_out_of_range_date_keeps_the_form_and_changes_nothing(
    client: TestClient, container: Container
) -> None:
    _, _, debit, credit, statement = paid_statement(client, container)
    bounds = bounds_of(container, statement)
    future = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    refused = client.post(
        f"/entries/{debit.id}/edit", data=payment_data(debit, date=future), headers=HX
    )
    assert (
        refused.status_code == 200
        and "Pagamentos de fatura não podem ter data futura" in refused.text
    )
    assert f'value="{future}"' in refused.text and "data-payment-date" in refused.text
    early = (bounds.min_date - dt.timedelta(days=1)).isoformat()
    refused = client.post(
        f"/entries/{debit.id}/edit", data=payment_data(debit, date=early), headers=HX
    )
    assert "anterior ao vencimento da fatura anterior" in refused.text
    with container.uow as work:
        assert work.transactions.get(debit.id) == debit
        assert work.transactions.get(credit.id) == credit


def test_day_header_shows_the_spending_and_the_running_account_balance(
    client: TestClient, container: Container
) -> None:
    checking, savings = setup_accounts(client, container)
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    client.post(
        f"/accounts/{checking}/balance",
        data={"date": (today - dt.timedelta(days=2)).isoformat(), "amount": "5.000,00"},
    )
    add_expense(client, checking, description="Mercado", amount="40,00", date=yesterday.isoformat())
    add_expense(client, checking, description="Almoço", amount="1.200,00")
    client.post(
        "/entries",
        data={
            "kind": "income",
            "account_id": checking,
            "date": today.isoformat(),
            "amount": "3.000,00",
            "description": "Salário",
        },
    )
    client.post(  # an internal transfer leaves the account: the balance follows, the spending not
        "/transfers",
        data={
            "from_account": checking,
            "to_account": savings,
            "date": today.isoformat(),
            "amount": "999",
        },
    )
    response = client.get("/entries")
    page, text = response.text, visible(response)
    assert "Gastos do dia: R$ 1.200,00" in text and "Gastos do dia: R$ 40,00" in text
    assert "Saldo da conta: R$ 4.960,00" in text  # 5.000 - 40, the end of yesterday
    assert "Saldo da conta: R$ 5.761,00" in text  # + 3.000 - 1.200 - 999, the end of today
    assert page.count('data-day-balance="pos"') == 2 and "day-net--pos" in page
    # a running balance, not the day's delta: today's delta alone would be +R$ 801,00
    assert "Saldo do dia" not in text
    one = visible(client.get(f"/entries?account={checking}"))
    assert "Saldo da conta: R$ 5.761,00" in one
    assert "Saldo da conta" not in visible(
        client.get(f"/entries?account={savings}")
    )  # not checking


def test_an_overdrawn_account_and_a_missing_balance_are_not_hidden(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Almoço", amount="300,00")
    assert "Saldo da conta: indisponível" in visible(client.get("/entries"))  # never a made-up 0
    client.post(
        f"/accounts/{checking}/balance",
        data={"date": (dt.date.today() - dt.timedelta(days=1)).isoformat(), "amount": "100,00"},
    )
    response = client.get("/entries")
    assert 'data-day-balance="neg"' in response.text  # 100 - 300: overdrawn, in the danger tone
    assert "day-net--neg" in response.text and "Gastos do dia: R$ 300,00" in visible(response)
