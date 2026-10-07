"""Credit card use cases: settings, purchases, installments, statements (CLAUDE.md 9.3-9.5)."""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.queries.cards import (
    is_locked,
    payment_date_boundaries,
    statement_view,
)
from financas.application.use_cases._cards import (
    assignment_for,
    ensure_statement,
    require_card,
)
from financas.application.use_cases._common import UNSET, Unset, found, new_id
from financas.application.use_cases.transactions import SplitItem, checked_split_item
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    CategoryKind,
    InstallmentPlan,
    Statement,
    StatementStatus,
    Transaction,
    TransactionKind,
    TransactionSplit,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.rules import validate_card_settings
from financas.domain.services.card_cycle import (
    StatementAssignment,
    check_payment_date,
    is_frozen,
    last_day_in_statement,
    statement_dates,
)
from financas.domain.services.installments import MAX_INSTALLMENTS, build_schedule, split_total
from financas.domain.services.merchants import settle_description
from financas.domain.services.splits import distribute_items, validate_split_amounts
from financas.domain.services.text import clean_merchant, clean_text, normalize_search


class SetCardSettings:
    """The full desired settings of a card (``credit_limit_cents=None`` clears the limit).

    Statements that are not closed yet follow the new settings (unless the user edited their dates
    by hand, or the new closing date would already be in the past); closed ones are frozen (9.3).
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(
        self,
        account_id: str,
        closing_days_before_due: int,
        due_day: int,
        credit_limit_cents: int | None,
    ) -> Account:
        validate_card_settings(closing_days_before_due, due_day, credit_limit_cents)
        with self._uow as uow:
            card = found(uow.accounts.get(account_id), "account")
            if card.kind is not AccountKind.CREDIT_CARD:
                raise DomainError("CARD_REQUIRED")
            before = card
            card = replace(
                card,
                closing_days_before_due=closing_days_before_due,
                due_day=due_day,
                credit_limit_cents=credit_limit_cents,
            )
            uow.accounts.update(card)
            self._refresh_open_statements(uow, before, card)
            uow.commit()
        return card

    def _refresh_open_statements(self, uow: Work, before: Account, after: Account) -> None:
        assert before.closing_days_before_due is not None and before.due_day is not None
        assert after.closing_days_before_due is not None and after.due_day is not None
        today = self._clock.today()
        for statement in uow.statements.list_for_card(after.id):
            if is_frozen(statement.closing_date, today):
                continue
            recipe = statement_dates(
                statement.month, before.due_day, before.closing_days_before_due
            )
            if (statement.closing_date, statement.due_date) != recipe:
                continue  # edited by hand: the user's dates stay
            closing, due = statement_dates(
                statement.month, after.due_day, after.closing_days_before_due
            )
            if is_frozen(closing, today):
                continue  # it would already be closed: keep what was in force
            uow.statements.update(replace(statement, closing_date=closing, due_date=due))


# --- purchases --------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardPurchaseCommand:
    """One card purchase. Give the total or the installment value, never both.

    ``statement_month`` is the statement of installment ``current_installment`` (of the first one
    for a new purchase); without it the purchase date decides. A purchase already running needs it.
    """

    account_id: str
    description: str
    purchased_on: dt.date | None = None
    category_id: str | None = None
    installments: int = 1
    current_installment: int = 1
    total_cents: int | None = None
    installment_cents: int | None = None
    statement_month: YearMonth | None = None
    is_recurring: bool = False
    notes: str | None = None
    merchant: str | None = None
    splits: tuple[SplitItem, ...] = ()  # itemize a single payment: the entry then has no category


@dataclass(frozen=True)
class PreviewLine:
    number: int
    installment_total: int
    statement_month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    amount_cents: int
    posted_on: dt.date
    statement_exists: bool


@dataclass(frozen=True)
class PurchasePreview:
    assignment: StatementAssignment  # why the first generated installment is on that statement
    lines: list[PreviewLine]
    total_cents: int


@dataclass(frozen=True)
class PurchaseResult:
    preview: PurchasePreview
    transactions: list[Transaction]
    plan: InstallmentPlan | None


def _default_category_id(uow: Work, category_id: str | None) -> str:
    if category_id is None:
        return found(uow.categories.get_by_slug("uncategorized"), "category").id
    category = found(uow.categories.get(category_id), "category")
    if category.kind is not CategoryKind.EXPENSE:
        raise DomainError(
            "CATEGORY_KIND_MISMATCH", kind="expense", category_kind=category.kind.value
        )
    return category.id


def _build_preview(uow: Work, cmd: CardPurchaseCommand) -> tuple[Account, PurchasePreview]:
    card = require_card(uow, cmd.account_id)
    if not 1 <= cmd.installments <= MAX_INSTALLMENTS:
        raise DomainError("INVALID_INSTALLMENT_COUNT", count=cmd.installments)
    if not 1 <= cmd.current_installment <= cmd.installments:
        raise DomainError(
            "INSTALLMENT_OUT_OF_RANGE", number=cmd.current_installment, count=cmd.installments
        )
    if cmd.current_installment > 1 and cmd.statement_month is None:
        raise DomainError("STATEMENT_REQUIRED")
    assignment = assignment_for(uow, card, cmd.purchased_on, cmd.statement_month)
    schedule = build_schedule(
        count=cmd.installments,
        first_number=cmd.current_installment,
        first_statement=assignment.month,
        total_cents=cmd.total_cents,
        installment_cents=cmd.installment_cents,
    )
    assert card.closing_days_before_due is not None and card.due_day is not None
    lines: list[PreviewLine] = []
    for line in schedule:
        stored = uow.statements.get_by_card_month(card.id, line.statement_month)
        closing, due = (
            (stored.closing_date, stored.due_date)
            if stored
            else statement_dates(line.statement_month, card.due_day, card.closing_days_before_due)
        )
        # installment 1 is dated on the purchase; the others on the last day of their statement
        posted_on = (
            cmd.purchased_on
            if line.number == 1 and cmd.purchased_on
            else last_day_in_statement(closing)
        )
        lines.append(
            PreviewLine(
                line.number, cmd.installments, line.statement_month, closing, due,
                line.amount_cents, posted_on, stored is not None,
            )
        )  # fmt: skip
    return card, PurchasePreview(assignment, lines, sum(ln.amount_cents for ln in lines))


class PreviewCardPurchase:
    """The schedule and the statement explanation, shown before saving (nothing is written)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: CardPurchaseCommand) -> PurchasePreview:
        with self._uow as uow:
            return _build_preview(uow, cmd)[1]


