"""Payment method over HTTP: the form field, the badge, the edit form and the filter chips."""

import datetime as dt
import re

from fastapi.testclient import TestClient
from test_web import HX, add_expense, buy_on_card, make_card

from financas.container import Container
from financas.domain.models import Transaction

TODAY = dt.date.today()


def by_description(container: Container, account: str) -> dict[str, Transaction]:
    with container.uow as work:
        return {t.description: t for t in work.transactions.list_by_account(account)}


def seed_entries(client: TestClient, container: Container) -> tuple[str, str]:
    checking, card = make_card(client, container)
    add_expense(client, checking, description="Feira PIX", payment_method="pix")
    add_expense(client, checking, description="Padaria Pão Quente", payment_method="debito")
    add_expense(client, checking, description="Condomínio", payment_method="boleto")
    buy_on_card(client, card, days_ago=0, description="Fone", amount="90,00")
    return checking, card


def listed(client: TestClient, query: str = "") -> str:
    page = client.get(f"/entries{query}").text
    return page.split('id="lista"')[1].split("</section>")[0]  # the rows, not the page data blocks


def test_the_form_stores_the_method_and_the_row_shows_a_badge(
    client: TestClient, container: Container
) -> None:
    checking, _ = seed_entries(client, container)
    rows = by_description(container, checking)
    assert [
        str(rows[n].payment_method) for n in ("Feira PIX", "Padaria Pão Quente", "Condomínio")
    ] == [
        "pix",
        "debito",
        "boleto",
    ]
    page = listed(client)
    for label in ("PIX", "Débito", "Boleto"):
        assert 'class="tag tag--method"' in page and f">{label}</span>" in page
    assert page.count("tag--method") == 3  # the card purchase has no badge


def test_the_quick_form_offers_pix_debito_boleto_outro_with_pix_selected(
    client: TestClient, container: Container
) -> None:
    seed_entries(client, container)
    page = client.get("/entries").text
    form = page[page.index("data-method-field") :]
    labels = re.findall(
        r'<input type="radio" name="payment_method" value="(\w+)"\s*(checked)?\s*>', form
    )
    assert [v for v, _ in labels][:4] == ["pix", "debito", "boleto", "outro"]
    assert [v for v, checked in labels if checked] == ["pix"]  # the default
    assert 'data-account-kind="credit_card"' in page  # the script hides the field for a card


def test_a_wrong_method_is_a_message_not_a_500(client: TestClient, container: Container) -> None:
    checking, card = make_card(client, container)
    data = {
        "kind": "expense",
        "date": "2026-07-10",
        "amount": "10,00",
        "description": "X",
        "account_id": checking,
    }
    bad = client.post("/entries", data={**data, "payment_method": "cheque"})
    assert bad.status_code == 400 or "Escolha inválida" in bad.text or bad.status_code == 200
    card_with_pix = client.post(
        "/entries", data={**data, "account_id": card, "payment_method": "pix"}
    )
    assert "Forma de pagamento inválida" in card_with_pix.text
    assert by_description(container, checking) == {}


def test_filter_chips_and_the_method_filter(client: TestClient, container: Container) -> None:
    seed_entries(client, container)
    page = client.get("/entries?method=pix").text
    chips = re.findall(r'id="method-chip-(\w+)" href="([^"]+)"( aria-current="true")?', page)
    assert [c[0] for c in chips] == ["all", "cartao_credito", "pix", "debito", "boleto"]
    assert [c[0] for c in chips if c[2]] == ["pix"]
    assert 'hx-select="#entries-results"' in page and 'hx-push-url="true"' in page
    only_pix = listed(client, "?method=pix")
    assert "Feira PIX" in only_pix and "Condomínio" not in only_pix and "Fone" not in only_pix
    assert "Padaria Pão Quente" in listed(client, "?method=debito")
    assert "Condomínio" in listed(client, "?method=boleto")
    cards = listed(client, "?method=cartao_credito")
    assert "Fone" in cards and "Feira PIX" not in cards  # every card, all at once
    everything = listed(client, "?method=")
    assert all(n in everything for n in ("Feira PIX", "Condomínio", "Fone"))
    assert "Feira PIX" in listed(client, "?method=cheque")  # lenient: ignored


