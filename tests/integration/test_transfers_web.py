"""Internal transfers over HTTP: the form, the reciprocal badges in the feed, edit and delete."""

import datetime as dt
import re

from fastapi.testclient import TestClient
from test_web import HX, setup_accounts

from financas.container import Container

TODAY = dt.date.today()


def make(client: TestClient, container: Container, **extra: str) -> tuple[str, str, list[str]]:
    checking, savings = setup_accounts(client, container)
    done = client.post(
        "/entries",
        data={
            "kind": "transfer",
            "date": TODAY.isoformat(),
            "amount": "500,00",
            "from_account": checking,
            "to_account": savings,
            **extra,
        },
    )
    assert done.status_code == 303 and "ok=transfer" in done.headers["location"]
    with container.uow as work:
        legs = [t for a in (checking, savings) for t in work.transactions.list_by_account(a)]
    return checking, savings, [t.id for t in sorted(legs, key=lambda t: t.amount_cents)]


def test_creating_a_transfer_makes_two_linked_legs_and_lands_on_the_first_leg(
    client: TestClient, container: Container
) -> None:
    _, _, (out_id, in_id) = make(client, container)
    with container.uow as work:
        out, inc = work.transactions.get(out_id), work.transactions.get(in_id)
    assert out and inc and out.transfer_id == inc.transfer_id
    assert (out.amount_cents, inc.amount_cents) == (-50_000, 50_000)
    # no description typed: the adapter names it after the accounts
    assert out.description == "Transferência: Conta Corrente → Caixinha"


def test_the_feed_shows_the_reciprocal_badges_with_links(
    client: TestClient, container: Container
) -> None:
    _, _, (out_id, in_id) = make(client, container)
    page = client.get("/entries").text
    out_row = page.split(f'id="entry-{out_id}"')[1].split("</div>\n")[0]
    in_row = page.split(f'id="entry-{in_id}"')[1].split("</div>\n")[0]
    assert "↔ Aporte: Caixinha" in out_row and f"focus={in_id}#entry-{in_id}" in out_row
    assert "↔ De: Conta Corrente" in in_row and f"focus={out_id}#entry-{out_id}" in in_row
    link = re.search(r'data-transfer-link href="([^"]+)"', out_row)
    assert link
    target = link.group(1).replace("&amp;", "&")
    landed = client.get(target.split("#")[0])
    assert landed.status_code == 200 and f'id="entry-{in_id}"' in landed.text
    # a plain entry has no badge
    assert page.count("data-transfer-link") == 2


def test_the_single_row_fragment_keeps_its_badge(client: TestClient, container: Container) -> None:
    _, _, (out_id, _) = make(client, container)
    row = client.get(f"/entries/{out_id}/row", headers=HX).text
    assert "↔ Aporte: Caixinha" in row and "<html" not in row


def test_editing_one_leg_updates_the_other_and_the_badges(
    client: TestClient, container: Container
) -> None:
    checking, savings, (out_id, in_id) = make(client, container)
    form = client.get(f"/entries/{in_id}/edit", headers=HX).text
    assert "Salvar transferência" in form and 'name="from_account"' in form
    saved = client.post(
        f"/entries/{in_id}/edit",
        data={
            "amount": "750,00",
            "date": TODAY.isoformat(),
            "description": "Reserva",
            "from_account": checking,
            "to_account": savings,
        },
        headers=HX,
    )
    assert saved.status_code == 200 and "ok=transfer_updated" in saved.headers["hx-redirect"]
    with container.uow as work:
        out, inc = work.transactions.get(out_id), work.transactions.get(in_id)
    assert out and inc
    assert (out.amount_cents, inc.amount_cents) == (-75_000, 75_000)
    assert out.description == inc.description == "Reserva"


def test_editing_with_the_same_account_on_both_sides_shows_the_error(
    client: TestClient, container: Container
) -> None:
    checking, _, (out_id, _) = make(client, container)
    refused = client.post(
        f"/entries/{out_id}/edit",
        data={
            "amount": "1,00",
            "date": TODAY.isoformat(),
            "from_account": checking,
            "to_account": checking,
        },
        headers=HX,
    )
    assert "Salvar transferência" in refused.text and 'data-tone="error"' in refused.text
    with container.uow as work:
        assert (leg := work.transactions.get(out_id)) and leg.amount_cents == -50_000


def test_deleting_one_leg_removes_both_and_asks_for_the_pair(
    client: TestClient, container: Container
) -> None:
    _, _, (out_id, in_id) = make(client, container)
    assert "nas duas contas" in client.get("/entries").text
    assert client.post(f"/entries/{out_id}/delete", headers=HX).status_code in (200, 303)
    with container.uow as work:
        assert work.transactions.get(out_id) is None and work.transactions.get(in_id) is None


def test_the_edit_button_of_a_transfer_row_opens_the_form_with_both_accounts(
    client: TestClient, container: Container
) -> None:
    checking, savings, (out_id, in_id) = make(client, container)
    page = client.get("/entries").text
    for leg_id in (out_id, in_id):
        assert f'hx-get="/entries/{leg_id}/edit"' in page  # clickable on both legs
        form = client.get(f"/entries/{leg_id}/edit", headers=HX).text
        assert "Salvar transferência" in form
        assert re.search(rf'<option value="{checking}"[^>]*selected', form)
        assert re.search(rf'<option value="{savings}"[^>]*selected', form)
        assert 'value="500,00"' in form


def test_changing_the_destination_through_the_form_moves_only_the_inflow_leg(
    client: TestClient, container: Container
) -> None:
    checking, _, (out_id, in_id) = make(client, container)
    with container.uow as work:
        inst = work.institutions.list_all()[0]
    client.post("/accounts", data={"nickname": "Reserva", "institution_id": inst.id})
    with container.uow as work:
        reserve = next(a.id for a in work.accounts.list_all() if a.nickname == "Reserva")
    saved = client.post(
        f"/entries/{out_id}/edit",
        data={
            "amount": "500,00",
            "date": TODAY.isoformat(),
            "description": "Mudou",
            "from_account": checking,
            "to_account": reserve,
        },
        headers=HX,
    )
    assert saved.status_code == 200
    with container.uow as work:
        out, inc = work.transactions.get(out_id), work.transactions.get(in_id)
    assert out and inc and (out.account_id, inc.account_id) == (checking, reserve)
    assert "↔ Para: Reserva" in client.get("/entries").text
