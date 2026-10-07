"""Anticipating the remaining installments of a card plan into the open statement (9.4).

The plan total and the installment numbers (``4/10``) stay as they are for the audit trail; only
each chosen installment's statement and date move to the card's open statement. An optional
discount (a monthly rate on the days gained, or the amount the issuer gave) is posted as one
``refund`` entry on the same statement, so the statement total is the discounted amount.
"""

import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal

from financas.application.queries.cards import statement_views
from financas.application.use_cases._cards import assignment_for, ensure_statement, require_card
from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import (
    InstallmentPlan,
    PaymentMethod,
    StatementStatus,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.card_cycle import last_day_in_statement
from financas.domain.services.present_value import present_value
from financas.domain.services.text import clean_text, normalize_search


@dataclass(frozen=True)
class AnticipationCommand:
    """``installment_numbers`` ``None`` means every installment that can still be anticipated.

    Give at most one of ``monthly_rate`` (the discount is computed from the days gained) and
    ``discount_cents`` (what the issuer actually gave). ``discount_description`` is the text of
    the discount entry, supplied by the adapter, and only needed when there is a discount.
    """

    plan_id: str
    installment_numbers: tuple[int, ...] | None = None
    monthly_rate: Decimal | None = None
    discount_cents: int | None = None
    discount_description: str = ""


@dataclass(frozen=True)
class AnticipationLine:
    entry: Transaction
    number: int
    from_month: YearMonth
    from_due_date: dt.date
    amount_cents: int  # positive
    selected: bool


@dataclass(frozen=True)
class AnticipationPreview:
    plan: InstallmentPlan
    target_month: YearMonth
    target_closing_date: dt.date
    target_due_date: dt.date
    lines: list[AnticipationLine]  # every installment that can be anticipated
    nominal_cents: int  # of the selected ones
    discount_cents: int
    final_cents: int

    @property
    def selected(self) -> list[AnticipationLine]:
        return [line for line in self.lines if line.selected]


def _preview(
    uow: Work, today: dt.date, cmd: AnticipationCommand
) -> tuple[AnticipationPreview, bool]:
    """The preview, and whether the target statement already exists (nothing is written)."""
    plan = found(uow.plans.get(cmd.plan_id), "plan")
    card = require_card(uow, plan.account_id)
    if cmd.monthly_rate is not None and cmd.discount_cents is not None:
        raise DomainError("DISCOUNT_RATE_OR_AMOUNT")
    assignment = assignment_for(uow, card, today, None)  # the statement receiving purchases today
    target = uow.statements.get_by_card_month(card.id, assignment.month)
    views = {v.statement.id: v for v in statement_views(uow, card, today)}
    if target is not None and views[target.id].status is not StatementStatus.OPEN:
        raise DomainError("NO_OPEN_STATEMENT")
    closing, due = (
        (target.closing_date, target.due_date)
        if target
        else (assignment.closing_date, assignment.due_date)
    )
    lines: list[AnticipationLine] = []
    wanted = set(cmd.installment_numbers) if cmd.installment_numbers is not None else None
    for entry in uow.transactions.list_by_plan(plan.id):
        view = views.get(entry.statement_id or "")
        if view is None or view.status is not StatementStatus.FUTURE:
            continue  # already billed, closed or paid: nothing to bring forward
        assert entry.installment_number is not None
        lines.append(
            AnticipationLine(
                entry,
                entry.installment_number,
                view.statement.month,
                view.statement.due_date,
                -entry.amount_cents,
                wanted is None or entry.installment_number in wanted,
            )
        )
    if not lines:
        raise DomainError("NOTHING_TO_ANTICIPATE")
    if wanted is not None:
        missing = sorted(wanted - {line.number for line in lines})
        if missing:
            raise DomainError("INSTALLMENT_NOT_ANTICIPABLE", number=missing[0])
    chosen = [line for line in lines if line.selected]
    if not chosen:
        raise DomainError("NOTHING_TO_ANTICIPATE")
    nominal = sum(line.amount_cents for line in chosen)
    if cmd.discount_cents is not None:
        discount = cmd.discount_cents
        if not 0 <= discount < nominal:
            raise DomainError("INVALID_DISCOUNT")
    elif cmd.monthly_rate:
        payments = [(max(0, (ln.from_due_date - due).days), ln.amount_cents) for ln in chosen]
        discount = nominal - present_value(payments, cmd.monthly_rate)
    else:
        discount = 0
    preview = AnticipationPreview(
        plan, assignment.month, closing, due, lines, nominal, discount, nominal - discount
    )
    return preview, target is not None


class PreviewAnticipation:
    """What anticipating would do: the candidates, the target statement and the amounts."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: AnticipationCommand) -> AnticipationPreview:
        with self._uow as uow:
            return _preview(uow, self._clock.today(), cmd)[0]


class AnticipateInstallments:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: AnticipationCommand) -> AnticipationPreview:
        today = self._clock.today()
        with self._uow as uow:
            preview, _ = _preview(uow, today, cmd)
            if preview.discount_cents and not clean_text(cmd.discount_description):
                raise DomainError("EMPTY_DESCRIPTION")
            card = require_card(uow, preview.plan.account_id)
            target = ensure_statement(uow, card, preview.target_month)
            for line in preview.selected:
                uow.transactions.update(
                    replace(
                        line.entry,
                        statement_id=target.id,
                        posted_on=last_day_in_statement(target.closing_date),
                    )
                )
            if preview.discount_cents:
                description = clean_text(cmd.discount_description)
                refund = found(uow.categories.get_by_slug("refund"), "category")
                uow.transactions.add_many(
                    [
                        Transaction(
                            id=new_id(),
                            account_id=card.id,
                            posted_on=today,
                            kind=TransactionKind.REFUND,
                            category_id=refund.id,
                            amount_cents=preview.discount_cents,
                            description=description,
                            description_search=normalize_search(description),
                            statement_id=target.id,
                            payment_method=PaymentMethod.CREDIT_CARD,
                        )
                    ]
                )
            uow.commit()
        return preview