def _schedule_amounts(cmd: CardPurchaseCommand) -> list[int]:
    """The amount of every installment 1..N of the whole purchase (also those a running purchase
    does not create), by the rules of ``domain/services/installments``."""
    if cmd.total_cents is not None:
        return split_total(cmd.total_cents, cmd.installments)
    assert cmd.installment_cents is not None
    return [cmd.installment_cents] * cmd.installments


class RegisterCardPurchase:
    """Single payment: one entry. Installments: one plan plus one entry per installment,
    every one of them created now, future ones included (9.4)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: CardPurchaseCommand) -> PurchaseResult:
        description = clean_text(cmd.description)
        if not description:
            raise DomainError("EMPTY_DESCRIPTION")
        description, merchant = settle_description(description, clean_merchant(cmd.merchant))
        if cmd.splits and cmd.category_id:  # the items carry the categories: the purchase has none
            raise DomainError("PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS")
        with self._uow as uow:
            card, preview = _build_preview(uow, cmd)
            category_id = None if cmd.splits else _default_category_id(uow, cmd.category_id)
            items = [
                (*checked_split_item(uow, TransactionKind.EXPENSE, item), abs(item.amount_cents))
                for item in cmd.splits
            ]
            # per installment, the cents of each item (installment number -> [(item, cents)])
            shares: dict[int, list[tuple[str, str, int]]] = {}
            plan_category = category_id
            if items:
                # the items are the totals of the whole purchase; each installment gets its share
                every = _schedule_amounts(cmd)
                validate_split_amounts(sum(every), [cents for _, _, cents in items])
                matrix = distribute_items(every, [cents for _, _, cents in items])
                for line in preview.lines:  # a running purchase only creates installments n..N
                    row = matrix[line.number - 1]
                    shares[line.number] = [
                        (text, category, cents)
                        for (text, category, _), cents in zip(items, row, strict=True)
                        if cents > 0
                    ]
                plan_category = max(items, key=lambda i: i[2])[1]  # for display: the biggest item's
            plan = None
            if cmd.installments > 1:
                assert plan_category is not None
                plan = InstallmentPlan(
                    new_id(),
                    card.id,
                    description,
                    plan_category,
                    cmd.installments,
                    cmd.purchased_on,
                )
                uow.plans.add(plan)
            entries: list[Transaction] = []
            for line in preview.lines:
                statement = ensure_statement(uow, card, line.statement_month)
                entries.append(
                    Transaction(
                        id=new_id(),
                        account_id=card.id,
                        posted_on=line.posted_on,
                        kind=TransactionKind.EXPENSE,
                        category_id=category_id,
                        amount_cents=-line.amount_cents,
                        description=description,
                        description_search=normalize_search(description),
                        is_recurring=cmd.is_recurring,
                        notes=clean_text(cmd.notes) if cmd.notes else None,
                        statement_id=statement.id,
                        plan_id=plan.id if plan else None,
                        installment_number=line.number if plan else None,
                        merchant=merchant,
                    )
                )
            uow.transactions.add_many(entries)
            for entry, line in zip(entries, preview.lines, strict=True):
                if shares:  # every installment is itemized: its items add up to its amount
                    uow.transactions.set_splits(
                        entry.id,
                        [
                            TransactionSplit(new_id(), entry.id, text, category, cents)
                            for text, category, cents in shares[line.number]
                        ],
                    )
            uow.commit()
        return PurchaseResult(preview, entries, plan)


class AdjustInstallment:
    """Change one entry's amount by hand; never on a statement that is already paid."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, transaction_id: str, amount_cents: int) -> None:
        if amount_cents <= 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        with self._uow as uow:
            entry = found(uow.transactions.get(transaction_id), "transaction")
            if entry.statement_id is None or entry.kind is not TransactionKind.EXPENSE:
                raise DomainError("NOT_A_CARD_PURCHASE")
            statement = found(uow.statements.get(entry.statement_id), "statement")
            if is_locked(statement_view(uow, statement, self._clock.today())):
                raise DomainError("STATEMENT_ALREADY_PAID")
            if uow.transactions.splits_for([transaction_id]):  # its items would stop adding up
                raise DomainError("SPLIT_AMOUNT_NEEDS_ITEMS")
            uow.transactions.update_amount(transaction_id, -amount_cents)
            uow.commit()


