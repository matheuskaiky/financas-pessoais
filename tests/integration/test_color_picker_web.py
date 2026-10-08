"""Account and card colors over HTTP: they persist (no checkbox gate) and the picker is rendered."""

import re

import pytest
from fastapi.testclient import TestClient
from test_web import HX, make_card

from financas.container import Container


def own_color(container: Container, account_id: str) -> str | None:
    with container.uow as work:
        account = work.accounts.get(account_id)
    assert account
    return account.color


def hex_value(page: str) -> list[str]:
    return re.findall(r'<input type="text" name="color" class="cp-hex"[^>]*value="([^"]*)"', page)


@pytest.mark.parametrize(
    ("typed", "stored"), [("#FF7A00", "#FF7A00"), ("#820ad1", "#820AD1"), ("0f5c45", "#0F5C45")]
)
def test_an_account_color_is_saved_without_any_checkbox(
    client: TestClient, container: Container, typed: str, stored: str
) -> None:
    checking, _ = make_card(client, container)
    assert own_color(container, checking) is None  # inherits: the case that used to be lost
    done = client.post(f"/appearance/account/{checking}", data={"color": typed})
    assert done.status_code == 303 and "ok=appearance" in done.headers["location"]
    assert own_color(container, checking) == stored
    assert stored in hex_value(client.get("/accounts").text)  # the field shows it after the reload


def test_an_invalid_color_is_refused_and_keeps_the_old_one(
    client: TestClient, container: Container
) -> None:
    checking, _ = make_card(client, container)
    client.post(f"/appearance/account/{checking}", data={"color": "#FF7A00"})
    bad = client.post(f"/appearance/account/{checking}", data={"color": "roxo"})
    assert bad.status_code == 400 and "cor" in bad.text.lower()
    assert own_color(container, checking) == "#FF7A00"


def test_clearing_the_hex_field_removes_the_accounts_own_color(
    client: TestClient, container: Container
) -> None:
    checking, _ = make_card(client, container)
    client.post(f"/appearance/account/{checking}", data={"color": "#FF7A00"})
    assert client.post(f"/appearance/account/{checking}", data={"color": ""}).status_code == 303
    assert own_color(container, checking) is None


def test_the_card_details_form_saves_the_color_and_shows_it_again(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    assert own_color(container, card) is None
    saved = client.post(
        f"/cards/{card}/details", data={"nickname": "Nubank", "color": "#820ad1"}, headers=HX
    )
    assert saved.status_code == 200 and saved.headers["hx-redirect"].startswith("/cards")
    assert own_color(container, card) == "#820AD1"
    form = client.get(f"/cards/{card}/details", headers=HX).text
    assert hex_value(form) == ["#820AD1"] and "<html" not in form  # a fragment, picker included
    assert "data-color-picker" in form


def test_the_picker_has_hex_presets_system_dialog_and_a_hidden_eyedropper(
    client: TestClient, container: Container
) -> None:
    make_card(client, container)
    page = client.get("/accounts").text
    assert "Usar esta cor" not in page  # the checkbox gate is gone
    assert 'class="cp-hex"' in page and 'pattern="#?[0-9A-Fa-f]{6}"' in page
    assert 'type="color" class="sr-only" data-cp-native' in page
    assert re.search(r"<button[^>]*data-cp-eyedropper[^>]*hidden", page)  # shown by the script
    assert "Conta-gotas" in page and "Sem cor própria" in page
    for hex_code in ("#820AD1", "#FF7A00", "#FFF159", "#003399", "#CC092F", "#242424", "#D4A017"):
        assert f'data-cp-preset="{hex_code}"' in page, hex_code
    assert "/static/color-picker.js" in page
