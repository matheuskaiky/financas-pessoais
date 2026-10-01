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
