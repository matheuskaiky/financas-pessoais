"""Filling in merchants: the one-off backfill and the one-card-at-a-time review ("Revisão Rápida").

Both only touch metadata (merchant, a cleaned description, a category): no amount, date or account
moves, so a paid statement does not block them.
"""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.use_cases._common import found
from financas.domain.errors import DomainError
from financas.domain.models import Transaction, TransactionKind
from financas.domain.ports import UnitOfWork
from financas.domain.rules import validate_category_kind
from financas.domain.services.merchants import (
    BackfillRow,
    ChangeReason,
    canonical_merchant,
    plan_backfill,
)
from financas.domain.services.text import normalize_search

BACKFILL_KINDS = frozenset({TransactionKind.EXPENSE, TransactionKind.REFUND})


@dataclass(frozen=True)
class BackfillResult:
    scanned: int  # expenses and refunds looked at
    changed: int
    by_reason: dict[ChangeReason, int]
    descriptions_cleaned: int  # entries that lost a " - Merchant" suffix
    dry_run: bool


class BackfillMerchants:
    """Give every existing expense and refund a canonical merchant when one can be told.

    Order of evidence (``domain/services/merchants.plan_backfill``): a trailing " - Merchant"
    (the description is cleaned), a known alias in the description, then what the user's own
    entries already say. A merchant the user typed stays, except an alias spelling
    ("mercadolivre"), which becomes its canonical name. ``dry_run`` writes nothing. One
    transaction: all of it or none of it.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, dry_run: bool = False) -> BackfillResult:
        with self._uow as uow:
            entries = uow.transactions.list_between(dt.date.min, dt.date.max, include_refunded=True)
            rows = [BackfillRow(t.id, t.kind, t.description, t.merchant) for t in entries]
            changes = plan_backfill(rows)
            by_id = {t.id: t for t in entries}
            if not dry_run:
                for change in changes:
                    entry = by_id[change.id]
                    description = change.description or entry.description
                    uow.transactions.update(
                        replace(
                            entry,
                            merchant=change.merchant,
                            description=description,
                            description_search=normalize_search(description),
                        )
                    )
                    if change.description is not None and entry.plan_id:
                        plan = uow.plans.get(entry.plan_id)
                        if plan is not None and plan.description == entry.description:
                            uow.plans.update(replace(plan, description=description))
                uow.commit()
            cleaned = sum(1 for c in changes if c.description is not None)
            counts: dict[ChangeReason, int] = {}
            for change in changes:
                counts[change.reason] = counts.get(change.reason, 0) + 1
            scanned = sum(1 for t in entries if t.kind in BACKFILL_KINDS)
        return BackfillResult(scanned, len(changes), counts, cleaned, dry_run)


@dataclass(frozen=True)
class EnrichCommand:
    """What the review card saves: a merchant and/or a category (``None`` leaves it as it is)."""

    transaction_id: str
    merchant: str | None = None
    category_id: str | None = None


class EnrichTransaction:
    """Save one review card. The merchant goes through the canonical normalizer ("mercadolivre"
    becomes "Mercado Livre"). On an installment the merchant and category go to every installment
    of the plan (they are the same purchase). Nothing to save is an error: use "Pular"."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: EnrichCommand) -> Transaction:
        typed = canonical_merchant(cmd.merchant) if cmd.merchant else None
        with self._uow as uow:
            entry = found(uow.transactions.get(cmd.transaction_id), "transaction")
            if entry.kind is not TransactionKind.EXPENSE:
                raise DomainError("REVIEW_ONLY_FOR_EXPENSES")
            category_id = entry.category_id
            if cmd.category_id:
                family = (
                    [
                        t.id
                        for t in uow.transactions.list_by_plan(entry.plan_id, include_refunded=True)
                    ]
                    if entry.plan_id
                    else [entry.id]
                )
                if uow.transactions.splits_for(family):  # the items carry the categories
                    raise DomainError("PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS")
                category = found(uow.categories.get(cmd.category_id), "category")
                validate_category_kind(entry.kind, category.kind, category.is_neutral)
                category_id = category.id
            merchant = typed or entry.merchant
            if merchant == entry.merchant and category_id == entry.category_id:
                raise DomainError("NOTHING_TO_SAVE")
            targets = (
                uow.transactions.list_by_plan(entry.plan_id, include_refunded=True)
                if entry.plan_id
                else [entry]
            )
            for target in targets:  # the category is only touched when one was chosen
                chosen = category_id if cmd.category_id else target.category_id
                uow.transactions.update(replace(target, merchant=merchant, category_id=chosen))
            if entry.plan_id and cmd.category_id and category_id is not None:
                plan = found(uow.plans.get(entry.plan_id), "plan")
                uow.plans.update(replace(plan, category_id=category_id))
            uow.commit()
            return replace(entry, merchant=merchant, category_id=category_id)