class DeletePurchase:
    """Remove an installment plan with all its entries, unless one is on a paid statement."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, plan_id: str) -> int:
        with self._uow as uow:
            found(uow.plans.get(plan_id), "plan")
            entries = uow.transactions.list_by_plan(plan_id, include_refunded=True)
            for entry in entries:
                statement = found(uow.statements.get(entry.statement_id or ""), "statement")
                if is_locked(statement_view(uow, statement, self._clock.today())):
                    raise DomainError("STATEMENT_ALREADY_PAID")
            for entry in entries:
                uow.transactions.delete(entry.id)
            uow.plans.delete(plan_id)
            uow.commit()
        return len(entries)


@dataclass(frozen=True)
class PlanDeletion:
    deleted: int
    kept: int  # installments on paid statements: history, never removed
    plan_removed: bool  # false when something was kept: the plan stays for the audit trail


class DeleteInstallmentPlan:
    """Remove the installments of a plan that are not on a paid statement (9.4).

    A paid statement is history: its installments stay, and so does the plan record. When
    nothing is paid the whole plan goes. Totals, statements and the limit are computed from the
    entries, so there is nothing else to recalculate. All or nothing.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, plan_id: str) -> PlanDeletion:
        with self._uow as uow:
            found(uow.plans.get(plan_id), "plan")
            entries = uow.transactions.list_by_plan(plan_id, include_refunded=True)
            locked: dict[str, bool] = {}
            removable: list[Transaction] = []
            for entry in entries:
                key = entry.statement_id or ""
                if key not in locked:
                    statement = found(uow.statements.get(key), "statement")
                    view = statement_view(uow, statement, self._clock.today())
                    locked[key] = is_locked(view)
                if not locked[key]:
                    removable.append(entry)
            if not removable:
                raise DomainError("NOTHING_TO_DELETE")
            for entry in removable:
                uow.transactions.delete(entry.id)
            kept = len(entries) - len(removable)
            if kept == 0:
                uow.plans.delete(plan_id)
            uow.commit()
        return PlanDeletion(len(removable), kept, plan_removed=kept == 0)


# --- statements -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PayStatementCommand:
    statement_id: str
    from_account_id: str | None  # ``None``: paid from an account the system does not track
    paid_on: dt.date
    amount_cents: int | None = None  # default: what is still outstanding
    description: str = ""  # supplied by the adapter (a pt-BR text), stored as data