def test_the_chips_keep_the_month_the_search_and_the_other_filters(
    client: TestClient, container: Container
) -> None:
    seed_entries(client, container)
    page = client.get("/entries?month=2026-07&q=feira&kind=expense").text
    chip = re.search(r'id="method-chip-pix" href="([^"]+)"', page)
    assert chip
    href = chip.group(1).replace("&amp;", "&")
    assert "month=2026-07" in href and "q=feira" in href and "kind=expense" in href
    assert href.endswith("method=pix")


def test_editing_an_entry_can_change_its_method(client: TestClient, container: Container) -> None:
    checking, _ = seed_entries(client, container)
    entry = by_description(container, checking)["Feira PIX"]
    form = client.get(f"/entries/{entry.id}/edit", headers=HX).text
    assert re.search(r'name="payment_method" value="pix" checked', form)
    saved = client.post(
        f"/entries/{entry.id}/edit",
        data={
            "amount": "123,45",
            "date": entry.posted_on.isoformat(),
            "description": "Feira PIX",
            "payment_method": "boleto",
        },
        headers=HX,
    )
    assert saved.status_code == 200
    changed = by_description(container, checking)["Feira PIX"]
    assert changed.payment_method is not None and changed.payment_method.value == "boleto"
    assert (
        "payment_method"
        not in client.get(
            f"/entries/{by_description(container, checking)['Fone'].id}/edit", headers=HX
        ).text
        if "Fone" in by_description(container, checking)
        else True
    )


def test_a_card_purchase_edit_form_has_no_method_field(
    client: TestClient, container: Container
) -> None:
    _, card = seed_entries(client, container)
    fone = by_description(container, card)["Fone"]
    assert fone.payment_method is not None and fone.payment_method.value == "cartao_credito"
    assert "data-method-field" not in client.get(f"/entries/{fone.id}/edit", headers=HX).text


# --- filters survive month changes and new entries; "Limpar filtros" ---


def clear_href(page: str) -> str:
    match = re.search(r'<a class="btn secondary" href="([^"]+)">Limpar filtros</a>', page)
    assert match
    return match.group(1).replace("&amp;", "&")


def test_month_navigation_keeps_every_active_filter(
    client: TestClient, container: Container
) -> None:
    seed_entries(client, container)
    page = client.get("/entries?month=2026-09&method=pix&q=feira&kind=expense").text
    links = [
        m.replace("&amp;", "&")
        for m in re.findall(
            r'<a class="icon-btn" href="([^"]+)" aria-label="(?:Mês anterior|Próximo mês)"', page
        )
    ]
    assert len(links) == 2
    for link, month in zip(links, ("2026-08", "2026-10"), strict=True):
        assert f"month={month}" in link and "method=pix" in link
        assert "q=feira" in link and "kind=expense" in link


def test_a_new_entry_returns_to_the_same_month_and_filters(
    client: TestClient, container: Container
) -> None:
    checking, _ = seed_entries(client, container)
    page = client.get("/entries?month=2026-09&method=cartao_credito&q=fone").text
    hidden = re.search(r'name="return_query" value="([^"]*)"', page)
    assert hidden
    query = hidden.group(1).replace("&amp;", "&")
    assert "month=2026-09" in query and "method=cartao_credito" in query and "q=fone" in query
    done = client.post(
        "/entries",
        data={
            "kind": "expense",
            "date": "2026-09-10",
            "amount": "12,00",
            "description": "Café",
            "account_id": checking,
            "payment_method": "pix",
            "return_query": query + "&evil=1&method=cheque",
        },
    )
    assert done.status_code == 303
    location = done.headers["location"]
    assert location.startswith("/entries?") and "ok=entry" in location
    assert (
        "month=2026-09" in location and "method=cartao_credito" in location and "q=fone" in location
    )
    assert "evil" not in location and "cheque" not in location  # only known, valid keys
    assert (
        "Novo lançamento" in client.get(location).text and client.get(location).status_code == 200
    )


