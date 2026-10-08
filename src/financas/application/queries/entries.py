"""Filters of the entries list that need no database round trip.

The payment-method filter ("Forma de pagamento"): ``cartao_credito`` is every entry of every
credit card (an account fact, so purchases entered before the column existed still match); the
others match the stored ``payment_method`` on any bank account.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from financas.domain.models import PaymentMethod, Transaction, TransactionKind


def parse_method_filter(text: str) -> str:
    """``""`` (every entry) or a valid method code; anything else is lenient: no filter."""
    try:
        return PaymentMethod(text.strip()).value if text.strip() else ""
    except ValueError:
        return ""


def matches_method(entry: Transaction, method: str, card_ids: Collection[str]) -> bool:
    if not method:
        return True
    if method == PaymentMethod.CREDIT_CARD.value:
        return entry.account_id in card_ids
    return entry.payment_method is not None and entry.payment_method.value == method


def filter_by_method(
    entries: Sequence[Transaction], method: str, card_ids: Collection[str]
) -> list[Transaction]:
    return [e for e in entries if matches_method(e, method, card_ids)]


@dataclass(frozen=True)
class DayTotals:
    """What one day of the list adds up to (integer cents, never a float).

    ``expense_cents`` is the spending of the day: expenses minus refunds, a refunded purchase
    counts nowhere (the same figure the day header always showed). ``net_cents`` is the day's
    balance: income minus that spending, plus the transfers when the list is one account's.
    """

    income_cents: int
    expense_cents: int
    net_cents: int
    has_spending: bool  # an expense or a refund is in the day (else there is nothing to "spend")


def day_totals(
    entries: Sequence[Transaction], include_transfers: bool = False
) -> dict[dt.date, DayTotals]:
    """Income, spending and net balance of every day present in ``entries``.

    Transfers between the user's own accounts are neither income nor expense (CLAUDE.md 9.1) and
    stay out of the net too, unless ``include_transfers``: a list filtered to ONE account is that
    account's cash flow, where a transfer really moves the balance.
    """
    income: dict[dt.date, int] = defaultdict(int)
    spent: dict[dt.date, int] = defaultdict(int)
    moved: dict[dt.date, int] = defaultdict(int)
    spending_days: set[dt.date] = set()
    days: set[dt.date] = set()
    for e in entries:
        days.add(e.posted_on)
        if e.is_refunded:
            continue
        if e.kind is TransactionKind.INCOME:
            income[e.posted_on] += e.amount_cents
        elif e.kind in (TransactionKind.EXPENSE, TransactionKind.REFUND):
            spent[e.posted_on] -= e.amount_cents  # an expense is negative, a refund positive
            spending_days.add(e.posted_on)
        elif e.kind is TransactionKind.TRANSFER:
            moved[e.posted_on] += e.amount_cents
    return {
        d: DayTotals(
            income[d],
            spent[d],
            income[d] - spent[d] + (moved[d] if include_transfers else 0),
            d in spending_days,
        )
        for d in days
    }
