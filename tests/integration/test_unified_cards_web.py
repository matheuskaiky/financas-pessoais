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


def test_each_row_has_a_dedicated_card_column_and_the_feed_a_matching_header(
    client: TestClient, container: Container
) -> None:
    first, second = two_cards(client, container)
    buy_on_card(client, first, days_ago=0, amount="300,00", description="Fone")
    buy_on_card(client, second, days_ago=0, amount="200,00", description="Livro")
    page = client.get("/cards?card=all").text
    assert "tag--card" not in page  # no more inline tag: the card has its own column
    head = page[page.index('class="rowhead card-head card-head--feed"') :]
    head = head[: head.index("</div>")]
    assert re.findall(r"<span[^>]*>([^<]*)</span>", head) == [
        "Data",
        "Cartão",
        "Descrição",
        "Categoria",
        "Valor",
        "Ações",
    ]
    cells = re.findall(
        r'<span class="c-date tnum">(\d\d/\d\d)</span>\s*'
        r'<span class="c-card" title="([^"]+)" style="--card-color: (#[0-9A-Fa-f]{6})"',
        page,
    )
    assert sorted(name for _, name, _ in cells) == ["Itaú", "Nubank"]
    assert page.count('class="c-card__dot"') == 2 and page.count('class="c-act"') == 2
    assert page.count('class="day-head') == 1  # both on today: one day header


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
    assert 'class="c-card"' in older.text and "tag--card" not in older.text


def test_the_unified_view_lists_the_active_installments_and_the_monthly_projection(
    client: TestClient, container: Container
) -> None:
    first, second = two_cards(client, container)
    buy_on_card(client, first, days_ago=0, installments="3", amount="300,00", description="Monitor")
    buy_on_card(client, second, days_ago=0, installments="2", amount="100,00", description="Livro")
    page = client.get("/cards?card=all").text
    assert 'id="h-inst"' in page and "Parcelas ativas" in page and "Parcelas por mês" in page
    assert "Monitor" in page.split('id="h-inst"')[1] and "Livro" in page.split('id="h-inst"')[1]
    assert "a pagar" in page and "cartões selecionados" in page
    months = re.findall(r'<li title="[^"]*">', page.split("Parcelas por mês")[1])
    assert len(months) >= 3  # three monthly bars: the 3x plan reaches the furthest month
    # the chips recalculate both panels: only the second card
    only = client.get(f"/cards?card=all&cards={second}").text
    assert "Livro" in only.split('id="h-inst"')[1] and "Monitor" not in only.split('id="h-inst"')[1]
    assert len(re.findall(r'<li title="[^"]*">', only.split("Parcelas por mês")[1])) == 2
    fragment = client.get(f"/cards?card=all&cards={second}", headers=HX).text
    assert "Parcelas ativas" in fragment and "Monitor" not in fragment.split('id="h-inst"')[1]


def test_no_installments_shows_the_empty_states_not_nothing(
    client: TestClient, container: Container
) -> None:
    two_cards(client, container)
    page = client.get("/cards?card=all").text
    assert "Nenhuma compra parcelada em aberto." in page and "Sem parcelas a vencer." in page


def test_every_face_reserves_the_second_telemetry_line_and_the_colour_is_flat(
    client: TestClient, container: Container
) -> None:
    two_cards(client, container)
    page = client.get("/cards").text
    faces = re.findall(r'<a class="card-face card-face--tinted.*?</a>', page, re.S)
    assert len(faces) == 2
    assert all(f.count("card-face__pending") == 1 for f in faces)  # a real line or a placeholder
    css = client.get("/static/cards.css").text
    assert "min-height:212px" in css
    tinted = [
        line for line in css.splitlines() if ".card-face--tinted{" in line and "background" in line
    ]
    assert tinted and all(
        "linear-gradient(145deg" not in line and "oklch" not in line for line in tinted
    )
    assert "--face-shade" not in page


def test_cancelling_an_edit_in_the_feed_keeps_the_date_and_card_columns(
    client: TestClient, container: Container
) -> None:
    first, _ = two_cards(client, container)
    buy_on_card(client, first, days_ago=0, amount="300,00", description="Fone")
    with container.uow as work:
        entry = work.transactions.list_by_account(first)[0]
    row = client.get(f"/entries/{entry.id}/row?from=cards&card=all", headers=HX).text
    assert "card-entry--feed" in row and 'class="c-card"' in row and 'class="c-date tnum"' in row
    plain = client.get(f"/entries/{entry.id}/row?from=cards&card={first}", headers=HX).text
    assert "card-entry--feed" not in plain
