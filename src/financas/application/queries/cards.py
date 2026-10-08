"""Cards: statements, limit, active installments and schedules (CLAUDE.md 9.4, 9.5, 10).

Definitions (each one has a test):
- Statement total = purchases and charges on it minus refunds; paid = payments linked to it;
  outstanding = total - paid. Status comes from the date (future/open/closed) and the payments.
- Committed limit = everything on the card not yet paid, future installments included
  (open decision 5): the unpaid part of every statement, each counted at 0 or more, so a credit on
  one statement never hides a debt on another (``committed_cents`` in domain/services/statements).
- Future installments = total of the statements that are not open yet.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    InstallmentPlan,
    Statement,
    StatementStatus,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.card_cycle import assign_statement, closing_date, statement_dates
from financas.domain.services.countdown import Countdown, countdown
from financas.domain.services.statements import (
    LimitUsage,
    Reconciliation,
    committed_cents,
    limit_usage,
    reconcile,
    statement_amounts,
    statement_status,
)


@dataclass(frozen=True)
class StatementView:
    statement: Statement
    status: StatementStatus
    total_cents: int
    paid_cents: int
    outstanding_cents: int
    entry_count: int
    reconciliation: Reconciliation
    days_to_due: int  # negative once overdue


@dataclass(frozen=True)
class CardTelemetry:
    """What the card face shows: the open statement's running total and the day counts.

    The *open* statement is the one that receives a purchase made today. A card that has none yet
    (no purchase in this cycle) gets the dates its settings give and a balance of zero. On the
    closing day itself the statement is already closed (9.3), so the open one is the next.
    """

    open_balance_cents: int  # purchases and charges minus refunds on the open statement
    closing: Countdown  # of the open statement
    due: Countdown  # of the open statement
    pending_month: YearMonth | None = None  # oldest closed statement still owing money...
    pending_due: Countdown | None = None  # ...and how far its due date is


@dataclass(frozen=True)
class CardView:
    account: Account
    usage: LimitUsage
    statements: list[StatementView]  # oldest month first
    telemetry: CardTelemetry | None = None

    @property
    def open_statement(self) -> StatementView | None:
        return next((s for s in self.statements if s.status is StatementStatus.OPEN), None)


@dataclass(frozen=True)
class CardsOverview:
    cards: list[CardView]
    closed_to_pay_cents: int
    open_total_cents: int
    future_installments_cents: int
    committed_cents: int
    available_cents: int | None  # only cards that have a limit; None when none has one


@dataclass(frozen=True)
class StatementDetail:
    card: Account
    view: StatementView
    entries: list[Transaction]


@dataclass(frozen=True)
class PlanView:
    plan: InstallmentPlan
    paid_count: int
    remaining_count: int
    to_pay_cents: int
    next_statement_month: YearMonth | None
    future_count: int = 0  # installments on statements that are not open yet (can be anticipated)


@dataclass(frozen=True)
class ScheduleRow:
    account_id: str
    month: YearMonth
    status: StatementStatus
    amount_cents: int  # installments only (plan entries), not other purchases


def card_committed(uow: Work, card_id: str, views: list[StatementView]) -> int:
    """The card's committed limit (see :func:`committed_cents`): unpaid part of each statement."""
    unlinked = -sum(
        t.amount_cents for t in uow.transactions.list_by_account(card_id) if t.statement_id is None
    )
    return committed_cents((v.outstanding_cents for v in views), unlinked)


def is_locked(view: StatementView) -> bool:
    """A paid statement is history: its entries are never rewritten (9.4).

    An empty closed statement counts as paid for the status but holds nothing to protect.
    """
    return view.status is StatementStatus.PAID and (view.total_cents != 0 or view.paid_cents != 0)


@dataclass(frozen=True)
class PaymentBounds:
    """The dates a payment of a statement may have (9.5)."""

    min_date: dt.date
    max_date: dt.date  # today: payments are never dated in the future
    from_previous_due: bool  # False: the statement is the card's first, ``min_date`` is its opening


def payment_date_boundaries(
    uow: Work, statement: Statement, today: dt.date | None = None
) -> PaymentBounds:
    """``min_date`` is the due date of the card's previous statement (the closest one before this
    one); a first statement has none, so it starts when its cycle opens (the closing date of the
    month before, from the card's settings). ``max_date`` is today."""
    card = uow.accounts.get(statement.account_id)
    if card is None:
        raise DomainError("NOT_FOUND", entity="account")
    previous = max(
        (s for s in uow.statements.list_for_card(card.id) if s.month < statement.month),
        key=lambda s: s.month,
        default=None,
    )
    if previous is not None:
        return PaymentBounds(previous.due_date, today or dt.date.today(), True)
    assert card.due_day is not None and card.closing_days_before_due is not None
    opening, _ = statement_dates(
        statement.month.add_months(-1), card.due_day, card.closing_days_before_due
    )
    return PaymentBounds(opening, today or dt.date.today(), False)


