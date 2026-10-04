"""Front v3, package 4: Carta do mês, E se… and the command palette (⌘K)."""

import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakes import FixedClock
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
from financas.domain.models import AccountKind, TransactionKind
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings
from financas.interfaces.web import nav
from financas.interfaces.web.app import create_app

HEADERS = {"host": "localhost", "origin": "http://localhost"}
D = dt.date
TODAY = D(2026, 10, 2)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'data' / 'f.db'}",
        data_dir=tmp_path / "data",
        _env_file=None,  # type: ignore[call-arg]
    )
    c = Container(settings)
    c.__dict__["clock"] = FixedClock(TODAY)  # cached_property: a fixed "today" for the whole app
    c.migrate()
    seed_categories(c.uow)
    return c


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(
        create_app(container), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )


def category(container: Container, slug: str) -> str:
    with container.uow as work:
        found = work.categories.get_by_slug(slug)
        assert found is not None
        return found.id


@pytest.fixture
def busy(container: Container) -> Container:
    """One bank, a checking account, a card (limit 12,000.00) and a September of entries."""
    u = container.uow
    bank = CreateInstitution(u).execute(CreateInstitutionCommand(name="Banco do Brasil"))
    cc = CreateAccount(u).execute(
        CreateAccountCommand(
            AccountKind.CHECKING,
            bank.id,
            "Conta BB",
            opening_balance_cents=760_045,
            opening_balance_on=D(2026, 9, 30),
        )
    )
    card = CreateAccount(u).execute(
        CreateAccountCommand(
            AccountKind.CREDIT_CARD,
            bank.id,
            "BB Ourocard",
            closing_days_before_due=11,
            due_day=5,
            credit_limit_cents=1_200_000,
        )
    )

    def add(day: dt.date, kind: TransactionKind, cents: int, slug: str, desc: str) -> None:
        RegisterTransaction(u).execute(
            RegisterTransactionCommand(
                account_id=cc.id,
                posted_on=day,
                kind=kind,
                amount_cents=cents,
                description=desc,
                category_id=category(container, slug),
            )
        )

    for month, total in ((6, 672_000), (7, 648_000), (8, 615_000)):
        add(D(2026, month, 8), TransactionKind.EXPENSE, total, "home", "Aluguel")
    add(D(2026, 9, 5), TransactionKind.INCOME, 985_000, "salary", "Salário")
    add(D(2026, 9, 8), TransactionKind.EXPENSE, 118_640, "groceries", "Mercado")
    add(D(2026, 9, 9), TransactionKind.EXPENSE, 84_230, "food", "Café São João")
    SetCategoryBudgets(u).execute({category(container, "food"): 70_000})
    RegisterCardPurchase(u).execute(
        CardPurchaseCommand(
            account_id=card.id,
            description="Fogão",
            purchased_on=D(2026, 9, 10),
            total_cents=231_846,
            category_id=category(container, "home"),
        )
    )
    RecordBalance(u).execute(RecordBalanceCommand(cc.id, D(2026, 9, 30), 760_045))
    return container


def body(html: str) -> str:
    assert "</title>" in html and "</head>" in html
    return html.split("</head>", 1)[1]


# --- Carta do mês --------------------------------------------------------------------------------


def test_carta_is_in_the_navigation_with_its_dot(client: TestClient) -> None:
    entry = next(e for e in nav.entries() if e.id == "carta")
    assert (entry.href, entry.group, entry.order, entry.badge) == ("/carta", "main", 30, True)
    sidebar = client.get("/carta").text
    sidebar = sidebar[sidebar.index('<nav class="sidebar ink"') : sidebar.index("</nav>")]
    assert 'href="/carta"' in sidebar and "nav-badge" in sidebar
    assert 'aria-current="page"' in sidebar


def test_carta_on_an_empty_database_is_an_empty_state(client: TestClient) -> None:
    response = client.get("/carta")
    assert response.status_code == 200
    page = body(response.text)
    assert "Carta de setembro" in response.text
    assert "Sem lançamentos em setembro" in page
    assert "Nada saiu deste computador" in page  # the off-state notice is always there
    assert "Assistente desligado" in page


