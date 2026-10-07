"""Installment plans as ONE purchase, for the entries list ("Lançamentos").

On ``/entries`` an installment purchase is a single row anchored on the **purchase date**: the
plan's ``purchased_on`` or, when it is not known (a purchase already running), the date of the
earliest installment on file. Its figures:

* ``total_cents``: what the installments on file add up to (refunded installments count
  nowhere, unless all of them are refunded);
* ``installment_cents``: the amount most installments have (the first one absorbs the
  remainder of the split, so it can differ by a few cents, 9.4); the larger one wins a tie;
* items (``PlanItemLine``): each item of the installments summed over the plan, with how it falls
  on the installments (``breakdown``: runs of equal amounts, e.g. 2 x 116,67 then 1 x 116,66).

The card screen (``/cards``), the statements and every projection keep reading the individual
installments: this view only changes how the entries list shows them.
"""

import datetime as dt
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from financas.domain.models import InstallmentPlan, Transaction, TransactionSplit
from financas.domain.ports import UnitOfWork
from financas.domain.services.splits import split_ids


@dataclass(frozen=True)
class PlanItemLine:
    description: str
    category_id: str
    total_cents: int  # positive
    breakdown: tuple[tuple[int, int], ...]  # (installments, cents each), in installment order


@dataclass(frozen=True)
class PlanPurchase:
    plan: InstallmentPlan
    anchor: Transaction  # the earliest installment on file: what the row's buttons act on
    purchased_on: dt.date
    entries: tuple[Transaction, ...]  # the installments on file, in order (refunded included)
    total_cents: int  # positive
    count: int  # installments on file
    installment_cents: int  # positive
    uniform: bool  # every installment has the same amount
    is_refunded: bool  # every installment is refunded
    items: tuple[PlanItemLine, ...]
    category_ids: frozenset[str]  # categories of the items, or of the installments

    def as_entry(self) -> Transaction:
        """The purchase as a list entry: the anchor's id, on the purchase date, for the total.

        An itemized purchase has no category of its own (its items carry them, 9.10).
        """
        return replace(
            self.anchor,
            posted_on=self.purchased_on,
            amount_cents=-self.total_cents,
            category_id=None if self.items else self.anchor.category_id,
            is_refunded=self.is_refunded,
        )

    @property
    def is_partial(self) -> bool:
        """A running purchase: only some of the plan's installments are on file."""
        return self.count != self.plan.installment_total


def _runs(amounts: Sequence[int]) -> tuple[tuple[int, int], ...]:
    runs: list[tuple[int, int]] = []
    for cents in amounts:
        if runs and runs[-1][1] == cents:
            runs[-1] = (runs[-1][0] + 1, cents)
        else:
            runs.append((1, cents))
    return tuple(runs)


def consolidate(
    plan: InstallmentPlan,
    entries: Sequence[Transaction],
    splits: Mapping[str, Sequence[TransactionSplit]],
) -> PlanPurchase:
    """One purchase out of the installments of ``plan`` (``entries`` must not be empty)."""
    ordered = sorted(entries, key=lambda e: (e.installment_number or 0, e.posted_on))
    live = [e for e in ordered if not e.is_refunded]
    counted = live or ordered
    amounts = [-e.amount_cents for e in counted]
    frequency = Counter(amounts)
    installment_cents = max(frequency, key=lambda cents: (frequency[cents], cents))
    anchor = ordered[0]
    purchased_on = plan.purchased_on or min(e.posted_on for e in ordered)

    # items summed by (description, category), in order of first appearance
    columns: dict[tuple[str, str], list[int]] = {}
    for position, entry in enumerate(counted):
        for item in splits.get(entry.id, ()):
            column = columns.setdefault((item.description, item.category_id), [0] * len(counted))
            column[position] += item.amount_cents
    items = tuple(
        PlanItemLine(
            description,
            category_id,
            sum(column),
            _runs([cents for cents in column if cents > 0]),
        )
        for (description, category_id), column in columns.items()
    )
    categories = {c for _, c in columns} or {
        e.category_id for e in ordered if e.category_id is not None
    }
    return PlanPurchase(
        plan=plan,
        anchor=anchor,
        purchased_on=purchased_on,
        entries=tuple(ordered),
        total_cents=sum(amounts),
        count=len(ordered),
        installment_cents=installment_cents,
        uniform=len(frequency) == 1,
        is_refunded=not live,
        items=items,
        category_ids=frozenset(categories),
    )


class ListPlanPurchases:
    """Every installment plan with entries, as one purchase each (see the module docstring)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self) -> list[PlanPurchase]:
        with self._uow as uow:
            plans = {p.id: p for p in uow.plans.list_all()}
            everything = uow.transactions.list_between(
                dt.date.min, dt.date.max, include_refunded=True
            )
            grouped: dict[str, list[Transaction]] = {}
            for entry in everything:
                if entry.plan_id is not None and entry.plan_id in plans:
                    grouped.setdefault(entry.plan_id, []).append(entry)
            splits = uow.transactions.splits_for(
                split_ids(e for rows in grouped.values() for e in rows)
            )
        return [consolidate(plans[pid], rows, splits) for pid, rows in grouped.items()]


class GetPlanPurchase:
    """One plan as a purchase (the row that "Cancelar" and the edit form swap back in)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, plan_id: str) -> PlanPurchase | None:
        with self._uow as uow:
            plan = uow.plans.get(plan_id)
            if plan is None:
                return None
            rows = uow.transactions.list_by_plan(plan_id, include_refunded=True)
            if not rows:
                return None
            splits = uow.transactions.splits_for(split_ids(rows))
        return consolidate(plan, rows, splits)
