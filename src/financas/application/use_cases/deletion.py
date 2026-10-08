"""The deletion rules, written once for the single "Apagar" buttons and the batch delete.

Both take the open unit of work: a caller that deletes several things (``BatchDelete``) does it
inside ONE ``with`` block, so it is all or nothing.
"""

import datetime as dt
from dataclasses import dataclass

from financas.application.queries.cards import is_locked, statement_view
from financas.application.use_cases._common import found
from financas.domain.errors import DomainError
from financas.domain.models import Transaction, TransactionKind
from financas.domain.ports import Work


@dataclass(frozen=True)
class PlanDeletion:
    deleted: int
    kept: int  # installments on paid statements: history, never removed
    plan_removed: bool  # false when something was kept: the plan stays for the audit trail


def delete_entry(uow: Work, transaction: Transaction, today: dt.date) -> int:
    """Delete an entry (for a transfer, every leg goes together); returns how many were removed.

    Never an installment (it leaves with its plan) nor an entry of a paid statement.
    """
    if transaction.plan_id is not None:
        raise DomainError("USE_DELETE_PURCHASE")  # an installment leaves with its plan
    if transaction.statement_id and transaction.kind is not TransactionKind.TRANSFER:
        statement = found(uow.statements.get(transaction.statement_id), "statement")
        if is_locked(statement_view(uow, statement, today)):
            raise DomainError("STATEMENT_ALREADY_PAID")
    targets = (
        uow.transactions.list_by_transfer(transaction.transfer_id)
        if transaction.transfer_id
        else [transaction]
    )
    for target in targets:
        uow.transactions.delete(target.id)
    return len(targets)


def delete_plan_pending(uow: Work, plan_id: str, today: dt.date) -> PlanDeletion:
    """Remove the installments of a plan that are not on a paid statement (9.4).

    A paid statement is history: its installments stay, and so does the plan record. When
    nothing is paid the whole plan goes. Totals, statements and the limit are computed from the
    entries, so there is nothing else to recalculate.
    """
    found(uow.plans.get(plan_id), "plan")
    entries = uow.transactions.list_by_plan(plan_id, include_refunded=True)
    locked: dict[str, bool] = {}
    removable: list[Transaction] = []
    for entry in entries:
        key = entry.statement_id or ""
        if key not in locked:
            statement = found(uow.statements.get(key), "statement")
            locked[key] = is_locked(statement_view(uow, statement, today))
        if not locked[key]:
            removable.append(entry)
    if not removable:
        raise DomainError("NOTHING_TO_DELETE")
    for entry in removable:
        uow.transactions.delete(entry.id)
    kept = len(entries) - len(removable)
    if kept == 0:
        uow.plans.delete(plan_id)
    return PlanDeletion(len(removable), kept, plan_removed=kept == 0)
