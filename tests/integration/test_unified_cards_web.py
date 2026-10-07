"""``/cards?card=all``: the consolidated hero, card chips and one timeline, over HTTP."""

import re

from fastapi.testclient import TestClient
from test_web import HX, buy_on_card, make_card

from financas.container import Container
from html_text import visible


def add_card(client: TestClient, container: Container, name: str, limit: str = "") -> str:
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    data = {
        "nickname": name,
        "institution_id": inst.id,
        "closing_days_before_due": "7",
        "due_day": "26",
        "limit": limit,
    }
    assert client.post("/cards", data=data).status_code == 303
    with container.uow as work:
        return next(a.id for a in work.accounts.list_all() if a.nickname == name)


def two_cards(client: TestClient, container: Container) -> tuple[str, str]:
    _, first = make_card(client, container)  # Nubank, limit R$ 12.000,00
    second = add_card(client, container, "Itaú")  # no limit
    return first, second


def test_no_card_redirects_to_the_cards_page_and_one_card_to_that_card(
    client: TestClient, container: Container
) -> None:
    empty = client.get("/cards?card=all")
    assert empty.status_code == 303 and empty.headers["location"] == "/cards"
    _, only = make_card(client, container)
    one = client.get("/cards?card=all")
    assert one.status_code == 303 and one.headers["location"] == f"/cards?card={only}"


def test_the_hero_is_the_sum_of_the_open_statements_and_the_meter_skips_cards_without_limit(
    client: TestClient, container: Container
) -> None:
    first, second = two_cards(client, container)
    buy_on_card(client, first, days_ago=0, amount="300,00", description="Fone")
    buy_on_card(client, second, days_ago=0, amount="200,00", description="Livro")
    page = client.get("/cards?card=all")
    assert page.status_code == 200 and "<html" in page.text
    text = visible(page)
    assert "Fatura consolidada" in text and "R$ 500,00" in text and "2 de 2 cartões" in text
    assert 'role="meter"' in page.text and 'aria-valuetext="Comprometido 2,5%"' in page.text
    assert "1 cartão sem limite informado" in text  # Itaú has none
    assert "Limite total R$ 12.000,00" in text  # only Nubank's limit
    assert 'id="cards-unified"' in page.text and 'id="cards-live"' in page.text
    assert "Mostrando todos os cartões." in page.text


def test_no_limit_at_all_shows_the_hatched_track_never_zero_percent(
    client: TestClient, container: Container
) -> None:
    _, first = make_card(client, container, limit="")  # no limit on the first card either
    second = add_card(client, container, "Itaú")
    buy_on_card(client, first, days_ago=0, amount="10,00")
    page = client.get("/cards?card=all")
    assert "limite não informado" in visible(page) and "sg-unset" in page.text
    assert 'role="meter"' not in page.text and "0,0%" not in page.text
    assert second in page.text


def test_chips_carry_the_page_after_their_own_toggle(
    client: TestClient, container: Container
) -> None:
    first, second = two_cards(client, container)
    page = client.get("/cards?card=all").text
    group = page[page.index('class="card-chips"') :]
    assert 'role="group" aria-label="Cartões"' in page
    chips = re.findall(
        r'<button type="button" class="card-chip" id="([^"]+)" aria-pressed="(\w+)"', group
    )
    assert chips == [("chip-all", "true"), (f"chip-{first}", "true"), (f"chip-{second}", "true")]
    assert f'hx-get="/cards?card=all&amp;cards={second}"' in page or (
        f'hx-get="/cards?card=all&cards={second}"' in page
    )  # pressing the first card leaves only the second
    assert 'hx-push-url="true"' in page and 'hx-sync="#cards-unified:replace"' in page


def test_a_subset_shows_only_its_cards_and_the_last_chip_cannot_be_excluded(
    client: TestClient, container: Container
) -> None:
    first, second = two_cards(client, container)
    buy_on_card(client, first, days_ago=0, amount="300,00", description="Fone")
    buy_on_card(client, second, days_ago=0, amount="200,00", description="Livro")
    page = client.get(f"/cards?card=all&cards={second}")
    text = visible(page)
    hero = page.text.split('id="hero-eyebrow"')[1].split("card-chips")[
        0
    ]  # the strip above is every card
    assert 'data-cents="20000"' in hero and 'data-cents="50000"' not in hero
    assert "1 de 2 cartões" in hero
    assert "Livro" in text and "Fone" not in text
    assert re.search(rf'id="chip-{second}" aria-pressed="true" aria-disabled="true"', page.text)
    assert "Mostrando Itaú." in page.text
    assert client.get("/cards?card=all&cards=zzz").text.count('aria-pressed="true"') == 3  # all


def test_a_chip_request_returns_only_the_fragment_and_history_restore_the_page(
    client: TestClient, container: Container
) -> None:
    first, _ = two_cards(client, container)
    fragment = client.get(f"/cards?card=all&cards={first}", headers=HX)
    assert fragment.status_code == 200 and "<html" not in fragment.text
    assert 'id="cards-unified"' in fragment.text
    assert (
        'hx-swap-oob="innerHTML:#cards-live"' in fragment.text
        and "Mostrando Nubank." in fragment.text
    )
    restore = client.get(
        f"/cards?card=all&cards={first}", headers={**HX, "hx-history-restore-request": "true"}
    )
    assert "<html" in restore.text  # Back after a swap needs the whole page


def test_the_timeline_tags_each_row_with_its_card_and_groups_by_day(
    client: TestClient, container: Container
) -> None:
    first, second = two_cards(client, container)
    buy_on_card(client, first, days_ago=0, amount="300,00", description="Fone")
    buy_on_card(client, second, days_ago=0, amount="200,00", description="Livro")
    page = client.get("/cards?card=all").text
    assert page.count('class="tag tag--card"') == 2
    assert 'title="Nubank"' in page and 'title="Itaú"' in page
    assert page.count('class="day-head') == 1  # both on today: one day header
    assert "data-sel-scope" in page and 'class="sel-dock"' in page and 'data-sel-key="' in page


def test_an_empty_current_statement_has_the_real_zero_and_the_timeline_text(
    client: TestClient, container: Container
) -> None:
    two_cards(client, container)
    page = client.get("/cards?card=all")
    text = visible(page)
    assert "R$ 0,00" in text  # a real zero here, not a dash
    assert "Nenhum lançamento nas faturas atuais dos cartões selecionados." in text


def test_older_statements_load_in_pages_and_the_button_goes_away_at_the_end(
    client: TestClient, container: Container
) -> None:
    first, _ = two_cards(client, container)
    buy_on_card(client, first, days_ago=75, amount="50,00", description="Antiga")
    buy_on_card(client, first, days_ago=0, amount="300,00", description="Fone")
    page = client.get("/cards?card=all").text
    assert "Ver lançamentos anteriores" in page and "Antiga" not in page.split('id="cards-more"')[0]
    more = re.search(r'hx-get="([^"]+older=1[^"]*)"', page)
    assert more
    older = client.get(more.group(1).replace("&amp;", "&"), headers=HX)
    assert older.status_code == 200 and "<html" not in older.text and "Antiga" in older.text
    assert "Ver lançamentos anteriores" not in older.text  # nothing left to load
    assert 'class="tag tag--card"' in older.text
