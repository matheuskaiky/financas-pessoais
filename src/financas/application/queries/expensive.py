"""The largest single expenses of a period ("Compras Mais Caras" on ``/analises``).

Definitions (each one has a test):
- Only **expenses** count. Not counted: refunded purchases, transfers (between own accounts and
  card-statement payments: the purchases are already counted on their cards) and income.
- An installment purchase is ONE row at its **total**, on its purchase date (``purchased_on``, else
  its earliest installment): it can rank in a period where only one installment is due (a ranking
  of statement lines would be a different feature). An itemized expense counts once, at its full
  amount, and is labelled "by item": nothing is added up twice.
- Every other entry counts on its own date (``posted_on``), like the entries list.
- Order: absolute amount descending; ties go to the newer date, then to the higher id.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from financas.application.queries.plan_purchases import ListPlanPurchases
from financas.domain.models import Transaction, TransactionKind, TransactionSplit
from financas.domain.ports import UnitOfWork
from financas.domain.services.splits import split_ids

TOP_CHOICES = (5, 10)
DEFAULT_TOP = 5


def parse_top(text: str) -> int:
    """5 or 10; anything else (bad text, 7, 0) falls back to 5."""
    try:
        value = int(text)
    except ValueError:
        return DEFAULT_TOP
    return value if value in TOP_CHOICES else DEFAULT_TOP


@dataclass(frozen=True)
class ExpensiveRow:
    kind: str  # "entry" or "plan": ids of the two can collide, so the pair is the identity
    id: str  # the entry id, or the plan id
    anchor_id: str  # the entry the row links to (a plan: its earliest installment)
    description: str
    merchant: str | None
    posted_on: dt.date
    account_id: str
    category_id: str | None  # ``None`` when itemized (shown as "Por item")
    has_items: bool
    amount_cents: int  # positive magnitude
    installments: int | None  # a plan: installments on file (the "3x" badge)


def rank_expensive(
    entries: Sequence[Transaction],
    plans: Sequence[ExpensiveRow],
    splits: Mapping[str, Sequence[TransactionSplit]],
    top: int,
) -> list[ExpensiveRow]:
    rows = list(plans)
    for t in entries:
        if t.kind is not TransactionKind.EXPENSE or t.is_refunded or t.plan_id is not None:
            continue
        rows.append(
            ExpensiveRow(
                "entry",
                t.id,
                t.id,
                t.description,
                t.merchant,
                t.posted_on,
                t.account_id,
                t.category_id,
                bool(splits.get(t.id)),
                -t.amount_cents,
                None,
            )
        )
    # stable passes, least significant first: id, then date, then amount (all descending)
    rows.sort(key=lambda r: (r.kind, r.id), reverse=True)
    rows.sort(key=lambda r: r.posted_on, reverse=True)
    rows.sort(key=lambda r: r.amount_cents, reverse=True)
    return rows[:top]


class GetMostExpensive:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self, start: dt.date, end: dt.date, top: int = DEFAULT_TOP, account_id: str | None = None
    ) -> list[ExpensiveRow]:
        purchases = ListPlanPurchases(self._uow).execute()
        with self._uow as uow:
            entries = uow.transactions.list_between(start, end, account_id)
            splits = uow.transactions.splits_for(split_ids(entries))
        plan_rows = [
            ExpensiveRow(
                "plan",
                p.plan.id,
                p.anchor.id,
                p.plan.description,
                p.anchor.merchant,
                p.purchased_on,
                p.plan.account_id,
                None if p.items else p.anchor.category_id,
                bool(p.items),
                p.total_cents,
                p.count,
            )
            for p in purchases
            if not p.is_refunded
            and start <= p.purchased_on <= end
            and (account_id is None or p.plan.account_id == account_id)
        ]
        return rank_expensive(entries, plan_rows, splits, top)
