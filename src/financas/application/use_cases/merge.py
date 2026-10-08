"""Merging several expenses into one itemized entry ("Mesclar lançamentos").

The originals become the items of a new parent whose amount is their sum, so the statement, the
balance and the limit see exactly what they saw before (one entry, same total) while spending by
category still follows each original category. All or nothing.

Only plain expenses of the **current calendar month** merge (it fixes logging mistakes and split
receipts; earlier months are consolidated), and none that is already itemized.
"""

import datetime as dt
from dataclasses import dataclass

from financas.application.queries.cards import is_locked, statement_view
from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    PaymentMethod,
    StatementStatus,
    Transaction,
    TransactionKind,
    TransactionSplit,
)
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.services.selection import in_current_month
from financas.domain.services.splits import validate_split_amounts
from financas.domain.services.text import clean_text, normalize_search

MIN_ENTRIES = 2


@dataclass(frozen=True)
class MergeCommand:
    entry_ids: tuple[str, ...]
    description: str  # of the unified entry
    posted_on: dt.date  # of the unified entry
    acknowledge_closed: bool = False  # the user knows a chosen statement is already closed


def _shared_merchant(entries: list[Transaction]) -> str | None:
    """The merchant the merged entries have in common (same name ignoring case and accents)."""
    if not all(e.merchant for e in entries):
        return None  # one of them has none: no common merchant
    keys = {normalize_search(e.merchant or "") for e in entries}
    return entries[0].merchant if len(keys) == 1 else None


def _shared_method(entries: list[Transaction]) -> PaymentMethod | None:
    """The payment method the merged entries have in common (none when they differ)."""
    methods = {e.payment_method for e in entries}
    return methods.pop() if len(methods) == 1 else None


class MergeTransactions:
    """Rules: two or more distinct expenses of one account (and, on a card, of one statement),
    all dated in the current calendar month; none an installment, a transfer, a refunded purchase
    or an entry that already has items; none on a paid statement."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: MergeCommand) -> Transaction:
        description = clean_text(cmd.description)
        if not description:
            raise DomainError("EMPTY_DESCRIPTION")
        ids = list(dict.fromkeys(cmd.entry_ids))
        if len(ids) < MIN_ENTRIES:
            raise DomainError("MERGE_NEEDS_TWO")
        with self._uow as uow:
            entries = [found(uow.transactions.get(i), "transaction") for i in ids]
            for entry in entries:
                if (
                    entry.kind is not TransactionKind.EXPENSE
                    or entry.plan_id is not None
                    or entry.is_refunded
                ):
                    raise DomainError("MERGE_ONLY_PLAIN_EXPENSES")
            if uow.transactions.splits_for(ids):
                raise DomainError("MERGE_ITEMIZED_FORBIDDEN")
            today = self._clock.today()
            outside = [e.id for e in entries if not in_current_month(e.posted_on, today)]
            if outside:
                raise DomainError("MERGE_OUTSIDE_CURRENT_MONTH", ids=",".join(outside))
            if len({e.account_id for e in entries}) != 1:
                raise DomainError("MERGE_ACCOUNT_MISMATCH")
            account = found(uow.accounts.get(entries[0].account_id), "account")
            statement_id = entries[0].statement_id
            if account.kind is AccountKind.CREDIT_CARD:
                if len({e.statement_id for e in entries}) != 1 or statement_id is None:
                    raise DomainError("MERGE_STATEMENT_MISMATCH")
                statement = found(uow.statements.get(statement_id), "statement")
                view = statement_view(uow, statement, self._clock.today())
                if is_locked(view):
                    raise DomainError("STATEMENT_ALREADY_PAID")
                if view.status is StatementStatus.CLOSED and not cmd.acknowledge_closed:
                    raise DomainError("STATEMENT_CLOSED_NEEDS_ACK")

            parent_id = new_id()
            items = [
                TransactionSplit(
                    new_id(), parent_id, entry.description, entry.category_id, -entry.amount_cents
                )
                for entry in entries
                if entry.category_id is not None
            ]
            if len(items) != len(entries):  # an expense with no category cannot become an item
                raise DomainError("MERGE_ONLY_PLAIN_EXPENSES")
            total = sum(-e.amount_cents for e in entries)
            validate_split_amounts(total, [i.amount_cents for i in items])
            parent = Transaction(
                id=parent_id,
                account_id=account.id,
                posted_on=cmd.posted_on,
                kind=TransactionKind.EXPENSE,
                category_id=None,  # the items carry the categories (CLAUDE.md 9.10)
                amount_cents=-total,
                description=description,
                description_search=normalize_search(description),
                is_recurring=False,
                statement_id=statement_id,
                merchant=_shared_merchant(entries),
                payment_method=_shared_method(entries),
            )
            for entry in entries:
                uow.transactions.delete(entry.id)  # their items go with them
            uow.transactions.add_many([parent])
            uow.transactions.set_splits(parent_id, items)
            uow.commit()
        return parent
