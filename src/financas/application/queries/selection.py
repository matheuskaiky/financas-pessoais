"""What Selection Mode may do with each listed row (merge, batch delete).

The server decides, the page only renders it (``data-sel-*``): the window of the current
calendar month, paid statements, installment plans, refunded and itemized entries. Codes only;
``interfaces/messages/selection.py`` writes the sentences.
"""

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from financas.application.queries.cards import is_locked, statement_view
from financas.application.queries.plan_purchases import PlanPurchase
from financas.domain.models import Transaction, TransactionKind, TransactionSplit
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.selection import MergeBlock, SelectionLock, month_lock


@dataclass(frozen=True)
class RowSelection:
    """One row of a list as Selection Mode sees it."""

    sel_id: str  # "entry:<id>" or "plan:<id>": what the server is sent
    key: str  # merge compatibility: an account id, or "<card id>:<statement id>"
    window_date: dt.date  # the date the window rule tests (a plan: its purchase date)
    lock: SelectionLock | None = None
    merge_block: MergeBlock | None = None
    plan_installments: int = 0  # installments on file, for the plan wording


def paid_statement_ids(uow: Work, statement_ids: Iterable[str], today: dt.date) -> set[str]:
    """The statements among ``statement_ids`` that are paid: history, never rewritten."""
    paid: set[str] = set()
    for statement_id in dict.fromkeys(statement_ids):
        statement = uow.statements.get(statement_id)
        if statement is not None and is_locked(statement_view(uow, statement, today)):
            paid.add(statement_id)
    return paid


def selection_key(entry: Transaction) -> str:
    """Entries merge only inside one account and, on a card, one statement."""
    if entry.statement_id:
        return f"{entry.account_id}:{entry.statement_id}"
    return entry.account_id


class ListRowSelections:
    """``execute(entries, purchases, splits)``: the :class:`RowSelection` of every row, by entry id.

    ``entries`` are the rows as shown (an installment purchase on ``/entries`` is its anchor
    installment); ``purchases`` maps a plan id to its consolidated purchase; ``splits`` are the
    items of the itemized rows.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(
        self,
        entries: Sequence[Transaction],
        purchases: Mapping[str, PlanPurchase],
        splits: Mapping[str, Sequence[TransactionSplit]],
    ) -> dict[str, RowSelection]:
        today = self._clock.today()
        statement_ids = {e.statement_id for e in entries if e.statement_id}
        for entry in entries:
            purchase = purchases.get(entry.plan_id or "")
            if purchase is not None:
                statement_ids.update(e.statement_id for e in purchase.entries if e.statement_id)
        with self._uow as uow:
            paid = paid_statement_ids(uow, statement_ids, today)
        result: dict[str, RowSelection] = {}
        for entry in entries:
            purchase = purchases.get(entry.plan_id or "")
            if purchase is not None:
                result[entry.id] = self._plan_row(entry, purchase, today, paid)
            else:
                result[entry.id] = self._entry_row(entry, splits, today, paid)
        return result

    @staticmethod
    def _plan_row(
        entry: Transaction, purchase: PlanPurchase, today: dt.date, paid: set[str]
    ) -> RowSelection:
        lock = month_lock(purchase.purchased_on, today)
        pending = [e for e in purchase.entries if e.statement_id not in paid]
        if lock is None and not pending:
            lock = SelectionLock.PLAN_ALL_PAID
        return RowSelection(
            sel_id=f"plan:{purchase.plan.id}",
            key=selection_key(entry),
            window_date=purchase.purchased_on,
            lock=lock,
            merge_block=MergeBlock.PLAN,
            plan_installments=purchase.count,
        )

    @staticmethod
    def _entry_row(
        entry: Transaction,
        splits: Mapping[str, Sequence[TransactionSplit]],
        today: dt.date,
        paid: set[str],
    ) -> RowSelection:
        lock = month_lock(entry.posted_on, today)
        is_transfer = entry.kind is TransactionKind.TRANSFER
        if lock is None and entry.statement_id in paid and not is_transfer:
            lock = SelectionLock.STATEMENT_PAID
        block: MergeBlock | None = None
        if entry.kind is not TransactionKind.EXPENSE:
            block = MergeBlock.NOT_EXPENSE
        elif entry.is_refunded:
            block = MergeBlock.REFUNDED
        elif splits.get(entry.id):
            block = MergeBlock.ITEMIZED
        return RowSelection(
            sel_id=f"entry:{entry.id}",
            key=selection_key(entry),
            window_date=entry.posted_on,
            lock=lock,
            merge_block=block,
        )
