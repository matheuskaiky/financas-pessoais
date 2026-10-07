"""The review deck ("Revisão Rápida"): expenses still missing a merchant or a category.

Pending = an expense (not refunded, not a transfer) with no merchant, or still in the
"uncategorized" category. Cards come newest first; the ones the user skipped in this pass are
passed in and left out. Suggestions are only suggestions: the merchant comes from
``domain/services/merchants.infer_merchant`` (alias, then what the user's own entries say), the
category from the last one used with the same description, else the merchant's usual one.
"""

import datetime as dt
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass

from financas.domain.models import Transaction, TransactionKind
from financas.domain.ports import UnitOfWork
from financas.domain.services.merchants import build_lookup, infer_merchant
from financas.domain.services.text import normalize_search

UNCATEGORIZED_SLUG = "uncategorized"


@dataclass(frozen=True)
class ReviewCard:
    entry: Transaction
    suggested_merchant: str | None
    suggested_category_id: str | None  # the entry's own category when it already has one
    itemized: bool = False  # its items carry the categories: only the merchant can be filled in


@dataclass(frozen=True)
class ReviewDeck:
    card: ReviewCard | None  # None: nothing left to review
    pending: int  # every pending entry, skipped or not
    remaining: int  # pending ones not skipped in this pass


class CountPendingReview:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self) -> int:
        with self._uow as uow:
            uncategorized = uow.categories.get_by_slug(UNCATEGORIZED_SLUG)
            return uow.transactions.count_pending_review(
                uncategorized.id if uncategorized else None
            )


class GetReviewDeck:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, skipped: Collection[str] = ()) -> ReviewDeck:
        with self._uow as uow:
            uncategorized = uow.categories.get_by_slug(UNCATEGORIZED_SLUG)
            uncategorized_id = uncategorized.id if uncategorized else None
            entries = uow.transactions.list_between(dt.date.min, dt.date.max)
        pending = [
            t
            for t in entries
            if t.kind is TransactionKind.EXPENSE
            and t.transfer_id is None
            and (not t.merchant or t.category_id == uncategorized_id)
        ]
        remaining = [t for t in pending if t.id not in set(skipped)]
        if not remaining:
            return ReviewDeck(None, len(pending), 0)
        entry = remaining[0]  # newest first
        lookup = build_lookup((t.description, t.merchant) for t in entries if t.merchant)
        merchant = entry.merchant or infer_merchant(entry.description, lookup)
        with self._uow as uow:
            itemized = bool(uow.transactions.splits_for([entry.id]))
        return ReviewDeck(
            ReviewCard(
                entry,
                merchant,
                None if itemized else _suggest_category(entry, merchant, entries, uncategorized_id),
                itemized,
            ),
            len(pending),
            len(remaining),
        )


def _suggest_category(
    entry: Transaction,
    merchant: str | None,
    entries: list[Transaction],
    uncategorized_id: str | None,
) -> str | None:
    if entry.category_id != uncategorized_id:
        return entry.category_id  # already categorized: keep it selected
    key = normalize_search(entry.description)
    same_description = next(  # entries are newest first: the last category used with this text
        (
            t.category_id
            for t in entries
            if t.id != entry.id
            and t.category_id != uncategorized_id
            and normalize_search(t.description) == key
            and t.kind is entry.kind
        ),
        None,
    )
    if same_description:
        return same_description
    if merchant:
        wanted = normalize_search(merchant)
        usual = Counter(
            t.category_id
            for t in entries
            if t.merchant
            and normalize_search(t.merchant) == wanted
            and t.category_id != uncategorized_id
            and t.kind is TransactionKind.EXPENSE
        )
        if usual:
            return usual.most_common(1)[0][0]
    return None
