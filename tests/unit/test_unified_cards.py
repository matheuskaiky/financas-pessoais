"""The unified cards view: hero, meter, chips and the keyset timeline."""

import datetime as dt
import itertools

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.cards import CardView, ListCards
from financas.application.queries.unified_cards import (
    PAGE_SIZE,
    ListUnifiedTimeline,
    parse_card_ids,
    parse_cursor,
    summarize,
)
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.catalog import CreateAccount, CreateAccountCommand
from financas.domain.models import Account, AccountKind, Institution
from financas.domain.services.statements import LimitAlert
from financas.interfaces.messages.unified_cards import (
    unified_chips,
    unified_live_text,
    unified_url,
)

D = dt.date
TODAY = D(2026, 7, 20)  # July's statements (close on the 25th for due day 5 / 11 days) are open
CLOCK = FixedClock(TODAY)


def make_card(
    uow: MemoryUnitOfWork, institution: Institution, name: str, limit: int | None
) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.CREDIT_CARD,
            institution.id,
            name,
            closing_days_before_due=11,
            due_day=5,
            credit_limit_cents=limit,
        )
    )


def buy(uow: MemoryUnitOfWork, card: Account, cents: int, day: int = 10, **kw: object):
    values: dict[str, object] = {
        "account_id": card.id,
        "description": "Compra",
        "purchased_on": D(2026, 7, day),
        "total_cents": cents,
    }
    values.update(kw)
    return RegisterCardPurchase(uow).execute(CardPurchaseCommand(**values))  # type: ignore[arg-type]


@pytest.fixture
def three(uow: MemoryUnitOfWork, institution: Institution) -> list[Account]:
    cards = [
        make_card(uow, institution, "Nubank", 100_000),
        make_card(uow, institution, "Itaú", 50_000),
        make_card(uow, institution, "Inter", None),  # no limit
    ]
    buy(uow, cards[0], 12_345)
    buy(uow, cards[1], 6_000)
    buy(uow, cards[2], 700)
    return cards


def views(uow: MemoryUnitOfWork) -> list[CardView]:
    return ListCards(uow, CLOCK).execute().cards


def subsets(items: list[CardView]):
    for size in range(1, len(items) + 1):
        yield from itertools.combinations(items, size)


def test_hero_equals_the_sum_of_the_faces_for_every_subset(
    uow: MemoryUnitOfWork, three: list[Account]
) -> None:
    every = views(uow)
    seen = 0
    for picked in subsets(every):
        summary = summarize(list(picked), len(every))
        face_sum = sum(v.telemetry.open_balance_cents for v in picked if v.telemetry)
        assert summary.hero_cents == face_sum and summary.included == len(picked)
        seen += 1
    assert seen == 7  # the 7 non-empty subsets of 3 cards


def test_the_meter_ignores_cards_without_a_limit(
    uow: MemoryUnitOfWork, three: list[Account]
) -> None:
    every = views(uow)
    summary = summarize(every, 3)
    assert (summary.committed_cents, summary.limit_cents) == (18_345, 150_000)  # Inter is out
    assert (summary.with_limit, summary.without_limit) == (2, 1)
    assert summary.usage and round(summary.usage.percent or 0, 1) == 12.2
    only_inter = summarize([every[2]], 3)
    assert only_inter.usage is None and only_inter.limit_cents is None  # never 0 %
    assert only_inter.without_limit == 1


@pytest.mark.parametrize(
    ("spent", "alert"),
    [(79_900, LimitAlert.NONE), (80_000, LimitAlert.WARNING), (100_000, LimitAlert.EXCEEDED)],
)
def test_meter_tones_follow_the_shipped_thresholds(
    uow: MemoryUnitOfWork, institution: Institution, spent: int, alert: LimitAlert
) -> None:
    card = make_card(uow, institution, "Único", 100_000)
    buy(uow, card, spent)
    summary = summarize(views(uow), 1)
    assert summary.usage and summary.usage.alert is alert


def test_an_unavailable_statement_makes_the_hero_unavailable(
    uow: MemoryUnitOfWork, three: list[Account]
) -> None:
    every = views(uow)
    broken = [every[0], CardView(every[1].account, every[1].usage, every[1].statements, None)]
    summary = summarize(broken, 3)
    assert summary.hero_cents is None and summary.unavailable == 1  # never a partial sum


def test_card_ids_keep_face_order_and_unknown_ones_mean_everything() -> None:
    order = ["a", "b", "c"]
    assert parse_card_ids("c,a", order) == ["a", "c"]
    assert parse_card_ids("", order) == order
    assert parse_card_ids("x,y", order) == order  # nothing known: every card
    assert parse_card_ids(" b ,,", order) == ["b"]