def test_a_failed_submission_keeps_the_filters_on_the_page(
    client: TestClient, container: Container
) -> None:
    seed_entries(client, container)
    page = client.post(
        "/entries",
        data={
            "kind": "expense",
            "date": "2026-09-10",
            "amount": "12,00",
            "description": "Sem conta",
            "return_query": "month=2026-09&method=boleto&q=cond",
        },
    )
    assert "Escolha uma conta" in page.text or page.status_code in (200, 400)
    assert re.search(r'id="method-chip-boleto"[^>]*aria-current="true"', page.text)
    assert 'value="cond"' in page.text and "setembro" in page.text.lower()


def test_limpar_filtros_shows_only_while_a_filter_is_active_and_keeps_the_month(
    client: TestClient, container: Container
) -> None:
    seed_entries(client, container)
    assert "Limpar filtros" not in client.get("/entries?month=2026-10").text
    for query in ("method=debito", "q=feira", "kind=expense"):
        page = client.get(f"/entries?month=2026-10&{query}").text
        assert "Limpar filtros" in page, query
        assert clear_href(page) == "/entries?month=2026-10"
    assert "Limpar filtros" not in client.get("/entries?month=2026-10&method=").text
    assert "Limpar filtros" not in client.get("/entries?month=2026-10&method=cheque").text


def test_a_chip_swap_replaces_the_filter_bar_too_so_limpar_filtros_is_never_stale(
    client: TestClient, container: Container
) -> None:
    seed_entries(client, container)
    page = client.get("/entries?month=2026-10&method=pix").text
    region = page[page.index('id="entries-results"') :]
    assert 'class="filter-bar"' in region.split('class="method-chips"')[0]  # bar is inside
    assert "Limpar filtros" in region and 'name="method" value="pix"' in region


# --- the options follow the direction of the money; the description is optional ---


def visible_options(html: str) -> list[str]:
    block = html[html.index("data-method-field") :]
    block = block[: block.index("</fieldset>")]
    return re.findall(
        r'<label><input type="radio" name="payment_method"[^>]*><span>([^<]*)</span>', block
    )


def test_the_edit_form_offers_pix_ted_outro_for_an_income_and_the_four_for_an_expense(
    client: TestClient, container: Container
) -> None:
    checking, _ = make_card(client, container)
    add_expense(client, checking, description="Mercado", payment_method="boleto")
    client.post(
        "/entries",
        data={
            "kind": "income",
            "date": "2026-07-05",
            "amount": "100,00",
            "description": "Cliente",
            "account_id": checking,
            "payment_method": "ted",
        },
    )
    by_name = by_description(container, checking)
    assert str(by_name["Cliente"].payment_method) == "ted"
    income = client.get(f"/entries/{by_name['Cliente'].id}/edit", headers=HX).text
    assert visible_options(income) == ["PIX", "TED", "Outro"]
    assert re.search(r'name="payment_method" value="ted"\s*checked', income)
    expense = client.get(f"/entries/{by_name['Mercado'].id}/edit", headers=HX).text
    assert visible_options(expense) == ["PIX", "Débito", "Boleto", "Outro"]
    assert re.search(r'name="payment_method" value="boleto"\s*checked', expense)


def method_field_html(page: str) -> str:
    block = page[page.index("data-method-field") :]
    return block[: block.index("</fieldset>")]


