"""``/analises``: the "Compras Mais Caras" panel and its Top 5 / Top 10 fragment."""

import datetime as dt
import re

from fastapi.testclient import TestClient
from test_web import HX, add_expense, buy_on_card, make_card

from financas.container import Container

TODAY = dt.date.today()
MONTH = f"{TODAY:%Y-%m}"


def seed(client: TestClient, container: Container) -> None:
    checking, card = make_card(client, container)
    for n in range(12):
        add_expense(
            client,
            checking,
            description=f"Despesa {n:02d}",
            amount=f"{100 + n},00",
            date=f"{MONTH}-01",
        )
    buy_on_card(client, card, days_ago=0, installments="3", amount="900,00", description="Monitor")
    add_expense(client, checking, description="Estornada", amount="9.999,00", date=f"{MONTH}-02")


def rows_of(html: str) -> list[str]:
    table = html.split('<table class="rank-table">')[1].split("</table>")[0]
    return re.findall(r'<td class="rank-desc"><a [^>]*title="([^"]*)"', table)


def test_the_panel_is_on_the_page_with_five_rows_by_default(
    client: TestClient, container: Container
) -> None:
    seed(client, container)
    page = client.get("/analises")
    assert page.status_code == 200 and 'id="rank-expensive"' in page.text
    assert "Compras Mais Caras" in page.text and "Maiores despesas do período" in page.text
    assert 'role="radiogroup" aria-label="Quantidade"' in page.text
    assert re.search(r'name="top" value="5" checked', page.text)
    names = rows_of(page.text)
    assert len(names) == 5 and names[0] == "Estornada"  # not yet marked as refunded here
    assert 'id="rank-live"' in page.text and "<caption" in page.text and 'scope="col"' in page.text
    assert '<ol class="rank-list">' in page.text


def test_top_ten_returns_the_fragment_and_a_bad_top_falls_back_to_five(
    client: TestClient, container: Container
) -> None:
    seed(client, container)
    ten = client.get(f"/analises/maiores-despesas?top=10&month={MONTH}", headers=HX)
    assert ten.status_code == 200 and "<html" not in ten.text
    assert len(rows_of(ten.text)) == 10
    assert 'hx-swap-oob="innerHTML:#rank-live"' in ten.text
    assert "Mostrando as 10 maiores despesas." in ten.text
    assert re.search(r'name="top" value="10" checked', ten.text)
    assert len(rows_of(client.get("/analises/maiores-despesas?top=7").text)) == 5
    assert len(rows_of(client.get("/analises/maiores-despesas?top=abc").text)) == 5


def test_an_installment_purchase_is_one_row_with_its_badge_and_a_negative_total(
    client: TestClient, container: Container
) -> None:
    seed(client, container)
    page = client.get(f"/analises/maiores-despesas?top=10&month={MONTH}").text
    table = page.split('<table class="rank-table">')[1].split("</table>")[0]
    assert table.count('title="Monitor"') == 1 and "3x</span>" in table
    assert 'data-cents="-90000"' in table and 'data-private="transactions"' in table
    assert re.search(r'href="/entries\?month=\d{4}-\d{2}#entry-[0-9a-f]{32}"', table)


def test_an_empty_period_says_so_without_a_table(client: TestClient, container: Container) -> None:
    make_card(client, container)
    page = client.get("/analises/maiores-despesas?month=2020-01").text
    assert "Nenhuma despesa no período." in page
    assert '<table class="rank-table">' not in page and '<ol class="rank-list">' not in page
