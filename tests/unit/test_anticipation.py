"""Anticipating installments into the open statement (CLAUDE.md 9.4)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.cards import ListActiveInstallments
from financas.application.use_cases.anticipation import (
    AnticipateInstallments,
    AnticipationCommand,
    PreviewAnticipation,
)
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, TransactionKind

D = dt.date
TODAY = D(2026, 7, 1)  # the 2026-07 statement is open (closes 07-25, due 08-05)


def plan_of(uow: MemoryUnitOfWork, card: Account, installments: int = 4, total: int = 4_000):
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id,
            "Notebook",
            purchased_on=D(2026, 7, 1),
            installments=installments,
            total_cents=total,
        )
    )
    assert result.plan
    return result.plan


def run(uow: MemoryUnitOfWork, plan_id: str, *, today: dt.date = TODAY, **kw: Any):
    cmd = AnticipationCommand(plan_id, **kw)
    return AnticipateInstallments(uow, FixedClock(today)).execute(cmd)


def statement_of(uow: MemoryUnitOfWork, entry_id: str) -> str:
    entry = uow.transactions.get(entry_id)
    assert entry and entry.statement_id
    statement = uow.statements.get(entry.statement_id)
    assert statement
    return str(statement.month)


def test_preview_lists_future_installments_and_writes_nothing(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = plan_of(uow, card)
    before = dict(uow.transactions.items)
    preview = PreviewAnticipation(uow, FixedClock(TODAY)).execute(AnticipationCommand(plan.id))
    assert [ln.number for ln in preview.lines] == [2, 3, 4]  # installment 1 is already billed
    assert str(preview.target_month) == "2026-07"
    assert (preview.target_closing_date, preview.target_due_date) == (D(2026, 7, 25), D(2026, 8, 5))
    assert preview.nominal_cents == 3_000 and preview.discount_cents == 0
    assert preview.final_cents == 3_000
    assert uow.transactions.items == before


def test_anticipate_all_moves_them_to_the_open_statement_and_keeps_the_numbering(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = plan_of(uow, card)
    result = run(uow, plan.id)
    assert result.final_cents == 3_000
    rows = uow.transactions.list_by_plan(plan.id)
    assert [r.installment_number for r in rows] == [1, 2, 3, 4]
    assert {statement_of(uow, r.id) for r in rows} == {"2026-07"}
    assert all(r.posted_on == D(2026, 7, 24) for r in rows[1:])  # the cycle's last day
    assert sum(-r.amount_cents for r in rows) == 4_000  # the plan total is untouched
    assert uow.plans.get(plan.id) == plan
    (view,) = ListActiveInstallments(uow, FixedClock(TODAY)).execute()
    assert view.remaining_count == 4


def test_anticipate_some_installments(uow: MemoryUnitOfWork, card: Account) -> None:
    plan = plan_of(uow, card)
    run(uow, plan.id, installment_numbers=(3,))
    months = {
        r.installment_number: statement_of(uow, r.id)
        for r in uow.transactions.list_by_plan(plan.id)
    }
    assert months == {1: "2026-07", 2: "2026-08", 3: "2026-07", 4: "2026-10"}


def test_discount_from_a_monthly_rate_is_posted_as_a_refund_on_the_statement(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = plan_of(uow, card)
    preview = PreviewAnticipation(uow, FixedClock(TODAY)).execute(
        AnticipationCommand(plan.id, monthly_rate=Decimal("0.02"))
    )
    assert 0 < preview.discount_cents < preview.nominal_cents
    assert preview.final_cents == preview.nominal_cents - preview.discount_cents
    run(uow, plan.id, monthly_rate=Decimal("0.02"), discount_description="Desconto antecipação")
    refunds = [t for t in uow.transactions.items.values() if t.kind is TransactionKind.REFUND]
    (refund,) = refunds
    assert refund.amount_cents == preview.discount_cents
    assert refund.plan_id is None and refund.account_id == card.id
    assert statement_of(uow, refund.id) == "2026-07"
    on_statement = [
        t for t in uow.transactions.items.values() if t.statement_id == refund.statement_id
    ]
    assert -sum(t.amount_cents for t in on_statement) == 1_000 + preview.final_cents


def test_explicit_discount_amount(uow: MemoryUnitOfWork, card: Account) -> None:
    plan = plan_of(uow, card)
    result = run(uow, plan.id, discount_cents=250, discount_description="Desconto")
    assert (result.discount_cents, result.final_cents) == (250, 2_750)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"discount_cents": 3_000}, "INVALID_DISCOUNT"),
        ({"discount_cents": -1}, "INVALID_DISCOUNT"),
        ({"discount_cents": 100, "monthly_rate": Decimal("0.01")}, "DISCOUNT_RATE_OR_AMOUNT"),
        ({"monthly_rate": Decimal("-0.01")}, "INVALID_RATE"),
        ({"discount_cents": 100}, "EMPTY_DESCRIPTION"),  # a discount entry needs its text
        ({"installment_numbers": (1,)}, "INSTALLMENT_NOT_ANTICIPABLE"),  # already billed
        ({"installment_numbers": (9,)}, "INSTALLMENT_NOT_ANTICIPABLE"),
    ],
)
def test_invalid_requests_change_nothing(
    uow: MemoryUnitOfWork, card: Account, kwargs: dict[str, Any], code: str
) -> None:
    plan = plan_of(uow, card)
    before = dict(uow.transactions.items)
    with pytest.raises(DomainError) as exc:
        run(uow, plan.id, **kwargs)
    assert exc.value.code == code
    assert uow.transactions.items == before


def test_nothing_to_anticipate_when_every_installment_is_billed_or_paid(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    plan = plan_of(uow, card, installments=2, total=2_000)
    run(uow, plan.id)  # everything is on the open statement now
    with pytest.raises(DomainError) as exc:
        run(uow, plan.id)
    assert exc.value.code == "NOTHING_TO_ANTICIPATE"
    statement_id = uow.transactions.list_by_plan(plan.id)[0].statement_id
    assert statement_id
    PayStatement(uow, FixedClock(D(2026, 8, 1))).execute(
        PayStatementCommand(statement_id, checking.id, D(2026, 8, 1))
    )
    with pytest.raises(DomainError) as exc2:
        run(uow, plan.id, today=D(2026, 8, 1))
    assert exc2.value.code == "NOTHING_TO_ANTICIPATE"


def test_target_is_the_open_statement_of_today_not_the_closed_one(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = plan_of(uow, card)
    run(uow, plan.id, today=D(2026, 7, 26))  # 2026-07 closed on 07-25; 2026-08 is open
    rows = uow.transactions.list_by_plan(plan.id)
    # installment 1 stays on the closed July statement; 2-4 come into August (closes 08-25)
    assert [statement_of(uow, r.id) for r in rows] == ["2026-07", "2026-08", "2026-08", "2026-08"]
    assert all(r.posted_on == D(2026, 8, 24) for r in rows[1:])


def test_atomic_when_the_discount_entry_cannot_be_written(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = plan_of(uow, card)
    before = dict(uow.transactions.items)
    del uow.categories.items[uow.categories.get_by_slug("refund").id]  # type: ignore[union-attr]
    with pytest.raises(DomainError):
        run(uow, plan.id, discount_cents=100, discount_description="Desconto")
    assert uow.transactions.items == before  # the moved installments rolled back too