def test_an_income_form_never_carries_debito_or_boleto_in_its_dom(
    client: TestClient, container: Container
) -> None:
    make_card(client, container)
    expense = method_field_html(client.get("/entries").text)  # "Despesa" is the default
    assert visible_options(client.get("/entries").text) == ["PIX", "Débito", "Boleto", "Outro"]
    assert 'data-side="expense"' in expense
    for url in (
        "/entries?fill=1&f_kind=income&f_amount=10,00",  # an income draft: first render
        "/entries/method-field?kind=income",  # what the toggle swaps in
        "/entries/method-field?kind=income&current=boleto",  # a stale choice falls back to PIX
        "/entries/method-field?kind=income&current=debito",
    ):
        page = client.get(url)
        field = method_field_html(page.text)
        assert 'data-side="income"' in field, url
        assert "Débito" not in field and "Boleto" not in field, url
        assert 'value="debito"' not in field and 'value="boleto"' not in field, url
        assert visible_options(page.text) == ["PIX", "TED", "Outro"], url
        assert re.search(r'value="pix"\s*checked', field), url
    back = client.get("/entries/method-field?kind=expense&current=pix").text
    assert visible_options(back) == ["PIX", "Débito", "Boleto", "Outro"]
    assert "TED" not in method_field_html(back)
    odd = client.get("/entries/method-field?kind=income&current=<script>").text
    assert "<script>" not in odd and visible_options(odd) == ["PIX", "TED", "Outro"]


def test_an_income_by_debit_or_boleto_is_refused_with_a_message(
    client: TestClient, container: Container
) -> None:
    checking, _ = make_card(client, container)
    answer = client.post(
        "/entries",
        data={
            "kind": "income",
            "date": "2026-07-05",
            "amount": "100,00",
            "description": "X",
            "account_id": checking,
            "payment_method": "boleto",
        },
    )
    assert "Forma de pagamento inválida" in answer.text
    assert by_description(container, checking) == {}


def test_an_entry_with_no_description_is_titled_by_its_category(
    client: TestClient, container: Container
) -> None:
    checking, _ = make_card(client, container)
    with container.uow as work:
        salary = work.categories.get_by_slug("salary")
        groceries = work.categories.get_by_slug("groceries")
    assert salary and groceries
    for kind, category, amount in (
        ("income", salary.id, "5.000,00"),
        ("expense", groceries.id, "80,00"),
    ):
        done = client.post(
            "/entries",
            data={
                "kind": kind,
                "date": f"{TODAY}",
                "amount": amount,
                "description": "",
                "category_id": category,
                "account_id": checking,
            },
        )
        assert done.status_code == 303
    bare = client.post(
        "/entries",
        data={"kind": "expense", "date": f"{TODAY}", "amount": "9,00", "account_id": checking},
    )
    assert bare.status_code == 303
    titles = set(by_description(container, checking))
    assert titles == {"Salário", "Supermercado", "Sem descrição"}
    page = client.get("/entries").text.split('id="lista"')[1]
    assert "Salário" in page and "Supermercado" in page and "Sem descrição" in page
    assert 'name="description"' in client.get("/entries").text
    assert " required" not in re.search(
        r'<input[^>]*name="description"[^>]*>', page + client.get("/entries").text
    ).group(0)  # type: ignore[union-attr]
    edit = client.get(
        f"/entries/{by_description(container, checking)['Supermercado'].id}/edit", headers=HX
    ).text
    assert " required" not in re.search(r'<input[^>]*name="description"[^>]*>', edit).group(0)  # type: ignore[union-attr]


# --- smart suggestions ---


def seed_habits(client: TestClient, container: Container) -> tuple[str, str]:
    checking, _ = make_card(client, container)
    with container.uow as work:
        food = work.categories.get_by_slug("food")
        salary = work.categories.get_by_slug("salary")
    assert food and salary
    for n in range(5):
        add_expense(
            client,
            checking,
            description="Almoço Restaurante Central",
            amount="35,00",
            category_id=food.id,
            payment_method="debito",
            date=(TODAY - dt.timedelta(days=1 + n * 2)).isoformat(),
        )
    client.post(
        "/entries",
        data={
            "kind": "income",
            "date": (TODAY - dt.timedelta(days=3)).isoformat(),
            "amount": "5.000,00",
            "description": "Salário",
            "category_id": salary.id,
            "account_id": checking,
            "payment_method": "ted",
        },
    )
    return checking, food.id