def card_accounts(uow: Work) -> list[Account]:
    return [a for a in uow.accounts.list_all() if a.kind is AccountKind.CREDIT_CARD]


def make_statement_view(
    card: Account, statement: Statement, entries: list[Transaction], today: dt.date,
    previous_closing: dt.date,
) -> StatementView:  # fmt: skip
    amounts = statement_amounts((t.kind, t.amount_cents) for t in entries)
    status = statement_status(
        today, statement.closing_date, previous_closing, amounts.total_cents, amounts.paid_cents
    )
    return StatementView(
        statement,
        status,
        amounts.total_cents,
        amounts.paid_cents,
        amounts.outstanding_cents,
        sum(1 for t in entries if t.kind is not TransactionKind.TRANSFER),
        reconcile(amounts.total_cents, statement.informed_total_cents),
        (statement.due_date - today).days,
    )


def _previous_closing(
    card: Account, statement: Statement, by_month: dict[YearMonth, Statement]
) -> dt.date:
    previous = statement.month.add_months(-1)
    if previous in by_month:
        return by_month[previous].closing_date
    assert card.closing_days_before_due is not None and card.due_day is not None
    return closing_date(previous, card.due_day, card.closing_days_before_due)


def statement_views(
    uow: Work,
    card: Account,
    today: dt.date,
    counts: Callable[[Transaction], bool] | None = None,
) -> list[StatementView]:
    """Views of the card's statements as of ``today``.

    ``counts`` (optional) keeps only the entries that already existed on a past ``today``
    (used by the net-worth series); without it every entry counts.
    """
    statements = uow.statements.list_for_card(card.id)
    by_month = {s.month: s for s in statements}
    views: list[StatementView] = []
    for s in statements:
        entries = uow.transactions.list_by_statement(s.id)
        if counts is not None:
            entries = [t for t in entries if counts(t)]
        views.append(
            make_statement_view(card, s, entries, today, _previous_closing(card, s, by_month))
        )
    return views


def statement_view(uow: Work, statement: Statement, today: dt.date) -> StatementView:
    card = uow.accounts.get(statement.account_id)
    assert card is not None
    by_month = {s.month: s for s in uow.statements.list_for_card(card.id)}
    return make_statement_view(
        card,
        statement,
        uow.transactions.list_by_statement(statement.id),
        today,
        _previous_closing(card, statement, by_month),
    )


def card_telemetry(card: Account, statements: list[StatementView], today: dt.date) -> CardTelemetry:
    open_view = next((s for s in statements if s.status is StatementStatus.OPEN), None)
    if open_view is not None:
        closing, due = open_view.statement.closing_date, open_view.statement.due_date
    else:
        assert card.due_day is not None and card.closing_days_before_due is not None
        known = {
            s.statement.month: (s.statement.closing_date, s.statement.due_date) for s in statements
        }
        assignment = assign_statement(today, card.due_day, card.closing_days_before_due, known)
        closing, due = assignment.closing_date, assignment.due_date
    owing = [
        s for s in statements if s.status is StatementStatus.CLOSED and s.outstanding_cents > 0
    ]
    pending = min(owing, key=lambda s: s.statement.due_date, default=None)
    return CardTelemetry(
        open_balance_cents=open_view.total_cents if open_view else 0,
        closing=countdown(closing, today),
        due=countdown(due, today),
        pending_month=pending.statement.month if pending else None,
        pending_due=countdown(pending.statement.due_date, today) if pending else None,
    )


class ListCards:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self) -> CardsOverview:
        today = self._clock.today()
        cards: list[CardView] = []
        with self._uow as uow:
            for card in card_accounts(uow):
                views = statement_views(uow, card, today)
                committed = card_committed(uow, card.id, views)
                cards.append(
                    CardView(
                        card,
                        limit_usage(committed, card.credit_limit_cents),
                        views,
                        card_telemetry(card, views, today),
                    )
                )
        every = [s for c in cards for s in c.statements]

        def total(status: StatementStatus, field: str) -> int:
            return sum(getattr(s, field) for s in every if s.status is status)

        with_limit = [c.usage.available_cents for c in cards if c.usage.available_cents is not None]
        return CardsOverview(
            cards,
            closed_to_pay_cents=total(StatementStatus.CLOSED, "outstanding_cents"),
            open_total_cents=total(StatementStatus.OPEN, "total_cents"),
            future_installments_cents=total(StatementStatus.FUTURE, "total_cents"),
            committed_cents=sum(c.usage.committed_cents for c in cards),
            available_cents=sum(with_limit) if with_limit else None,
        )


