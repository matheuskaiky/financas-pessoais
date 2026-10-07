"""Itemized expenses: the items must add up to the entry, and spending follows the items. Pure."""

from collections.abc import Iterable, Mapping, Sequence

from financas.domain.errors import DomainError
from financas.domain.models import Transaction, TransactionKind, TransactionSplit

MIN_ITEMS = 2  # one item is just the entry with another category


def validate_split_amounts(total_cents: int, amounts: Sequence[int]) -> None:
    """At least two items, each positive, adding up exactly to ``total_cents`` (a magnitude).

    The error carries ``remaining_cents``: positive when the items fall short, negative when
    they go over (the form shows it as "Restam R$ …").
    """
    if len(amounts) < MIN_ITEMS:
        raise DomainError("SPLIT_NEEDS_TWO_ITEMS")
    if any(a <= 0 for a in amounts):
        raise DomainError("AMOUNT_NOT_POSITIVE")
    remaining = total_cents - sum(amounts)
    if remaining != 0:
        raise DomainError("SPLIT_SUM_MISMATCH", remaining_cents=remaining)


def allocations(
    entry: Transaction, splits: Mapping[str, Sequence[TransactionSplit]]
) -> list[tuple[str, int]]:
    """``(category_id, signed cents)`` the entry counts for: its items, or itself when it has none.

    The cents keep the entry's sign (an expense is negative), so the parts always add up to the
    entry's amount.
    """
    items = splits.get(entry.id)
    if not items or entry.kind is not TransactionKind.EXPENSE:
        # an entry without items has a category; one without both means the items were not loaded
        assert entry.category_id is not None, "itemized entry asked for without its items"
        return [(entry.category_id, entry.amount_cents)]
    return [(item.category_id, -item.amount_cents) for item in items]


def split_ids(entries: Iterable[Transaction]) -> list[str]:
    """Ids worth asking the repository about: only expenses can be itemized."""
    return [t.id for t in entries if t.kind is TransactionKind.EXPENSE]


def distribute_items(amounts: Sequence[int], items: Sequence[int]) -> list[list[int]]:
    """Spread the items of a plan over its installments with no cent lost or invented.

    ``amounts[m]`` is the amount of installment m, ``items[j]`` the total of item j; both add up
    to the same total. Returns ``matrix[m][j]``: what item j weighs in installment m, such that
    every row adds up to its installment and every column to its item (exactly: integers only).

    Each item but the last is spread in proportion to what the installments still have room for
    (floor of ``item * room / room_total``; the leftover cents go, one each, to the largest
    fractional parts, the earliest installment first on a tie). The last item takes whatever room
    is left, which is what makes every installment exact. With equal installments this is the
    plain rule "``⌊C/N⌋`` each, one extra cent to the first ``C mod N``" for every item but the
    last. A cell can be 0 (a tiny item over many installments): callers skip those.
    """
    total = sum(amounts)
    if any(a <= 0 for a in amounts) or any(c <= 0 for c in items):
        raise DomainError("AMOUNT_NOT_POSITIVE")
    if sum(items) != total:
        raise DomainError("SPLIT_SUM_MISMATCH", remaining_cents=total - sum(items))
    room = list(amounts)
    matrix = [[0] * len(items) for _ in amounts]
    for j, cents in enumerate(items):
        if j == len(items) - 1:
            column = list(room)  # the last item closes every installment exactly
        else:
            left = sum(room)
            column = [cents * r // left for r in room]
            leftover = cents - sum(column)
            by_fraction = sorted(range(len(room)), key=lambda m: (-(cents * room[m] % left), m))
            for m in by_fraction[:leftover]:
                column[m] += 1
        for m, share in enumerate(column):
            matrix[m][j] = share
            room[m] -= share
    return matrix