def test_the_suggestions_endpoint_returns_the_scored_habits_with_the_amount(
    client: TestClient, container: Container
) -> None:
    checking, food = seed_habits(client, container)
    answer = client.get("/entries/suggestions?flow=expense&q=almoco")
    assert answer.status_code == 200 and answer.headers["content-type"].startswith(
        "application/json"
    )
    (top,) = answer.json()
    assert top == {
        "description": "Almoço Restaurante Central",
        "flow": "expense",
        "category_id": food,
        "category_name": "Alimentação",
        "account_id": checking,
        "account_name": "Conta Corrente",
        "payment_method": "debito",
        "payment_label": "Débito",
        "habitual_amount_cents": 3_500,
        "habitual_amount": "35,00",
    }
    income = client.get("/entries/suggestions?flow=income").json()
    assert [s["description"] for s in income] == ["Salário"]
    assert income[0]["payment_method"] == "ted" and income[0]["habitual_amount"] == "5000,00"
    assert client.get("/entries/suggestions?flow=zzz&limit=abc").status_code == 200
    assert client.get("/entries/suggestions?flow=income&q=nada").json() == []


def test_the_page_seeds_the_description_list_from_the_same_data(
    client: TestClient, container: Container
) -> None:
    seed_habits(client, container)
    page = client.get("/entries").text
    # our own listbox, not the browser's datalist: it must exist next to the field, closed
    assert "data-suggest" in page and 'role="combobox"' in page
    assert re.search(
        r'<ul id="description-suggestions" class="suggest-list" role="listbox"[^>]*hidden>', page
    )
    assert 'list="description-suggestions"' not in page
    assert '<datalist id="description-suggestions"' not in page
    block = re.search(r'id="suggestions-data">(.*?)</script>', page, re.S)
    assert block
    import json

    data = json.loads(block.group(1))
    assert set(data) == {"expense", "income"}
    assert data["expense"][0]["description"] == "Almoço Restaurante Central"
    assert {s["payment_method"] for s in data["income"]} <= {"pix", "ted", "outro", None}
    assert "/static/suggestions.js" in page


# --- landing on the new row ---


def test_a_new_entry_lands_on_its_own_month_and_row(
    client: TestClient, container: Container
) -> None:
    checking, _ = seed_entries(client, container)
    done = client.post(
        "/entries",
        data={
            "kind": "expense",
            "date": "2026-03-05",
            "amount": "12,00",
            "description": "Lanche março",
            "account_id": checking,
            "payment_method": "pix",
            "return_query": "month=2026-09",  # the list was showing another month
        },
    )
    location = done.headers["location"]
    row = by_description(container, checking)["Lanche março"]
    assert done.status_code == 303 and "month=2026-03" in location
    assert "month=2026-09" not in location
    assert "ok=entry" in location and location.endswith(f"#entry-{row.id}")
    page = client.get(location.split("#")[0]).text
    assert f'id="entry-{row.id}"' in page and "Lanche março" in page


def test_a_new_transfer_lands_on_its_month_and_first_leg(
    client: TestClient, container: Container
) -> None:
    checking, card = seed_entries(client, container)
    with container.uow as work:
        other = next(a for a in work.accounts.list_all() if a.id not in (checking, card))
    done = client.post(
        "/entries",
        data={
            "kind": "transfer",
            "date": "2026-02-10",
            "amount": "50,00",
            "from_account": checking,
            "to_account": other.id,
            "description": "Reserva",
        },
    )
    location = done.headers["location"]
    assert "month=2026-02" in location and "#entry-" in location
    assert f'id="{location.split("#")[1]}"' in client.get(location.split("#")[0]).text


def test_the_focus_widens_the_page_to_reach_an_old_row(
    client: TestClient, container: Container
) -> None:
    checking, _ = seed_entries(client, container)
    for n in range(55):  # more than one page of the month, all newer than the target
        add_expense(client, checking, description=f"Item {n:02d}", date="2026-04-20")
    done = client.post(
        "/entries",
        data={
            "kind": "expense",
            "date": "2026-04-01",
            "amount": "9,00",
            "description": "Muito antigo",
            "account_id": checking,
            "payment_method": "pix",
        },
    )
    location = done.headers["location"]
    row_id = location.split("#entry-")[1]
    assert f'id="entry-{row_id}"' in client.get(location.split("#")[0]).text
    assert f'id="entry-{row_id}"' not in client.get("/entries?month=2026-04").text
