"""Deleting several entries and installment plans at once ("Apagar (N)" in Selection Mode).

Rules, in order: typed ids (``entry:<id>`` / ``plan:<id>``) at most ``MAX_SELECTION``; an id that
no longer exists is skipped and counted (a retry is harmless); every remaining row must belong to
the current calendar month (a plan counts by its purchase date); then each one goes through the
same deletion code as the single "Apagar" buttons, **inside one unit of work**: all or nothing.
Totals, balances and statements are computed on read, so there is nothing to recalculate.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

from financas.application.queries.cards import is_locked, statement_view
from financas.application.queries.plan_purchases import consolidate
from financas.application.use_cases.deletion import delete_entry, delete_plan_pending
from financas.domain.errors import DomainError
from financas.domain.models import InstallmentPlan, Transaction
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.selection import MAX_SELECTION, in_current_month

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class SelectedIds:
    entry_ids: tuple[str, ...]
    plan_ids: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.entry_ids) + len(self.plan_ids)


def parse_selection_ids(raw: Sequence[str]) -> SelectedIds:
    """``["entry:ab", "plan:cd"]`` -> typed ids, duplicates dropped, order kept."""
    entries: dict[str, None] = {}
    plans: dict[str, None] = {}
    for text in raw:
        prefix, _, identifier = text.partition(":")
        if prefix not in {"entry", "plan"} or not _ID.fullmatch(identifier):
            raise DomainError("BAD_SELECTION_ID")
        (entries if prefix == "entry" else plans)[identifier] = None
    if len(entries) + len(plans) > MAX_SELECTION:
        raise DomainError("TOO_MANY_IDS", max=MAX_SELECTION)
    return SelectedIds(tuple(entries), tuple(plans))


@dataclass(frozen=True)
class PlanNote:
    """What deleting a plan does, for the confirmation dialog."""

    plan_id: str
    description: str
    installments: int  # installments on file
    installment_cents: int  # the amount most installments have
    pending_count: int
    pending_cents: int
    paid_count: int


@dataclass(frozen=True)
class BatchDeletePreview:
    rows: int  # rows the user checked that still exist
    skipped: int
    plans: tuple[PlanNote, ...]


@dataclass(frozen=True)
class BatchDeleteResult:
    deleted: int  # rows deleted (a plan counts once)
    skipped: int  # ids that were already gone


@dataclass
class _Resolved:
    entries: list[Transaction]
    plans: dict[str, tuple[InstallmentPlan, list[Transaction]]]
    skipped: int


def _plan_date(plan: InstallmentPlan, entries: Sequence[Transaction]) -> dt.date | None:
    """The date the window tests for a plan: its purchase date, else its earliest installment."""
    if plan.purchased_on is not None:
        return plan.purchased_on
    return min((e.posted_on for e in entries), default=None)


def _resolve(uow: Work, selected: SelectedIds, today: dt.date) -> _Resolved:
    entries: list[Transaction] = []
    plans: dict[str, tuple[InstallmentPlan, list[Transaction]]] = {}
    skipped = 0
    outside: list[str] = []
    for entry_id in selected.entry_ids:
        entry = uow.transactions.get(entry_id)
        if entry is None:
            skipped += 1
        elif not in_current_month(entry.posted_on, today):
            outside.append(f"entry:{entry_id}")
        else:
            entries.append(entry)
    for plan_id in selected.plan_ids:
        plan = uow.plans.get(plan_id)
        if plan is None:
            skipped += 1
            continue
        installments = uow.transactions.list_by_plan(plan_id, include_refunded=True)
        day = _plan_date(plan, installments)
        if day is None or not in_current_month(day, today):
            outside.append(f"plan:{plan_id}")
        else:
            plans[plan_id] = (plan, installments)
    if outside:
        raise DomainError("DELETE_OUTSIDE_CURRENT_MONTH", ids=",".join(outside))
    return _Resolved(entries, plans, skipped)


def _note(
    uow: Work, plan: InstallmentPlan, installments: list[Transaction], today: dt.date
) -> PlanNote:
    paid_statements: dict[str, bool] = {}
    for entry in installments:
        key = entry.statement_id or ""
        if key not in paid_statements:
            statement = uow.statements.get(key)
            paid_statements[key] = statement is not None and is_locked(
                statement_view(uow, statement, today)
            )
    pending = [e for e in installments if not paid_statements[e.statement_id or ""]]
    purchase = consolidate(plan, installments, {}) if installments else None
    return PlanNote(
        plan_id=plan.id,
        description=plan.description,
        installments=len(installments),
        installment_cents=purchase.installment_cents if purchase else 0,
        pending_count=len(pending),
        pending_cents=sum(-e.amount_cents for e in pending),
        paid_count=len(installments) - len(pending),
    )


class PreviewBatchDelete:
    """The figures of the confirmation dialog. Changes nothing."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, selected: SelectedIds) -> BatchDeletePreview:
        today = self._clock.today()
        with self._uow as uow:
            resolved = _resolve(uow, selected, today)
            rows = len(resolved.entries) + len(resolved.plans)
            if rows == 0:
                raise DomainError("NOT_FOUND", entity="transaction")
            notes = tuple(_note(uow, p, e, today) for p, e in resolved.plans.values())
        return BatchDeletePreview(rows, resolved.skipped, notes)


class BatchDelete:
    """Delete every selected entry and plan in one unit of work."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, selected: SelectedIds) -> BatchDeleteResult:
        today = self._clock.today()
        with self._uow as uow:
            resolved = _resolve(uow, selected, today)
            deleted = 0
            skipped = resolved.skipped
            for entry in resolved.entries:
                # the other leg of a transfer deleted a moment ago is already gone
                current = uow.transactions.get(entry.id)
                if current is None:
                    skipped += 1
                    continue
                delete_entry(uow, current, today)
                deleted += 1
            for plan_id in resolved.plans:
                delete_plan_pending(uow, plan_id, today)
                deleted += 1
            uow.commit()
        return BatchDeleteResult(deleted, skipped)