class GetStatementDetail:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, statement_id: str) -> StatementDetail:
        with self._uow as uow:
            statement = uow.statements.get(statement_id)
            if statement is None:
                raise DomainError("NOT_FOUND", entity="statement")
            card = uow.accounts.get(statement.account_id)
            assert card is not None
            view = statement_view(uow, statement, self._clock.today())
            entries = uow.transactions.list_by_statement(statement_id, include_refunded=True)
        return StatementDetail(card, view, entries)


class ListActiveInstallments:
    """Plans that still have installments to pay."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, account_id: str | None = None) -> list[PlanView]:
        today = self._clock.today()
        result: list[PlanView] = []
        with self._uow as uow:
            statuses: dict[str, StatementStatus] = {}
            months: dict[str, YearMonth] = {}
            for card in card_accounts(uow):
                for view in statement_views(uow, card, today):
                    statuses[view.statement.id] = view.status
                    months[view.statement.id] = view.statement.month
            for plan in uow.plans.list_all():
                if account_id and plan.account_id != account_id:
                    continue
                rows = uow.transactions.list_by_plan(plan.id)
                unpaid = [
                    t
                    for t in rows
                    if statuses.get(t.statement_id or "") is not StatementStatus.PAID
                ]
                if not unpaid:
                    continue
                result.append(
                    PlanView(
                        plan,
                        paid_count=len(rows) - len(unpaid),
                        remaining_count=len(unpaid),
                        to_pay_cents=-sum(t.amount_cents for t in unpaid),
                        next_statement_month=min(
                            (months[t.statement_id] for t in unpaid if t.statement_id), default=None
                        ),
                        future_count=sum(
                            1
                            for t in unpaid
                            if statuses.get(t.statement_id or "") is StatementStatus.FUTURE
                        ),
                    )
                )
        return sorted(
            result,
            key=lambda p: (p.next_statement_month or YearMonth(9999, 12), p.plan.description),
        )


class InstallmentSchedule:
    """Month-by-month installments still to pay (statements that are not paid)."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, account_id: str | None = None) -> list[ScheduleRow]:
        today = self._clock.today()
        totals: dict[tuple[str, YearMonth], tuple[StatementStatus, int]] = {}
        with self._uow as uow:
            for card in card_accounts(uow):
                if account_id and card.id != account_id:
                    continue
                for view in statement_views(uow, card, today):
                    if view.status is StatementStatus.PAID:
                        continue
                    entries = uow.transactions.list_by_statement(view.statement.id)
                    amount = -sum(t.amount_cents for t in entries if t.plan_id is not None)
                    if amount:
                        totals[(card.id, view.statement.month)] = (view.status, amount)
        return [
            ScheduleRow(account, month, status, amount)
            for (account, month), (status, amount) in sorted(
                totals.items(), key=lambda i: (i[0][1], i[0][0])
            )
        ]


@dataclass(frozen=True)
class PurchaseView:
    description: str
    account_id: str
    purchased_on: dt.date
    installments: int
    total_cents: int


class ListMonthPurchases:
    """Purchases dated in the period, at full value including installments (section 10)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, start: dt.date, end: dt.date) -> list[PurchaseView]:
        result: list[PurchaseView] = []
        with self._uow as uow:
            card_ids = {c.id for c in card_accounts(uow)}
            for plan in uow.plans.list_all():
                if plan.purchased_on is not None and start <= plan.purchased_on <= end:
                    total = -sum(t.amount_cents for t in uow.transactions.list_by_plan(plan.id))
                    result.append(
                        PurchaseView(
                            plan.description,
                            plan.account_id,
                            plan.purchased_on,
                            plan.installment_total,
                            total,
                        )
                    )
            for t in uow.transactions.list_between(start, end):
                if (
                    t.account_id in card_ids
                    and t.plan_id is None
                    and t.kind is TransactionKind.EXPENSE
                ):
                    result.append(
                        PurchaseView(t.description, t.account_id, t.posted_on, 1, -t.amount_cents)
                    )
        return sorted(result, key=lambda p: (p.purchased_on, p.description), reverse=True)