def test_carta_reads_the_month_in_words_with_notes(busy: Container, client: TestClient) -> None:
    page = body(client.get("/carta").text)
    assert "Setembro fechou" in page
    assert "Entraram" in page and "R$ 9.850,00" in page
    assert 'class="num-v"' in page and 'data-note="1"' in page
    assert 'id="nota-1"' in page and "Receitas − despesas" in page
    assert "Alimentação" in page and "passou da meta" in page  # food: 842,30 over a 700,00 goal
    assert "O que vem aí" in page and "Antes de fechar" in page
    assert "Como esta carta foi escrita" in page
    assert "Números escritos por modelo externo" in page
    assert "Impressão dos totais" in page


def test_carta_says_the_truth_about_how_it_was_written(busy: Container, client: TestClient) -> None:
    page = body(client.get("/carta").text)
    assert "Nenhum modelo de linguagem escreveu o texto" in page
    assert "nada foi enviado" in page
    assert "Enviado à API" not in page  # that wording belongs to the on-state, not to this page


def test_carta_month_parameter_is_lenient(busy: Container, client: TestClient) -> None:
    assert "Carta de agosto" in client.get("/carta?month=2026-08").text
    for odd in ("garbage", "2026-13", "", "2099-01", "<script>", "2026-10"):
        response = client.get("/carta", params={"month": odd})
        assert response.status_code == 200
        assert "Carta de setembro" in response.text  # the latest closed month


def test_carta_has_no_inline_scripts_or_handlers(busy: Container, client: TestClient) -> None:
    html = client.get("/carta").text
    assert not re.search(
        r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")", html
    )  # data blocks only
    assert not re.search(r"\son(click|change|input|mouseover)=", html)


def test_carta_todos_link_to_the_places_that_fix_them(busy: Container, client: TestClient) -> None:
    page = body(client.get("/carta").text)
    assert "Fazer o primeiro backup" in page  # no backup yet
    assert 'action="/backup"' in page


# --- E se… -----------------------------------------------------------------------------------


def test_ese_is_in_the_planning_group(client: TestClient) -> None:
    entry = next(e for e in nav.entries() if e.id == "ese")
    assert (entry.href, entry.group, entry.order) == ("/ese", "planning", 30)


def test_ese_empty_page_asks_for_a_phrase(client: TestClient) -> None:
    response = client.get("/ese")
    assert response.status_code == 200
    page = body(response.text)
    assert "Digite uma compra para simular" in page
    assert 'hx-get="/ese/result"' in page and 'id="ese-q"' in page
    assert "Nada é gravado" in page


def test_ese_without_cards_still_understands_the_phrase(client: TestClient) -> None:
    page = body(client.get("/ese", params={"q": "geladeira de 4.200 em 10x"}).text)
    assert "Geladeira" in page and "R$ 4.200,00" in page
    assert "Nenhum cartão com vencimento e fechamento" in page


def test_ese_full_simulation(busy: Container, client: TestClient) -> None:
    page = body(client.get("/ese", params={"q": "geladeira de 4.200 em 10x de 450 no BB"}).text)
    assert "Nada saiu deste computador" in page and "a frase foi entendida aqui" in page
    assert "R$ 4.500,00" in page  # total parcelado
    assert "Quanto vence em cada mês" in page and "Limite dos cartões" in page
    assert "Caixa livre" in page and "R$ 7.600,45" in page
    assert "Cabe?" in page and "Compensa?" in page and "Como o sistema calculou" in page
    assert 'name="rate"' in page and "% ao mês" in page
    assert "é uma suposição sua, começa em zero" in page
    assert "Registrar parcelado no BB Ourocard" in page
    # the underlined words
    assert 'class="u ' in page and 'class="u ub"' in page


def test_ese_card_cycle_is_explained_in_portuguese(busy: Container, client: TestClient) -> None:
    page = body(client.get("/ese", params={"q": "geladeira 4200 10x bb"}).text)
    assert "Compra em 02/10/2026" in page  # explain_assignment, from the card's own rules
    assert "fatura de out/2026" in page


def test_ese_asks_total_or_each_for_one_amount(busy: Container, client: TestClient) -> None:
    page = body(client.get("/ese", params={"q": "sofá 1.400 em 4x no bb"}).text)
    assert "é o valor total ou o de cada parcela?" in page
    assert 'name="role" value="each"' in page
    each = body(client.get("/ese", params={"q": "sofá 1.400 em 4x no bb", "role": "each"}).text)
    assert "é o valor total ou o de cada parcela?" not in each
    assert "R$ 5.600,00" in each  # 4 x 1.400,00