class PayStatement:
    """A transfer from a checking account to the card, linked to the statement (9.5).

    Partial and multiple payments are allowed. Both legs are created atomically.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: PayStatementCommand) -> list[Transaction]:
        with self._uow as uow:
            statement = found(uow.statements.get(cmd.statement_id), "statement")
            card = require_card(uow, statement.account_id)
            origin = (
                found(uow.accounts.get(cmd.from_account_id), "account")
                if cmd.from_account_id is not None
                else None
            )
            if origin is not None:
                if origin.kind is not AccountKind.CHECKING:
                    raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=origin.kind.value)
                if not origin.is_active:
                    raise DomainError("ACCOUNT_INACTIVE")
            today = self._clock.today()
            bounds = payment_date_boundaries(uow, statement, today)
            check_payment_date(cmd.paid_on, bounds.min_date, bounds.max_date)
            outstanding = statement_view(uow, statement, today).outstanding_cents
            amount = cmd.amount_cents if cmd.amount_cents is not None else outstanding
            if amount <= 0:
                raise DomainError(
                    "NOTHING_TO_PAY" if cmd.amount_cents is None else "AMOUNT_NOT_POSITIVE"
                )
            category = found(uow.categories.get_by_slug("transfer"), "category")
            description = clean_text(cmd.description)
            transfer_id = new_id()
            legs = [
                Transaction(
                    id=new_id(),
                    account_id=account_id,
                    posted_on=cmd.paid_on,
                    kind=TransactionKind.TRANSFER,
                    category_id=category.id,
                    amount_cents=signed,
                    description=description,
                    description_search=normalize_search(description),
                    transfer_id=transfer_id,
                    statement_id=statement_id,
                )
                for account_id, signed, statement_id in (
                    (origin.id if origin else None, -amount, None),
                    (card.id, amount, statement.id),
                )
                if account_id is not None
            ]
            uow.transactions.add_many(legs)
            uow.commit()
        return legs


@dataclass(frozen=True)
class UpdatePaymentCommand:
    """The full desired state of a statement payment, named by either of its legs.

    ``from_account_id`` ``UNSET`` keeps the origin, ``None`` makes it an untracked account (the
    checking leg goes away), an id moves the debit to that checking account.
    """

    transaction_id: str
    paid_on: dt.date
    amount_cents: int  # positive
    from_account_id: Unset | str | None = UNSET
    notes: str | None = None


@dataclass(frozen=True)
class PaymentUpdate:
    legs: list[Transaction]
    statement_id: str
    status_before: StatementStatus
    status_after: StatementStatus  # computed again with the new payment (never stored)


class UpdateInvoicePayment:
    """Change a statement payment: amount, date, source account and notes (9.5).

    Both legs move together and stay opposite and equal. The statement's paid amount, its status
    (a smaller payment can take it from paid back to closed), the card limit and the checking
    balances are all computed from the entries, so they follow at once. All or nothing.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: UpdatePaymentCommand) -> PaymentUpdate:
        if cmd.amount_cents <= 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        today = self._clock.today()
        with self._uow as uow:
            tapped = found(uow.transactions.get(cmd.transaction_id), "transaction")
            if tapped.kind is not TransactionKind.TRANSFER or tapped.transfer_id is None:
                raise DomainError("NOT_A_STATEMENT_PAYMENT")
            legs = uow.transactions.list_by_transfer(tapped.transfer_id)
            card_leg = next((leg for leg in legs if leg.statement_id), None)
            if card_leg is None:
                raise DomainError("NOT_A_STATEMENT_PAYMENT")
            origin_leg = next((leg for leg in legs if leg.id != card_leg.id), None)
            statement = found(uow.statements.get(card_leg.statement_id or ""), "statement")
            bounds = payment_date_boundaries(uow, statement, today)
            if cmd.paid_on != card_leg.posted_on:  # an unchanged date is never re-judged
                check_payment_date(cmd.paid_on, bounds.min_date, bounds.max_date)
            before = statement_view(uow, statement, today).status

            current = origin_leg.account_id if origin_leg else None
            wanted = current if isinstance(cmd.from_account_id, Unset) else cmd.from_account_id
            if wanted is not None:
                origin = found(uow.accounts.get(wanted), "account")
                if origin.kind is not AccountKind.CHECKING:
                    raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=origin.kind.value)
                if not origin.is_active and wanted != current:
                    raise DomainError("ACCOUNT_INACTIVE")
            notes = clean_text(cmd.notes) if cmd.notes else None

            updated = [
                replace(card_leg, amount_cents=cmd.amount_cents, posted_on=cmd.paid_on, notes=notes)
            ]
            uow.transactions.update(updated[0])
            if wanted is None:
                if origin_leg is not None:
                    uow.transactions.delete(origin_leg.id)
            elif origin_leg is not None:
                debit = replace(
                    origin_leg,
                    account_id=wanted,
                    amount_cents=-cmd.amount_cents,
                    posted_on=cmd.paid_on,
                    notes=notes,
                )
                uow.transactions.update(debit)
                updated.append(debit)
            else:  # it was paid from an untracked account and now has a tracked one
                debit = replace(
                    card_leg,
                    id=new_id(),
                    account_id=wanted,
                    amount_cents=-cmd.amount_cents,
                    statement_id=None,
                    notes=notes,
                    posted_on=cmd.paid_on,
                    transfer_id=card_leg.transfer_id,
                )
                uow.transactions.add_many([debit])
                updated.append(debit)
            after = statement_view(uow, statement, today).status
            uow.commit()
        return PaymentUpdate(updated, statement.id, before, after)


