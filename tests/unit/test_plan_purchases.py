"""Installment plans as one purchase on the entries list, and moving the purchase date (9.4)."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.plan_purchases import GetPlanPurchase, ListPlanPurchases
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.plan_dates import ReschedulePlanPurchase
from financas.application.use_cases.transactions import (
    SplitItem,
    UpdateTransaction,
    UpdateTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, Transaction
from financas.domain.money import YearMonth

D = dt.date
TODAY = D(2026, 7, 20)  # July's statement (closes 07-25) is open


def cat(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def buy(uow: MemoryUnitOfWork, card: Account, **kw: object):
    values: dict[str, object] = {
        "account_id": card.id,
        "description": "Monitor",
        "purchased_on": D(2026, 7, 10),
        "installments": 3,
        "total_cents": 45_000,
        "category_id": cat(uow, "shopping"),
    }
    values.update(kw)
    return RegisterCardPurchase(uow).execute(CardPurchaseCommand(**values))  # type: ignore[arg-type]


def months_of(uow: MemoryUnitOfWork, entries: list[Transaction]) -> list[YearMonth]:
    out = []
    for e in entries:
        assert e.statement_id
        statement = uow.statements.get(e.statement_id)
        assert statement
        out.append(statement.month)
    return out


def fresh(uow: MemoryUnitOfWork, plan_id: str) -> list[Transaction]:
    return sorted(uow.transactions.list_by_plan(plan_id), key=lambda t: t.installment_number or 0)


# --- the consolidated purchase ---


def test_a_plan_is_one_purchase_with_its_total_and_installment(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(uow, card, total_cents=30_100)  # 100,34 + 100,33 + 100,33
    assert result.plan
    (purchase,) = ListPlanPurchases(uow).execute()
    assert purchase.plan.id == result.plan.id
    assert purchase.purchased_on == D(2026, 7, 10)
    assert (purchase.total_cents, purchase.count) == (30_100, 3)
    assert purchase.installment_cents == 10_033  # what most installments have
    assert not purchase.uniform and not purchase.is_partial and not purchase.is_refunded
    assert purchase.anchor.installment_number == 1
    assert purchase.category_ids == {cat(uow, "shopping")}
    entry = purchase.as_entry()
    assert (entry.id, entry.posted_on, entry.amount_cents) == (
        purchase.anchor.id,
        D(2026, 7, 10),
        -30_100,
    )


def test_items_show_their_total_and_how_they_fall_on_the_installments(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(
        uow,
        card,
        category_id=None,
        splits=(
            SplitItem("Monitor Gamer", cat(uow, "shopping"), 35_000),
            SplitItem("Cabo HDMI e Suporte", cat(uow, "home"), 10_000),
        ),
    )
    assert result.plan
    purchase = GetPlanPurchase(uow).execute(result.plan.id)
    assert purchase
    monitor, cable = purchase.items
    assert (monitor.description, monitor.total_cents) == ("Monitor Gamer", 35_000)
    assert monitor.breakdown == ((2, 11_667), (1, 11_666))  # 350,00 over three installments
    assert (cable.total_cents, cable.breakdown) == (10_000, ((2, 3_333), (1, 3_334)))
    assert sum(c * n for n, c in monitor.breakdown) == monitor.total_cents
    assert purchase.category_ids == {cat(uow, "shopping"), cat(uow, "home")}
    assert purchase.as_entry().category_id is None  # the items carry the categories
    assert purchase.uniform and purchase.installment_cents == 15_000


def test_a_running_purchase_is_anchored_on_its_first_installment_on_file(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(
        uow, card, purchased_on=None, current_installment=2, statement_month=YearMonth(2026, 8)
    )
    assert result.plan
    purchase = GetPlanPurchase(uow).execute(result.plan.id)
    assert purchase
    assert purchase.is_partial and purchase.count == 2
    assert purchase.anchor.installment_number == 2
    assert purchase.purchased_on == purchase.anchor.posted_on  # no purchase date known


def test_refunded_installments_count_nowhere_in_the_total(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(uow, card)
    assert result.plan
    from dataclasses import replace

    last = result.transactions[-1]
    uow.transactions.update(replace(last, is_refunded=True))
    purchase = GetPlanPurchase(uow).execute(result.plan.id)
    assert purchase and purchase.total_cents == 30_000 and not purchase.is_refunded


def test_single_payments_are_not_purchases_of_a_plan(uow: MemoryUnitOfWork, card: Account) -> None:
    buy(uow, card, installments=1)
    assert ListPlanPurchases(uow).execute() == []


# --- moving the purchase date ---


def test_a_new_purchase_date_moves_every_pending_installment(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(uow, card)  # 2026-07-10: statements 2026-07, 08, 09
    assert result.plan
    moved = ReschedulePlanPurchase(uow, FixedClock(TODAY)).execute(result.plan.id, D(2026, 7, 26))
    assert (moved.moved, moved.kept) == (3, 0)  # 07-26 is after July's closing (07-25)
    entries = fresh(uow, result.plan.id)
    assert months_of(uow, entries) == [YearMonth(2026, 8), YearMonth(2026, 9), YearMonth(2026, 10)]
    assert entries[0].posted_on == D(2026, 7, 26)  # installment 1 is dated on the purchase
    assert all(e.posted_on > D(2026, 8, 1) for e in entries[1:])  # the others, in their cycle
    assert uow.plans.get(result.plan.id).purchased_on == D(2026, 7, 26)  # type: ignore[union-attr]
    assert sum(-e.amount_cents for e in entries) == 45_000  # amounts never change


def test_the_same_date_changes_nothing(uow: MemoryUnitOfWork, card: Account) -> None:
    result = buy(uow, card)
    assert result.plan
    before = fresh(uow, result.plan.id)
    moved = ReschedulePlanPurchase(uow, FixedClock(TODAY)).execute(result.plan.id, D(2026, 7, 10))
    assert (moved.moved, moved.kept) == (0, 0)
    assert fresh(uow, result.plan.id) == before


def test_installments_on_closed_statements_stay_where_they_are(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    # installment 2 of 3 on July (closed on 08-01), installment 3 on August
    result = buy(
        uow, card, purchased_on=None, current_installment=2, statement_month=YearMonth(2026, 7)
    )
    assert result.plan
    moved = ReschedulePlanPurchase(uow, FixedClock(D(2026, 8, 1))).execute(
        result.plan.id, D(2026, 7, 26)
    )
    assert (moved.moved, moved.kept) == (1, 1)
    second, third = fresh(uow, result.plan.id)
    assert months_of(uow, [second, third]) == [YearMonth(2026, 7), YearMonth(2026, 10)]
    assert second.posted_on == result.transactions[0].posted_on  # history untouched


def test_a_closed_first_installment_refuses_the_new_date(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(uow, card)  # installment 1 on July, closed on 08-01
    assert result.plan
    with pytest.raises(DomainError) as error:
        ReschedulePlanPurchase(uow, FixedClock(D(2026, 8, 1))).execute(
            result.plan.id, D(2026, 8, 20)
        )
    assert error.value.code == "PURCHASE_DATE_LOCKED"


def test_a_new_date_that_lands_on_a_closed_statement_is_refused_without_changes(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(
        uow, card, purchased_on=None, current_installment=2, statement_month=YearMonth(2026, 9)
    )
    assert result.plan
    before = fresh(uow, result.plan.id)
    with pytest.raises(DomainError) as error:
        ReschedulePlanPurchase(uow, FixedClock(D(2026, 8, 20))).execute(
            result.plan.id,
            D(2026, 6, 1),  # installment 2 would fall on July: already closed
        )
    assert error.value.code == "PLAN_TARGET_STATEMENT_CLOSED"
    assert fresh(uow, result.plan.id) == before
    assert uow.plans.get(result.plan.id).purchased_on is None  # type: ignore[union-attr]


def test_editing_the_purchase_date_and_the_installment_is_one_atomic_edit(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(uow, card)
    assert result.plan
    first = result.transactions[0]
    UpdateTransaction(uow, FixedClock(TODAY)).execute(
        UpdateTransactionCommand(
            first.id,
            first.posted_on,  # the form posts the date the installment had
            15_000,
            "Monitor novo",
            purchase_date=D(2026, 7, 26),
        )
    )
    entries = fresh(uow, result.plan.id)
    assert months_of(uow, entries) == [YearMonth(2026, 8), YearMonth(2026, 9), YearMonth(2026, 10)]
    assert entries[0].posted_on == D(2026, 7, 26)
    assert {e.description for e in entries} == {"Monitor novo"}  # the description spread too

    with pytest.raises(DomainError):  # a refused edit changes nothing
        UpdateTransaction(uow, FixedClock(TODAY)).execute(
            UpdateTransactionCommand(
                entries[0].id, entries[0].posted_on, 15_000, "   ", purchase_date=D(2026, 7, 1)
            )
        )
    assert fresh(uow, result.plan.id)[0].posted_on == D(2026, 7, 26)
