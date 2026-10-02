"""Credit card use cases: settings, purchases, installments, statements (CLAUDE.md 9.3-9.5)."""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.queries.cards import is_locked, statement_view
from financas.application.use_cases._cards import (
    assignment_for,
    ensure_statement,
    require_card,
)
from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    CategoryKind,
    InstallmentPlan,
    Statement,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.rules import validate_card_settings
from financas.domain.services.card_cycle import StatementAssignment, statement_dates
from financas.domain.services.installments import MAX_INSTALLMENTS, build_schedule
from financas.domain.services.text import clean_text, normalize_search


class SetCardSettings:
    """The full desired settings of a card (``credit_limit_cents=None`` clears the limit).

    A new closing day affects only new entries: stored statements keep their dates (9.3).
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

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
            card = replace(
                card,
                closing_days_before_due=closing_days_before_due,
                due_day=due_day,
                credit_limit_cents=credit_limit_cents,
            )
            uow.accounts.update(card)
            uow.commit()
        return card


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


def _default_category_id(uow: UnitOfWork, category_id: str | None) -> str:
    if category_id is None:
        return found(uow.categories.get_by_slug("uncategorized"), "category").id
    category = found(uow.categories.get(category_id), "category")
    if category.kind is not CategoryKind.EXPENSE:
        raise DomainError(
            "CATEGORY_KIND_MISMATCH", kind="expense", category_kind=category.kind.value
        )
    return category.id


def _build_preview(uow: UnitOfWork, cmd: CardPurchaseCommand) -> tuple[Account, PurchasePreview]:
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
        # installment 1 is dated on the purchase; the others on their statement's closing date
        posted_on = cmd.purchased_on if line.number == 1 and cmd.purchased_on else closing
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


class RegisterCardPurchase:
    """Single payment: one entry. Installments: one plan plus one entry per installment,
    every one of them created now, future ones included (9.4)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: CardPurchaseCommand) -> PurchaseResult:
        description = clean_text(cmd.description)
        if not description:
            raise DomainError("EMPTY_DESCRIPTION")
        with self._uow as uow:
            card, preview = _build_preview(uow, cmd)
            category_id = _default_category_id(uow, cmd.category_id)
            plan = None
            if cmd.installments > 1:
                plan = InstallmentPlan(
                    new_id(), card.id, description, category_id, cmd.installments, cmd.purchased_on
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
                    )
                )
            uow.transactions.add_many(entries)
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
            entries = uow.transactions.list_by_plan(plan_id)
            for entry in entries:
                statement = found(uow.statements.get(entry.statement_id or ""), "statement")
                if is_locked(statement_view(uow, statement, self._clock.today())):
                    raise DomainError("STATEMENT_ALREADY_PAID")
            for entry in entries:
                uow.transactions.delete(entry.id)
            uow.plans.delete(plan_id)
            uow.commit()
        return len(entries)


# --- statements -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PayStatementCommand:
    statement_id: str
    from_account_id: str
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
            origin = found(uow.accounts.get(cmd.from_account_id), "account")
            if origin.kind is not AccountKind.CHECKING:
                raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=origin.kind.value)
            if not origin.is_active:
                raise DomainError("ACCOUNT_INACTIVE")
            outstanding = statement_view(uow, statement, self._clock.today()).outstanding_cents
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
                    (origin.id, -amount, None),
                    (card.id, amount, statement.id),
                )
            ]
            uow.transactions.add_many(legs)
            uow.commit()
        return legs


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
                posted_on=statement.closing_date,
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