def test_ese_rate_changes_the_verdict_and_never_crashes(
    busy: Container, client: TestClient
) -> None:
    q = "geladeira de 4.200 em 10x de 450 no BB"
    zero = client.get("/ese/result", params={"q": q, "rate": "0"}).text
    high = client.get("/ese/result", params={"q": q, "rate": "200"}).text
    assert "À vista sai mais barato" in zero
    assert "Parcelar sai mais barato" in high
    for odd in ("abc", "-5", "99999", "", "1.5"):
        assert client.get("/ese/result", params={"q": q, "rate": odd}).status_code == 200


def test_ese_result_is_a_fragment(busy: Container, client: TestClient) -> None:
    response = client.get("/ese/result", params={"q": "tv 3000 em 5x no bb"})
    assert response.status_code == 200
    assert "<html" not in response.text and "<title" not in response.text
    assert 'name="q0"' in response.text


def test_ese_pick_cash_shows_the_reference(busy: Container, client: TestClient) -> None:
    q = "geladeira 3000 em 10x no bb"
    page = client.get("/ese/result", params={"q": q, "pick": "cash", "q0": q}).text
    assert "Referência" in page and "Registrar como despesa" in page
    assert "Compra à vista" in page


def test_ese_garbage_and_strange_input(busy: Container, client: TestClient) -> None:
    for q in (
        "!!!",
        "x",
        "R$",
        "999999999999999999 em 999x",
        "a" * 400,
        "<script>alert(1)</script>",
    ):
        response = client.get("/ese", params={"q": q})
        assert response.status_code == 200
        assert "<script>alert" not in response.text
    page = body(client.get("/ese", params={"q": "sem valor aqui"}).text)
    assert "Não achei um valor na frase" in page


def test_ese_too_small_installments_is_a_message_not_a_500(
    busy: Container, client: TestClient
) -> None:
    page = body(client.get("/ese", params={"q": "bala 0,05 em 10x no bb"}).text)
    assert "Não deu para simular" in page


def test_ese_saves_nothing(busy: Container, client: TestClient) -> None:
    with busy.uow as work:
        before = len(work.transactions.list_between(D(2000, 1, 1), D(2100, 1, 1)))
    client.get("/ese", params={"q": "geladeira de 4.200 em 10x de 450 no BB"})
    client.get("/ese/result", params={"q": "geladeira 3000 10x", "pick": "cash"})
    with busy.uow as work:
        assert len(work.transactions.list_between(D(2000, 1, 1), D(2100, 1, 1))) == before


# --- Comando ⌘K ------------------------------------------------------------------------------


def test_every_page_carries_the_palette_markup(client: TestClient) -> None:
    for path in ("/", "/entries", "/carta", "/ese", "/cards"):
        page = client.get(path).text
        assert '<dialog id="palette"' in page, path
        assert "/static/palette.js" in page and "/static/palette.css" in page
        assert "data-palette-open" in page  # the sidebar trigger
        assert "Nada sai deste computador" in body(page)


def test_palette_lists_every_registered_page(client: TestClient) -> None:
    page = client.get("/entries").text
    dialog = page[
        page.index('<dialog id="palette"') : page.index(
            "</dialog>", page.index('<dialog id="palette"')
        )
    ]
    for entry in nav.entries():
        assert f'href="{entry.href}"' in dialog, entry.id
    assert "Novo lançamento" in dialog and "Nova compra parcelada" in dialog
    assert "Fazer backup" in dialog and 'action="/backup"' in dialog


def test_palette_script_is_a_static_file_without_inline_code(client: TestClient) -> None:
    script = client.get("/static/palette.js")
    assert script.status_code == 200
    assert (
        "showModal" in script.text
        and "metaKey" in script.text
        and "data-palette-open" in script.text
    )
    assert "http://" not in script.text and "https://" not in script.text
    page = client.get("/entries").text
    assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")", page)