class InformStatementTotal:
    """The total the bank shows (``None`` clears it); the screen compares it with the entries."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, statement_id: str, informed_total_cents: int | None) -> Statement:
        if informed_total_cents is not None and informed_total_cents < 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        with self._uow as uow:
            statement = replace(
                found(uow.statements.get(statement_id), "statement"),
                informed_total_cents=informed_total_cents,
            )
            uow.statements.update(statement)
            uow.commit()
        return statement


class PostStatementDifference:
    """Post the 'undetailed' part of the bank's total as one ``uncategorized`` expense (9.5)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, statement_id: str, description: str = "") -> Transaction:
        with self._uow as uow:
            statement = found(uow.statements.get(statement_id), "statement")
            if statement.informed_total_cents is None:
                raise DomainError("TOTAL_NOT_INFORMED")
            entries = uow.transactions.list_by_statement(statement_id)
            entered = -sum(
                t.amount_cents
                for t in entries
                if t.kind in (TransactionKind.EXPENSE, TransactionKind.REFUND)
            )
            difference = statement.informed_total_cents - entered
            if difference <= 0:
                raise DomainError("DIFFERENCE_NOT_POSITIVE")
            category = found(uow.categories.get_by_slug("uncategorized"), "category")
            text = clean_text(description)
            entry = Transaction(
                id=new_id(),
                account_id=statement.account_id,
                posted_on=last_day_in_statement(statement.closing_date),
                kind=TransactionKind.EXPENSE,
                category_id=category.id,
                amount_cents=-difference,
                description=text,
                description_search=normalize_search(text),
                statement_id=statement.id,
            )
            uow.transactions.add_many([entry])
            uow.commit()
        return entry


class SetStatementDates:
    """Closing and due dates can be edited by the user (9.3); no business-day adjustment."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, statement_id: str, closing: dt.date, due: dt.date) -> Statement:
        if due <= closing:
            raise DomainError("INVALID_STATEMENT_DATES")
        with self._uow as uow:
            statement = replace(
                found(uow.statements.get(statement_id), "statement"),
                closing_date=closing,
                due_date=due,
            )
            uow.statements.update(statement)
            uow.commit()
        return statement


class MoveEntryToStatement:
    """Override the statement of one card entry (the issuer behaved differently, 9.3)."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, transaction_id: str, month: YearMonth) -> None:
        with self._uow as uow:
            entry = found(uow.transactions.get(transaction_id), "transaction")
            card = require_card(uow, entry.account_id)
            if entry.kind is TransactionKind.TRANSFER:
                raise DomainError("NOT_A_CARD_PURCHASE")
            if entry.statement_id is not None:
                source = found(uow.statements.get(entry.statement_id), "statement")
                if is_locked(statement_view(uow, source, self._clock.today())):
                    raise DomainError("STATEMENT_ALREADY_PAID")
            statement = ensure_statement(uow, card, month)
            if is_locked(statement_view(uow, statement, self._clock.today())):
                raise DomainError("STATEMENT_ALREADY_PAID")
            uow.transactions.set_statement(transaction_id, statement.id)
            uow.commit()
