"""Entering, deleting and categorizing income, expenses, refunds and transfers."""

import datetime as dt
from dataclasses import dataclass

from financas.application.queries.cards import is_locked, statement_view
from financas.application.use_cases._cards import assignment_for, ensure_statement, require_card
from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    Category,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.rules import (
    validate_category_kind,
    validate_sign,
    validate_transfer_accounts,
)
from financas.domain.services.text import clean_text, normalize_search

# Phase 1: income, expense and refund are entered on checking accounts only.
# Cards (Phase 2) and investment accounts (Phase 3) get their own flows.
_ENTRY_ACCOUNT_KINDS = {AccountKind.CHECKING}
_TRANSFER_ACCOUNT_KINDS = {AccountKind.CHECKING, AccountKind.INVESTMENT}

_DEFAULT_CATEGORY_SLUG = {
    TransactionKind.EXPENSE: "uncategorized",
    TransactionKind.INCOME: "other_income",
    TransactionKind.REFUND: "refund",
    TransactionKind.TRANSFER: "transfer",
}


def _check_account(account: Account, allowed: set[AccountKind]) -> None:
    if not account.is_active:
        raise DomainError("ACCOUNT_INACTIVE")
    if account.kind not in allowed:
        raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=account.kind.value)


def _magnitude(amount_cents: int) -> int:
    if amount_cents <= 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")
    return amount_cents


@dataclass(frozen=True)
class RegisterTransactionCommand:
    """``amount_cents`` is the positive magnitude; the sign comes from ``kind``."""

    account_id: str
    posted_on: dt.date
    kind: TransactionKind
    amount_cents: int
    description: str
    category_id: str | None = None
    is_recurring: bool = False
    notes: str | None = None
    statement_month: YearMonth | None = None  # cards only; default comes from the card cycle


class RegisterTransaction:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RegisterTransactionCommand) -> Transaction:
        if cmd.kind is TransactionKind.TRANSFER:
            raise DomainError("USE_TRANSFER_FOR_TRANSFERS")
        magnitude = _magnitude(cmd.amount_cents)
        description = clean_text(cmd.description)
        if not description:
            raise DomainError("EMPTY_DESCRIPTION")
        amount = -magnitude if cmd.kind is TransactionKind.EXPENSE else magnitude
        validate_sign(cmd.kind, amount)
        with self._uow as uow:
            account = found(uow.accounts.get(cmd.account_id), "account")
            statement_id: str | None = None
            if account.kind is AccountKind.CREDIT_CARD:
                # Purchases, charges and refunds on a card go to a statement (9.3, 9.5).
                if cmd.kind is TransactionKind.INCOME:
                    raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=account.kind.value)
                card = require_card(uow, account.id)
                month = assignment_for(card, cmd.posted_on, cmd.statement_month).month
                statement_id = ensure_statement(uow, card, month).id
            else:
                if cmd.statement_month is not None:
                    raise DomainError("STATEMENT_ONLY_FOR_CARDS")
                _check_account(account, _ENTRY_ACCOUNT_KINDS)
            if cmd.category_id is None:
                category = found(
                    uow.categories.get_by_slug(_DEFAULT_CATEGORY_SLUG[cmd.kind]), "category"
                )
            else:
                category = found(uow.categories.get(cmd.category_id), "category")
            validate_category_kind(cmd.kind, category.kind)
            transaction = Transaction(
                id=new_id(),
                account_id=cmd.account_id,
                posted_on=cmd.posted_on,
                kind=cmd.kind,
                category_id=category.id,
                amount_cents=amount,
                description=description,
                description_search=normalize_search(description),
                is_recurring=cmd.is_recurring,
                notes=clean_text(cmd.notes) if cmd.notes else None,
                statement_id=statement_id,
            )
            uow.transactions.add_many([transaction])
            uow.commit()
        return transaction


@dataclass(frozen=True)
class RegisterTransferCommand:
    """Money moved from ``from_account_id`` to ``to_account_id``.

    Either side may be ``None`` when that account is not tracked: one leg is created.
    ``holding_id`` tags the leg that lands on (or leaves) an investment account (9.6).
    """

    from_account_id: str | None
    to_account_id: str | None
    posted_on: dt.date
    amount_cents: int
    description: str = ""
    notes: str | None = None
    holding_id: str | None = None


def build_transfer_legs(uow: UnitOfWork, cmd: RegisterTransferCommand) -> list[Transaction]:
    """Validate and build the legs inside an open unit of work (the caller adds and commits)."""
    magnitude = _magnitude(cmd.amount_cents)
    validate_transfer_accounts(cmd.from_account_id, cmd.to_account_id)
    description = clean_text(cmd.description)
    transfer_id = new_id()
    category = found(uow.categories.get_by_slug("transfer"), "category")
    holding_account_id = None
    if cmd.holding_id is not None:  # the holding is tagged on the leg of its own account only
        holding_account_id = found(uow.holdings.get(cmd.holding_id), "holding").account_id
    legs: list[Transaction] = []
    for account_id, amount in (
        (cmd.from_account_id, -magnitude),
        (cmd.to_account_id, magnitude),
    ):
        if account_id is None:
            continue
        account = found(uow.accounts.get(account_id), "account")
        _check_account(account, _TRANSFER_ACCOUNT_KINDS)
        tag = holding_account_id is not None and account.id == holding_account_id
        legs.append(
            Transaction(
                id=new_id(),
                account_id=account_id,
                posted_on=cmd.posted_on,
                kind=TransactionKind.TRANSFER,
                category_id=category.id,
                amount_cents=amount,
                description=description,
                description_search=normalize_search(description),
                transfer_id=transfer_id,
                notes=clean_text(cmd.notes) if cmd.notes else None,
                holding_id=cmd.holding_id if tag else None,
            )
        )
    return legs


class RegisterTransfer:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RegisterTransferCommand) -> list[Transaction]:
        with self._uow as uow:
            legs = build_transfer_legs(uow, cmd)
            uow.transactions.add_many(legs)
            uow.commit()
        return legs


class DeleteTransaction:
    """Deletes an entry; for a transfer, every leg goes together."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, transaction_id: str) -> int:
        with self._uow as uow:
            transaction = found(uow.transactions.get(transaction_id), "transaction")
            if transaction.plan_id is not None:
                raise DomainError("USE_DELETE_PURCHASE")  # an installment leaves with its plan
            if transaction.statement_id and transaction.kind is not TransactionKind.TRANSFER:
                statement = found(uow.statements.get(transaction.statement_id), "statement")
                if is_locked(statement_view(uow, statement, self._clock.today())):
                    raise DomainError("STATEMENT_ALREADY_PAID")
            targets = (
                uow.transactions.list_by_transfer(transaction.transfer_id)
                if transaction.transfer_id
                else [transaction]
            )
            for target in targets:
                uow.transactions.delete(target.id)
            uow.commit()
        return len(targets)


class SuggestCategory:
    """The last category used for the same normalized description and kind (9.2)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, description: str, kind: TransactionKind) -> Category | None:
        key = normalize_search(description)
        if not key:
            return None
        with self._uow as uow:
            category_id = uow.transactions.last_category_id(key, kind)
            return uow.categories.get(category_id) if category_id else None
