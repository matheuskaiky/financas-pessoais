"""Investment snapshots over HTTP: notes, timeline, KPI cards and the transfer form's note."""

import datetime as dt
import re

from fastapi.testclient import TestClient
from test_web import HX, make_broker, new_holding

from financas.container import Container
from html_text import visible

TODAY = dt.date.today()


def holding_of(container: Container):
    with container.uow as work:
        (holding,) = work.holdings.list_all()
    return holding


def anchors_of(container: Container, holding_id: str):
    with container.uow as work:
        return work.anchors.list_for_holding(holding_id)


def test_posting_a_snapshot_persists_and_shows_in_the_timeline(
    client: TestClient, container: Container
) -> None:
    _, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    holding = holding_of(container)
    done = client.post(
        f"/investments/holdings/{holding.id}/snapshots",
        data={"as_of_date": "2026-03-15", "gross_value_cents": "105000"},
    )
    assert done.status_code == 303 and "ok=valuation" in done.headers["location"]
    (anchor,) = anchors_of(container, holding.id)
    assert (anchor.on_date, anchor.gross_balance_cents) == (dt.date(2026, 3, 15), 105_000)
    page = client.get("/investments").text
    assert "Histórico de CDB Banco X 110% CDI" in page
    assert "1.050,00" in page
    assert "data-snapshot-table" in page and "Rentabilidade acumulada" in page


def test_a_second_snapshot_on_the_same_day_replaces_the_first(
    client: TestClient, container: Container
) -> None:
    _, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    holding = holding_of(container)
    for typed in ("1.050,00", "1.100,00"):
        client.post(
            f"/investments/holdings/{holding.id}/snapshots",
            data={"as_of_date": "2026-03-15", "gross": typed},
        )
    (anchor,) = anchors_of(container, holding.id)
    assert anchor.gross_balance_cents == 110_000


def test_a_snapshot_can_be_deleted_and_a_bad_one_is_refused_in_portuguese(
    client: TestClient, container: Container
) -> None:
    _, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    holding = holding_of(container)
    client.post(
        f"/investments/holdings/{holding.id}/snapshots",
        data={"as_of_date": "2026-03-15", "gross": "1.050,00"},
    )
    (anchor,) = anchors_of(container, holding.id)
    bad = client.post(
        f"/investments/holdings/{holding.id}/snapshots",
        data={"as_of_date": "2026-03-16", "gross": "abc"},
    )
    assert bad.status_code == 400
    gone = client.post(f"/investments/holdings/{holding.id}/snapshots/{anchor.id}/delete")
    assert gone.status_code == 303 and "ok=snapshot_deleted" in gone.headers["location"]
    assert anchors_of(container, holding.id) == []
    missing = client.post(f"/investments/holdings/{holding.id}/snapshots/{'0' * 32}/delete")
    assert missing.status_code in (400, 404)


def test_the_kpi_cards_and_the_per_account_update_action_are_rendered(
    client: TestClient, container: Container
) -> None:
    checking, _, _ = make_broker(client, container)
    page = client.get("/investments").text
    assert "Resultado dos investimentos" in page and "data-profit-kpis" in page
    for label in ("Saldo atual", "Total aportado", "Lucro total"):
        assert label in page
    with container.uow as work:
        account = next(a for a in work.accounts.list_all() if a.nickname == "Caixinha")
    client.post(
        f"/investments/{account.id}/settings",
        data={"asset_class": "fixed_income", "tracking": "account"},
    )
    client.post(
        "/entries",
        data={
            "kind": "transfer",
            "date": "2026-01-10",
            "amount": "1.000,00",
            "from_account": checking,
            "to_account": account.id,
        },
    )
    done = client.post(
        f"/investments/{account.id}/valuation", data={"date": "2026-01-31", "gross": "1.010,00"}
    )  # "Atualizar saldo": only the gross position
    assert done.status_code == 303
    response = client.get("/investments")
    assert "Atualizar saldo" in response.text and "data-snapshot-form" in response.text
    assert "+R$ 10,00" in visible(response)  # the profit of the account: 1.010,00 - 1.000,00


def test_a_transfer_into_a_note_is_linked_from_the_quick_form(
    client: TestClient, container: Container
) -> None:
    checking, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    holding = holding_of(container)
    form = client.get("/entries").text
    assert "data-transfer-holding" in form and f'value="{holding.id}"' in form
    assert "Destino do aporte" in form and "Saldo livre da conta" in form
    base = {
        "kind": "transfer",
        "date": TODAY.isoformat(),
        "amount": "500,00",
        "from_account": checking,
        "to_account": savings,
    }
    missing = client.post("/entries", data=base)  # a holdings-level account needs its note
    assert missing.status_code == 400 and "aplicação" in missing.text.lower()
    done = client.post("/entries", data={**base, "holding_id": holding.id})
    assert done.status_code == 303
    with container.uow as work:
        legs = work.transactions.list_by_account(savings)
        out = work.transactions.list_by_account(checking)
    assert [leg.holding_id for leg in legs] == [holding.id]
    assert all(leg.kind.value == "transfer" for leg in [*legs, *out])  # not income or expense
    page = client.get("/entries").text
    assert "↔ Aporte: Caixinha / CDB Banco X 110% CDI" in page


def test_editing_the_transfer_keeps_or_changes_the_note(
    client: TestClient, container: Container
) -> None:
    checking, savings, issuer = make_broker(client, container)
    new_holding(client, savings, issuer)
    holding = holding_of(container)
    client.post(
        "/entries",
        data={
            "kind": "transfer",
            "date": TODAY.isoformat(),
            "amount": "500,00",
            "from_account": checking,
            "to_account": savings,
            "holding_id": holding.id,
        },
    )
    with container.uow as work:
        (leg,) = work.transactions.list_by_account(savings)
    form = client.get(f"/entries/{leg.id}/edit", headers=HX).text
    assert re.search(rf'<option value="{holding.id}"[^>]*selected', form)
    saved = client.post(
        f"/entries/{leg.id}/edit",
        data={
            "amount": "700,00",
            "date": TODAY.isoformat(),
            "from_account": checking,
            "to_account": savings,
            "holding_id": holding.id,
        },
        headers=HX,
    )
    assert saved.status_code == 200 and "transfer_updated" in saved.headers["hx-redirect"]
    with container.uow as work:
        (after,) = work.transactions.list_by_account(savings)
    assert (after.amount_cents, after.holding_id) == (70_000, holding.id)
