"""Merchants: autocomplete suggestions and the "Principais Estabelecimentos" ranking.

Definitions (each one has a test):
- A merchant is what the user typed in the entry's ``merchant`` field. Spellings that differ only
  in case or accents are one merchant (the key is :func:`normalize_search`); the name shown is the
  spelling used most often.
- The ranking counts **expenses** only, by competence (a card purchase in its statement month),
  leaving out refunded purchases and transfers. Entries with no merchant fall in one
  "unidentified" bucket, shown apart.
- ``total_spent_cents`` is the sum of the entries' amounts; ``percentage_of_total`` is that over
  all the period's expenses (identified or not); ``average_ticket_cents`` is the total over the
  number of entries, rounded half up to the cent.
- The main category of a merchant is the one its entries (or the items of an itemized entry) fall
  in most often; ties go to the larger amount.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from financas.application.queries.summary import Period
from financas.domain.models import Category, Transaction, TransactionKind, TransactionSplit
from financas.domain.ports import UnitOfWork
from financas.domain.services.splits import allocations, split_ids
from financas.domain.services.text import normalize_search

DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class MerchantRow:
    merchant: str | None  # ``None``: the entries that name no merchant
    total_spent_cents: int
    percentage_of_total: float  # of the period's expenses, 0 to 1
    transaction_count: int
    average_ticket_cents: int
    top_category_id: str | None
    top_category_name: str | None


@dataclass(frozen=True)
class MerchantsSummary:
    period: Period
    total_expenses_cents: int
    rows: list[MerchantRow]  # identified merchants, biggest first, at most ``limit``
    unidentified: MerchantRow | None
    merchant_count: int  # distinct identified merchants in the period, before the limit


def _display_name(spellings: Counter[str]) -> str:
    """The most used spelling; ties go to the alphabetically first."""
    return sorted(spellings.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _row(
    merchant: str | None,
    entries: Sequence[Transaction],
    total_expenses: int,
    categories: Mapping[str, Category],
    splits: Mapping[str, Sequence[TransactionSplit]],
) -> MerchantRow:
    total = sum(-t.amount_cents for t in entries)
    count = len(entries)
    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # id -> [entries, cents]
    for t in entries:
        for category_id, cents in allocations(t, splits):
            per_category[category_id][0] += 1
            per_category[category_id][1] += -cents
    top = max(per_category.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0]), default=None)
    top_id = top[0] if top else None
    return MerchantRow(
        merchant=merchant,
        total_spent_cents=total,
        percentage_of_total=total / total_expenses if total_expenses else 0.0,
        transaction_count=count,
        average_ticket_cents=(2 * total + count) // (2 * count) if count else 0,
        top_category_id=top_id,
        top_category_name=categories[top_id].name
        if top_id is not None and top_id in categories
        else None,
    )


def rank_merchants(
    transactions: Iterable[Transaction],
    categories: Mapping[str, Category],
    splits: Mapping[str, Sequence[TransactionSplit]],
    period: Period,
    limit: int = DEFAULT_LIMIT,
) -> MerchantsSummary:
    expenses = [t for t in transactions if t.kind is TransactionKind.EXPENSE and not t.is_refunded]
    total_expenses = sum(-t.amount_cents for t in expenses)
    groups: dict[str, list[Transaction]] = defaultdict(list)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    anonymous: list[Transaction] = []
    for t in expenses:
        if t.merchant and t.merchant.strip():
            key = normalize_search(t.merchant)
            groups[key].append(t)
            spellings[key][t.merchant] += 1
        else:
            anonymous.append(t)
    rows = [
        _row(_display_name(spellings[key]), entries, total_expenses, categories, splits)
        for key, entries in groups.items()
    ]
    rows.sort(key=lambda r: (-r.total_spent_cents, normalize_search(r.merchant or "")))
    return MerchantsSummary(
        period=period,
        total_expenses_cents=total_expenses,
        rows=rows[: max(0, limit)],
        unidentified=(
            _row(None, anonymous, total_expenses, categories, splits) if anonymous else None
        ),
        merchant_count=len(rows),
    )


class GetTopMerchants:
    """``get_top_merchants_summary``: the ranking of one period (default: ten merchants)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, period: Period, limit: int = DEFAULT_LIMIT) -> MerchantsSummary:
        with self._uow as uow:
            categories = {c.id: c for c in uow.categories.list_all()}
            transactions = uow.transactions.list_for_competence(period.start, period.end)
            splits = uow.transactions.splits_for(split_ids(transactions))
        return rank_merchants(transactions, categories, splits, period, limit)


class ListMerchants:
    """``get_distinct_merchants``: every merchant typed so far, once, sorted ignoring accents."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, account_id: str | None = None) -> list[str]:
        with self._uow as uow:
            counts = uow.transactions.merchant_counts(account_id)
        spellings: dict[str, Counter[str]] = defaultdict(Counter)
        for name, count in counts:
            cleaned = " ".join(name.split())
            if cleaned:
                spellings[normalize_search(cleaned)][cleaned] += count
        return [_display_name(spellings[key]) for key in sorted(spellings)]
