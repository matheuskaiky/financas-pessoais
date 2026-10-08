"""Items on an expense of a checking account (PIX, débito, boleto) from the quick entry form."""

import re

from fastapi.testclient import TestClient
from test_web import category_by_slug, make_card, setup_accounts

from financas.application.queries.summary import GetSummary, Period
from financas.container import Container
from financas.domain.money import YearMonth

DAY = "2026-07-04"


def items(container: Container, *rows: tuple[str, str, str]) -> dict[str, list[str]]:
    return {
        "item_description": [d for d, _, _ in rows],
        "item_category": [category_by_slug(container, slug).id for _, slug, _ in rows],
        "item_amount": [a for _, _, a in rows],
    }


def post_pix(client: TestClient, container: Container, account: str, **extra: object):
    data: dict[str, object] = {
        "kind": "expense",
        "date": DAY,
        "amount": "180,00",
        "description": "Feira de sábado",
        "account_id": account,
        "payment_method": "pix",
        "splits_present": "1",
        **items(container, ("Hortifruti", "groceries", "110,00"), ("Açougue", "food", "70,00")),
        **extra,
    }
    return client.post("/entries", data=data)


def test_a_pix_split_in_two_is_one_debit_and_the_categories_follow_the_items(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    assert post_pix(client, container, checking).status_code == 303
    with container.uow as work:
        (parent,) = work.transactions.list_by_account(checking)
        split = work.transactions.splits_for([parent.id])[parent.id]
        movements = [m[1] for m in work.transactions.movements(checking)]
    assert (parent.amount_cents, parent.category_id) == (-18_000, None)
    assert parent.payment_method is not None and parent.payment_method.value == "pix"
    assert [(i.description, i.amount_cents) for i in split] == [
        ("Hortifruti", 11_000),
        ("Açougue", 7_000),
    ]
    assert movements == [-18_000]  # the items move no balance
    summary = GetSummary(container.uow).execute(Period.month(YearMonth(2026, 7)))
    by_category = {r.category_id: r.total_cents for r in summary.by_category}
    assert by_category[category_by_slug(container, "groceries").id] == 11_000
    assert by_category[category_by_slug(container, "food").id] == 7_000
    assert sum(by_category.values()) == summary.expenses_cents == 18_000  # nothing counted twice


def test_the_list_shows_the_split_row_with_its_items(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    post_pix(client, container, checking)
    page = client.get("/entries?month=2026-07").text
    row = page[page.index('id="lista"') :]
    assert "Por item" in row and "▾ 2 itens" in row and "Hortifruti" in row
    assert re.search(r'class="tag tag--method"[^>]*>PIX<', row)


def test_a_sum_that_does_not_match_is_refused_and_the_items_stay_on_the_form(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    bad = post_pix(
        client,
        container,
        checking,
        **items(container, ("Hortifruti", "groceries", "110,00"), ("Açougue", "food", "60,00")),
    )
    assert bad.status_code == 400 or "Os itens precisam somar" in bad.text
    assert "Os itens precisam somar o valor do lançamento" in bad.text
    assert 'value="Hortifruti"' in bad.text and 'value="60,00"' in bad.text  # nothing is lost
    with container.uow as work:
        assert work.transactions.list_by_account(checking) == []


def test_items_are_for_expenses_only(client: TestClient, container: Container) -> None:
    checking, _ = setup_accounts(client, container)
    answer = post_pix(client, container, checking, kind="income")
    assert "Só despesas podem ser divididas em itens" in answer.text
    with container.uow as work:
        assert work.transactions.list_by_account(checking) == []


def test_the_toggle_off_sends_only_the_marker_and_the_entry_is_plain(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    answer = client.post(
        "/entries",
        data={
            "kind": "expense",
            "date": DAY,
            "amount": "50,00",
            "description": "Café",
            "account_id": checking,
            "category_id": category_by_slug(container, "food").id,
            "splits_present": "1",
        },
    )
    assert answer.status_code == 303
    with container.uow as work:
        (entry,) = work.transactions.list_by_account(checking)
        assert work.transactions.splits_for([entry.id]) == {}
    assert entry.category_id == category_by_slug(container, "food").id


def test_the_quick_form_has_the_editor_with_the_bank_wording(
    client: TestClient, container: Container
) -> None:
    make_card(client, container)
    page = client.get("/entries").text
    form = page[page.index("data-split-field") :]
    assert "Adicionar itens / Dividir categorias" in form
    assert (
        "O saldo da conta continua vendo o lançamento inteiro; os gastos por categoria seguem "
        "os itens." in form
    )
    assert "A fatura, o saldo e o limite" not in form  # the card wording is not for PIX or débito
    assert "Total da compra" not in form and "faturas" not in form.split("</fieldset>")[0]
