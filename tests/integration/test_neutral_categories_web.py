"""Pass-through ("neutral") categories over HTTP: the toggle, the feed tag and the totals."""

import datetime as dt

from fastapi.testclient import TestClient
from test_web import setup_accounts

from financas.container import Container
from html_text import visible

TODAY = dt.date.today()


def category(container: Container, slug: str):
    with container.uow as work:
        found = work.categories.get_by_slug(slug)
    assert found
    return found


def enter(
    client: TestClient, container: Container, account: str, kind: str, amount: str, slug: str
):
    return client.post(
        "/entries",
        data={
            "kind": kind,
            "account_id": account,
            "date": TODAY.isoformat(),
            "amount": amount,
            "description": f"{kind} {slug}",
            "category_id": category(container, slug).id,
        },
    )


def test_toggling_a_category_neutral_persists_and_shows_the_badge(
    client: TestClient, container: Container
) -> None:
    food = category(container, "food")
    assert not food.is_neutral
    page = client.get("/categories")
    assert 'action="/categories/' + food.id + '/neutral"' in page.text
    assert "Categoria neutra (não afeta gastos pessoais, orçamento nem receitas)" in page.text
    assert "Ideal para reembolsos, contas pagas para terceiros ou adiantamentos" in page.text
    done = client.post(f"/categories/{food.id}/neutral", data={"neutral": "1"})
    assert done.status_code == 303 and "ok=category_neutral" in done.headers["location"]
    assert category(container, "food").is_neutral
    assert "data-neutral-badge" in client.get("/categories").text
    client.post(f"/categories/{food.id}/neutral", data={})  # unchecked
    assert not category(container, "food").is_neutral


def test_a_category_can_be_created_neutral(client: TestClient, container: Container) -> None:
    done = client.post(
        "/categories",
        data={"name": "Adiantamentos", "group": "non_essential", "kind": "expense", "neutral": "1"},
    )
    assert done.status_code == 303
    with container.uow as work:
        made = next(c for c in work.categories.list_all() if c.name == "Adiantamentos")
    assert made.is_neutral


def test_a_movement_category_cannot_be_made_neutral_over_http(
    client: TestClient, container: Container
) -> None:
    transfer = category(container, "transfer")
    refused = client.post(f"/categories/{transfer.id}/neutral", data={"neutral": "1"})
    assert refused.status_code == 400 and "podem ser neutras" in refused.text


def test_entries_tag_neutral_money_and_keep_the_days_spending_clean(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    client.post(
        f"/accounts/{checking}/balance",
        data={"date": (TODAY - dt.timedelta(days=1)).isoformat(), "amount": "1.000,00"},
    )
    assert enter(client, container, checking, "expense", "250,00", "third_party").status_code == 303
    assert enter(client, container, checking, "income", "250,00", "third_party").status_code == 303
    assert enter(client, container, checking, "expense", "40,00", "food").status_code == 303
    response = client.get("/entries")
    page, text = response.text, visible(response)
    assert page.count("data-neutral-tag") == 2 and "Neutro" in text
    assert "Gastos do dia: R$ 40,00" in text  # the R$ 250,00 passed through: not spending
    assert "Em trânsito / Reembolsos: pagos R$ 250,00 · recebidos R$ 250,00" in text
    assert "Saldo da conta: R$ 960,00" in text  # the bank is exact: 1.000 - 250 + 250 - 40
    assert "is-neutral" in page  # the amount is muted


def test_a_day_with_only_neutral_money_shows_zero_spending(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    enter(client, container, checking, "expense", "250,00", "third_party")
    text = visible(client.get("/entries"))
    assert "Gastos do dia: R$ 0,00" in text


def test_the_dashboard_totals_ignore_neutral_entries(
    client: TestClient, container: Container
) -> None:
    checking, _ = setup_accounts(client, container)
    enter(client, container, checking, "income", "5.000,00", "salary")
    enter(client, container, checking, "expense", "100,00", "home")
    enter(client, container, checking, "expense", "999,00", "third_party")
    enter(client, container, checking, "income", "999,00", "third_party")
    home = visible(client.get("/"))
    assert "R$ 4.900,00" in home  # 5.000 income - 100 spending
    assert "999,00" not in home
