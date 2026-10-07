"""Selection Mode on the web: row hooks, the merge window, batch delete and its confirmation."""

import datetime as dt
import re

from fastapi.testclient import TestClient
from test_web import HX, add_expense, buy_on_card, make_card, setup_accounts

from financas.container import Container
from financas.domain.models import Transaction

TODAY = dt.date.today()
LAST_MONTH = TODAY.replace(day=1) - dt.timedelta(days=1)
LOCK_PAST = (
    "Apenas compras do mês atual podem ser mescladas. "
    "Lançamentos de meses anteriores já estão consolidados."
)


def entries_of(container: Container, account: str) -> list[Transaction]:
    with container.uow as work:
        return work.transactions.list_by_account(account)


def ids_query(*ids: str) -> str:
    return "&".join(f"ids={i}" for i in ids)


def test_a_row_of_a_previous_month_is_locked_with_its_reason(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Agora")
    add_expense(client, checking, description="Antigo", date=LAST_MONTH.isoformat())
    now_page = client.get("/entries").text
    assert "data-sel-lock" not in now_page  # the month shown has only current rows
    old = client.get(f"/entries?month={LAST_MONTH:%Y-%m}").text
    row = old[old.index("data-sel-row") :]
    assert 'data-sel-lock="month"' in old and f'data-sel-reason="{LOCK_PAST}"' in old
    assert re.search(
        r'class="sel-check"[^>]*aria-disabled="true"[^>]*aria-describedby="sel-[0-9a-f]+-why"', row
    )
    assert '<span class="sel-vh" id="sel-' in old and LOCK_PAST in old
    assert 'aria-label="Selecionar Antigo, Conta Corrente"' in old  # never an amount


def test_a_row_after_the_current_month_gets_the_one_sentence_reason(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    later = (TODAY.replace(day=28) + dt.timedelta(days=10)).replace(day=3)
    add_expense(client, checking, description="Futuro", date=later.isoformat())
    page = client.get(f"/entries?month={later:%Y-%m}").text
    assert 'data-sel-lock="month"' in page
    assert 'data-sel-reason="Apenas compras do mês atual podem ser mescladas."' in page


def test_the_row_swap_keeps_its_selection_slot(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking)
    entry = entries_of(container, checking)[0]
    row = client.get(f"/entries/{entry.id}/row", headers=HX).text
    assert f'data-sel-id="entry:{entry.id}"' in row and 'class="sel-slot"' in row


def test_merge_outside_the_current_month_answers_422_with_the_offenders(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Agora")
    add_expense(client, checking, description="Antigo", date=LAST_MONTH.isoformat())
    ids = {e.description: e.id for e in entries_of(container, checking)}
    data = {"ids": list(ids.values()), "description": "X", "date": TODAY.isoformat()}
    answer = client.post("/entries/merge", data=data, headers=HX)
    assert answer.status_code == 422
    body = answer.json()
    assert body["code"] == "merge_outside_current_month" and body["ids"] == [ids["Antigo"]]
    assert body["message"] == LOCK_PAST
    plain = client.post("/entries/merge", data=data)
    assert (
        plain.status_code == 303 and "err=MERGE_OUTSIDE_CURRENT_MONTH" in plain.headers["location"]
    )
    assert len(entries_of(container, checking)) == 2


def test_merge_of_an_itemized_entry_answers_merge_not_allowed(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Um", amount="100,00")
    add_expense(client, checking, description="Dois", amount="50,00")
    first = next(e for e in entries_of(container, checking) if e.description == "Um")
    groceries = _category(container, "groceries")
    home = _category(container, "home")
    saved = client.post(
        f"/entries/{first.id}/edit",
        data={
            "amount": "100,00",
            "date": first.posted_on.isoformat(),
            "description": "Um",
            "splits_present": "1",
            "item_description": ["A", "B"],
            "item_category": [groceries, home],
            "item_amount": ["60,00", "40,00"],
        },
        headers=HX,
    )
    assert saved.status_code == 200
    ids = [e.id for e in entries_of(container, checking)]
    answer = client.post(
        "/entries/merge",
        data={"ids": ids, "description": "X", "date": TODAY.isoformat()},
        headers=HX,
    )
    assert answer.status_code == 422 and answer.json()["code"] == "merge_not_allowed"


def _category(container: Container, slug: str) -> str:
    with container.uow as work:
        found = work.categories.get_by_slug(slug)
    assert found
    return found.id


# --- batch delete: the confirmation dialog ---


def test_confirm_for_one_entry_has_no_plan_note(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking)
    entry = entries_of(container, checking)[0]
    page = client.get(f"/entries/batch-delete/confirm?{ids_query(f'entry:{entry.id}')}")
    assert page.status_code == 200 and "<html" not in page.text
    assert 'id="sel-delete-dialog"' in page.text and 'role="alertdialog"' in page.text
    assert "Apagar 1 lançamento?" in page.text and ">Apagar 1 lançamento</button>" in page.text
    assert 'aria-describedby="del-d"' in page.text and "dlg-note" not in page.text
    assert (
        "Esta ação não pode ser desfeita. O saldo das contas e os totais de fatura serão "
        "recalculados imediatamente." in page.text
    )
    assert page.text.index('id="del-no"') < page.text.index('id="del-yes"')
    assert 'id="del-no" autofocus' in page.text


def test_confirm_lists_at_most_three_plans_and_counts_the_rest(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    for n in range(4):
        buy_on_card(
            client, card, days_ago=0, installments="3", amount="90,00", description=f"Compra {n}"
        )
    with container.uow as work:
        plans = work.plans.list_all()
    query = ids_query(*[f"plan:{p.id}" for p in plans])
    page = client.get(f"/entries/batch-delete/confirm?{query}").text
    assert "Apagar 4 lançamentos?" in page and "Compras parceladas na seleção" in page
    assert page.count("<li>") == 3 and "e mais 1 compra parcelada" in page
    assert 'aria-describedby="del-d del-n"' in page
    assert "3x" in page and "apaga 3 parcelas pendentes" in page


def test_confirm_errors_are_json_with_a_code(client: TestClient, container: Container) -> None:
    setup_accounts(client, container)
    assert client.get("/entries/batch-delete/confirm?ids=entry:ghost").status_code == 404
    bad = client.get("/entries/batch-delete/confirm?ids=card:1")
    assert bad.status_code == 422 and bad.json()["code"] == "bad_id"
    many = client.get(
        "/entries/batch-delete/confirm?" + ids_query(*[f"entry:{n}" for n in range(201)])
    )
    assert many.status_code == 422 and many.json()["code"] == "too_many_ids"
    assert "200" in many.json()["message"]


# --- batch delete: the request ---


def test_batch_delete_removes_the_rows_and_tells_the_page(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    for name in ("Um", "Dois", "Três"):
        add_expense(client, checking, description=name)
    rows = {e.description: e.id for e in entries_of(container, checking)}
    done = client.post(
        "/entries/batch-delete",
        data={"ids": [f"entry:{rows['Um']}", f"entry:{rows['Dois']}", "entry:ghost"]},
    )
    assert done.status_code == 200
    assert done.headers["hx-trigger"] == '{"selection:deleted": {"deleted": 2, "skipped": 1}}'
    assert [e.description for e in entries_of(container, checking)] == ["Três"]
    page = client.get("/entries").text
    assert "Três" in page and "Um" not in page.split('id="lista"')[1]


def test_batch_delete_rejects_a_closed_month_and_deletes_nothing(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    add_expense(client, checking, description="Agora")
    add_expense(client, checking, description="Antigo", date=LAST_MONTH.isoformat())
    rows = {e.description: e.id for e in entries_of(container, checking)}
    answer = client.post(
        "/entries/batch-delete",
        data={"ids": [f"entry:{rows['Agora']}", f"entry:{rows['Antigo']}"]},
    )
    assert answer.status_code == 422
    body = answer.json()
    assert body["code"] == "delete_outside_current_month" and body["ids"] == [
        f"entry:{rows['Antigo']}"
    ]
    assert len(entries_of(container, checking)) == 2


def test_batch_delete_of_a_plan_removes_its_pending_installments(
    client: TestClient, container: Container
) -> None:
    _, card = make_card(client, container)
    buy_on_card(client, card, days_ago=0, installments="3", amount="90,00", description="Monitor")
    with container.uow as work:
        (plan,) = work.plans.list_all()
    answer = client.post("/entries/batch-delete", data={"ids": [f"plan:{plan.id}"]})
    assert answer.status_code == 200
    assert entries_of(container, card) == []
    with container.uow as work:
        assert work.plans.list_all() == []


def test_batch_delete_caps_the_request_at_200_ids(client: TestClient, container: Container) -> None:
    setup_accounts(client, container)
    answer = client.post("/entries/batch-delete", data={"ids": [f"entry:{n}" for n in range(201)]})
    assert answer.status_code == 422 and answer.json()["code"] == "too_many_ids"
