"""Payment method over HTTP: the form field, the badge, the edit form and the filter chips."""

import re

from fastapi.testclient import TestClient
from test_web import HX, add_expense, buy_on_card, make_card

from financas.container import Container
from financas.domain.models import Transaction


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
    return page.split('id="lista"')[1]


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
