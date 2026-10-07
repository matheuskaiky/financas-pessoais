"""Changing the purchase date of an installment plan (9.4).

The statement of installment k is the statement of installment 1 plus (k - 1) months, so a new
purchase date moves every installment that can still move: the ones on an **open or future**
statement. Installments on a closed or paid statement are history and stay exactly as they are.
If installment 1 itself is on file but cannot move, the date is history too and is refused.
"""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.queries.cards import is_locked, statement_view
from financas.application.use_cases._cards import assignment_for, ensure_statement, require_card
from financas.application.use_cases._common import found
from financas.domain.errors import DomainError
from financas.domain.models import StatementStatus, Transaction
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.card_cycle import last_day_in_statement

_MOVABLE = {StatementStatus.OPEN, StatementStatus.FUTURE}


@dataclass(frozen=True)
class PlanReschedule:
    moved: int  # installments given a new statement
    kept: int  # installments left where they were (history)


def reschedule_plan(
    uow: Work, plan_id: str, purchased_on: dt.date, today: dt.date
) -> PlanReschedule:
    """Move the plan to ``purchased_on`` inside the caller's unit of work (nothing is committed)."""
    plan = found(uow.plans.get(plan_id), "plan")
    if plan.purchased_on == purchased_on:
        return PlanReschedule(0, 0)
    card = require_card(uow, plan.account_id)
    first_month = assignment_for(uow, card, purchased_on, None).month
    entries = sorted(
        uow.transactions.list_by_plan(plan_id, include_refunded=True),
        key=lambda e: e.installment_number or 0,
    )
    movable: list[Transaction] = []
    kept = 0
    for entry in entries:
        statement = found(uow.statements.get(entry.statement_id or ""), "statement")
        view = statement_view(uow, statement, today)
        if view.status in _MOVABLE and not is_locked(view):
            movable.append(entry)
        elif entry.installment_number == 1:
            raise DomainError("PURCHASE_DATE_LOCKED")
        else:
            kept += 1
    for entry in movable:
        assert entry.installment_number is not None
        target = ensure_statement(uow, card, first_month.add_months(entry.installment_number - 1))
        if statement_view(uow, target, today).status not in _MOVABLE:
            raise DomainError("PLAN_TARGET_STATEMENT_CLOSED", number=entry.installment_number)
        posted_on = (
            purchased_on
            if entry.installment_number == 1
            else last_day_in_statement(target.closing_date)
        )
        uow.transactions.update(replace(entry, statement_id=target.id, posted_on=posted_on))
    uow.plans.update(replace(plan, purchased_on=purchased_on))
    return PlanReschedule(len(movable), kept)


class ReschedulePlanPurchase:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, plan_id: str, purchased_on: dt.date) -> PlanReschedule:
        with self._uow as uow:
            result = reschedule_plan(uow, plan_id, purchased_on, self._clock.today())
            uow.commit()
        return result