def test_chips_toggle_independently_and_the_last_one_cannot_go(
    uow: MemoryUnitOfWork, three: list[Account]
) -> None:
    every = views(uow)
    ids = [v.account.id for v in every]
    chips = unified_chips(every, ids)
    assert [c.label for c in chips] == ["Todos", "Nubank", "Itaú", "Inter"]
    assert chips[0].pressed and all(c.pressed for c in chips)
    assert (
        chips[1].url == f"/cards?card=all&cards={ids[1]},{ids[2]}"
    )  # the page after its own press
    only = unified_chips(every, [ids[1]])
    assert [c.pressed for c in only] == [False, False, True, False]
    assert only[2].disabled and not only[1].disabled
    assert (
        only[0].url == "/cards?card=all"
        and only[1].url == f"/cards?card=all&cards={ids[0]},{ids[1]}"
    )
    assert unified_url(ids, [ids[2], ids[0]]) == f"/cards?card=all&cards={ids[0]},{ids[2]}"


def test_live_text_names_the_cards_without_amounts() -> None:
    assert unified_live_text(["Nubank", "Itaú", "Inter"], True) == "Mostrando todos os cartões."
    assert unified_live_text(["Nubank", "Itaú"], False) == "Mostrando Nubank e Itaú."
    assert (
        unified_live_text(["Nubank", "Itaú", "Inter"], False) == "Mostrando Nubank, Itaú e Inter."
    )
    assert unified_live_text(["Nubank"], False) == "Mostrando Nubank."


def test_first_page_is_the_open_statements_and_older_pages_walk_the_closed_ones(
    uow: MemoryUnitOfWork, institution: Institution
) -> None:
    card = make_card(uow, institution, "Nubank", 100_000)
    buy(uow, card, 1_000, purchased_on=D(2026, 6, 10))  # June: closed (closed 06-25)
    buy(uow, card, 2_000)  # July: open
    timeline = ListUnifiedTimeline(uow, CLOCK)
    first = timeline.execute(views(uow))
    assert [t.amount_cents for t in first.entries] == [-2_000]
    assert first.older_available and first.older_cursor is None
    older = timeline.execute(views(uow), older=True)
    assert [t.amount_cents for t in older.entries] == [-1_000] and older.older_cursor is None
    assert card.id in first.cards


def test_keyset_pages_have_no_duplicates_or_gaps_when_rows_share_a_date(
    uow: MemoryUnitOfWork, institution: Institution
) -> None:
    card = make_card(uow, institution, "Nubank", 10_000_000)
    for n in range(PAGE_SIZE + 3):  # a closed month with more rows than a page, all on 06-10
        buy(uow, card, 100 + n, purchased_on=D(2026, 6, 10), description=f"Junho {n}")
    timeline = ListUnifiedTimeline(uow, CLOCK)
    one = timeline.execute(views(uow), older=True)
    assert len(one.entries) == PAGE_SIZE and one.older_cursor
    cursor = parse_cursor(one.older_cursor)
    assert cursor and cursor[0] == D(2026, 6, 10)
    two = timeline.execute(views(uow), older=True, after=one.older_cursor)
    assert len(two.entries) == 3 and two.older_cursor is None
    seen = [t.id for t in one.entries] + [t.id for t in two.entries]
    assert len(seen) == len(set(seen)) == PAGE_SIZE + 3
    assert sorted(seen, reverse=True) == seen  # same date: newest id first, across the boundary


def test_a_bad_cursor_is_ignored() -> None:
    assert parse_cursor("garbage") is None and parse_cursor("2026-13-40_x") is None


def test_installments_are_consolidated_per_month_over_the_included_cards(
    uow: MemoryUnitOfWork, institution: Institution
) -> None:
    from financas.application.queries.cards import InstallmentSchedule, ListActiveInstallments
    from financas.application.queries.unified_cards import consolidate_plans, consolidate_schedule

    nu = make_card(uow, institution, "Nubank", 100_000)
    itau = make_card(uow, institution, "Itaú", None)
    buy(uow, nu, 9_000, installments=3)  # 3 x 30,00
    buy(uow, itau, 4_000, installments=2)  # 2 x 20,00
    buy(uow, itau, 1_000)  # not an installment
    plans = ListActiveInstallments(uow, CLOCK).execute()
    rows = InstallmentSchedule(uow, CLOCK).execute()
    both = {nu.id, itau.id}
    assert {p.plan.description for p in consolidate_plans(plans, both)} == {"Compra"}
    assert len(consolidate_plans(plans, both)) == 2 and len(consolidate_plans(plans, {nu.id})) == 1
    months = consolidate_schedule(rows, both)
    assert [r.amount_cents for r in months] == [5_000, 5_000, 3_000]  # 30+20, 30+20, 30
    assert all(r.account_id == "" for r in months)  # consolidated: no single card
    only_itau = consolidate_schedule(rows, {itau.id})
    assert [r.amount_cents for r in only_itau] == [2_000, 2_000]
    assert consolidate_schedule(rows, set()) == []
