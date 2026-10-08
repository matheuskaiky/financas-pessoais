"""Smart suggestions for the entry form: what the user habitually enters, no model involved.

Definitions (each one has a test):
- The history is the last ``WINDOW_DAYS`` days of **expenses and incomes** (transfers, refunds,
  refunded purchases and installments are not habits). A habit is a group of entries with the same
  description (ignoring case and accents), category, account and payment method.
- ``score`` = the sum, over the group's entries, of ``0.5 ** (age_in_days / HALF_LIFE_DAYS)``:
  frequency weighted by recency, so a lunch eaten 5 times last month beats one eaten 8 times
  four months ago. Ties go to the more recent, then to the description.
- ``habitual_amount_cents`` is the amount every entry of the group has; failing that, the amount
  most of them have when it covers at least 60 % of at least 3 entries; otherwise ``None``.
- An income never proposes a payment method an income cannot have (Débito, Boleto).
- Search (``q``) is accent- and case-insensitive and matches anywhere in the description.
"""

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from financas.domain.models import PaymentMethod, Transaction, TransactionKind
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.rules import EXPENSE_ONLY_METHODS
from financas.domain.services.text import normalize_search

WINDOW_DAYS = 180
HALF_LIFE_DAYS = 45
MIN_MODE_SHARE = 0.6
MIN_MODE_COUNT = 3
DEFAULT_LIMIT = 8
MAX_LIMIT = 50
FLOWS = {"expense": TransactionKind.EXPENSE, "income": TransactionKind.INCOME}


@dataclass(frozen=True)
class Suggestion:
    description: str  # the spelling of the most recent entry of the group
    flow: str  # "expense" or "income"
    category_id: str | None  # ``None``: an itemized entry
    account_id: str
    payment_method: PaymentMethod | None
    habitual_amount_cents: int | None  # a positive magnitude
    count: int
    last_on: dt.date
    score: float


def habitual_amount(amounts: list[int]) -> int | None:
    """The amount of a recurring entry: all equal, or a clear majority of at least 3 entries."""
    if not amounts:
        return None
    value, times = Counter(amounts).most_common(1)[0]
    if times == len(amounts):
        return value
    if len(amounts) >= MIN_MODE_COUNT and times / len(amounts) >= MIN_MODE_SHARE:
        return value
    return None


def rank_suggestions(
    entries: Iterable[Transaction],
    today: dt.date,
    flow: str,
    q: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[Suggestion]:
    kind = FLOWS[flow]
    wanted = normalize_search(q)
    groups: dict[tuple[str, str | None, str, PaymentMethod | None], list[Transaction]] = (
        defaultdict(list)
    )
    for t in entries:
        if t.kind is not kind or t.plan_id is not None or t.is_refunded or not t.description:
            continue
        if wanted and wanted not in t.description_search:
            continue
        method = t.payment_method
        if flow == "income" and method in EXPENSE_ONLY_METHODS:
            method = None
        groups[(t.description_search, t.category_id, t.account_id, method)].append(t)
    ranked: list[Suggestion] = []
    for (_, category_id, account_id, method), rows in groups.items():
        newest = max(rows, key=lambda t: (t.posted_on, t.id))
        score = sum(0.5 ** (max((today - t.posted_on).days, 0) / HALF_LIFE_DAYS) for t in rows)
        ranked.append(
            Suggestion(
                newest.description,
                flow,
                category_id,
                account_id,
                method,
                habitual_amount([abs(t.amount_cents) for t in rows]),
                len(rows),
                newest.posted_on,
                round(score, 6),
            )
        )
    ranked.sort(key=lambda s: (-s.score, -s.last_on.toordinal(), s.description))
    return ranked[: max(1, min(limit, MAX_LIMIT))]


class ListSuggestions:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, flow: str, q: str = "", limit: int = DEFAULT_LIMIT) -> list[Suggestion]:
        if flow not in FLOWS:
            return []
        today = self._clock.today()
        with self._uow as uow:
            entries = uow.transactions.list_between(today - dt.timedelta(days=WINDOW_DAYS), today)
        return rank_suggestions(entries, today, flow, q, limit)