def test_palette_parse_empty_is_a_hint(client: TestClient) -> None:
    response = client.get("/palette/parse", params={"q": ""})
    assert response.status_code == 200
    assert "Escreva como falaria" in response.text


def test_palette_parse_a_phrase_fills_a_preview_never_a_kind(
    busy: Container, client: TestClient
) -> None:
    html = client.get("/palette/parse", params={"q": "café 12,50 hoje nubank"}).text
    assert "R$ 12,50" in html and "Café" in html
    assert "02/10/2026" in html
    # the kind is a control with the user's pick; the phrase never chooses it
    assert html.count('name="kind"') == 4
    assert re.search(r'value="expense" checked', html)
    assert "O tipo é sempre escolha sua" in html
    assert "f_amount=12%2C50" in html and "f_kind=expense" in html
    assert "Preencher o lançamento" in html


def test_palette_kind_is_the_users_even_for_a_salary_phrase(
    busy: Container, client: TestClient
) -> None:
    html = client.get("/palette/parse", params={"q": "salário 5.000 hoje"}).text
    assert re.search(r'value="expense" checked', html)
    assert not re.search(r'value="income" checked', html)
    chosen = client.get("/palette/parse", params={"q": "salário 5.000 hoje", "kind": "income"}).text
    assert re.search(r'value="income" checked', chosen) and "f_kind=income" in chosen
    odd = client.get("/palette/parse", params={"q": "salário 5.000", "kind": "nonsense"}).text
    assert re.search(r'value="expense" checked', odd)


def test_palette_suggests_the_last_category_used(busy: Container, client: TestClient) -> None:
    html = client.get("/palette/parse", params={"q": "café são joão 9,90"}).text
    assert "Alimentação" in html and "última usada" in html
    assert f"f_category={category(busy, 'food')}" in html


def test_palette_installments_offer_the_simulation_and_ask_back(
    busy: Container, client: TestClient
) -> None:
    html = client.get("/palette/parse", params={"q": "jantar 142 ontem no ourocard em 2x"}).text
    assert "O 142,00 é o valor total ou o de cada parcela?" in html
    assert "Simular em E se…" in html and "/ese?q=jantar+142+ontem" in html
    assert "1/2" in html and "2/2" in html and "fatura" in html  # the schedule
    assert 'name="role"' in html


def test_palette_what_if_phrase_links_to_the_simulator(busy: Container, client: TestClient) -> None:
    html = client.get("/palette/parse", params={"q": "e se geladeira 4.200 em 10x"}).text
    assert "Simular em E se…" in html and "/ese?q=e+se+geladeira" in html
    assert 'name="kind"' not in html


def test_palette_parse_survives_garbage(client: TestClient) -> None:
    for q in ("!!!", "R$", "x" * 1000, "<b>12</b>", "dia 99 ontem hoje", "\x00"):
        response = client.get("/palette/parse", params={"q": q})
        assert response.status_code == 200
        assert "<b>12</b>" not in response.text


def test_entries_form_accepts_the_palette_prefill(busy: Container, client: TestClient) -> None:
    category_id = category(busy, "food")
    page = body(
        client.get(
            "/entries",
            params={
                "fill": "1",
                "f_kind": "income",
                "f_amount": "12,50",
                "f_description": "Café",
                "f_date": "2026-09-30",
                "f_category": category_id,
            },
        ).text
    )
    assert 'value="12,50"' in page and 'value="Café"' in page
    assert 'value="2026-09-30"' in page
    assert re.search(r'value="income"\s+checked', page)
    # a plain /entries is unchanged: today's date, no amount
    plain = body(client.get("/entries").text)
    assert 'value="2026-10-02"' in plain


def test_prefill_ignores_junk(client: TestClient) -> None:
    response = client.get("/entries", params={"fill": "1", "f_kind": "???", "f_amount": "x" * 500})
    assert response.status_code == 200


def test_ese_installment_value_only_has_no_cash_price(busy: Container, client: TestClient) -> None:
    page = body(client.get("/ese", params={"q": "sofá 10x de 450 no bb"}).text)
    assert "Sem preço à vista" in page and "R$ 4.500,00" in page
    nothing = body(client.get("/ese", params={"q": "sofá 10x de 450"}).text)
    assert "Nenhum cartão" not in nothing  # the card exists; the phrase just did not name it
