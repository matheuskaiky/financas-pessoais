"""Filters of the entries list that need no database round trip.

The payment-method filter ("Forma de pagamento"): ``cartao_credito`` is every entry of every
credit card (an account fact, so purchases entered before the column existed still match); the
others match the stored ``payment_method`` on any bank account.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from financas.domain.models import PaymentMethod, Transaction, TransactionKind, TransactionSplit


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

    ``expense_cents`` is "Gastos do dia": expenses minus refunds, in categories that are the user's
    own (a refunded purchase counts nowhere, and a pass-through item of a neutral category is the
    money of someone else: 9.14). ``income_cents`` follows the same rule. Transfers are neither.
    The account balance at the end of the day is a different thing (``GetDayBalances``).
    """

    income_cents: int
    expense_cents: int


def day_totals(
    entries: Sequence[Transaction],
    neutral_category_ids: Collection[str] = (),
    splits: Mapping[str, Sequence[TransactionSplit]] | None = None,
) -> dict[dt.date, DayTotals]:
    """Income and spending of every day present in ``entries`` (neutral categories left out)."""
    neutral = set(neutral_category_ids)
    income: dict[dt.date, int] = defaultdict(int)
    spent: dict[dt.date, int] = defaultdict(int)
    days: set[dt.date] = set()
    for e in entries:
        days.add(e.posted_on)
        if e.is_refunded:
            continue
        if e.kind is TransactionKind.INCOME:
            if e.category_id not in neutral:
                income[e.posted_on] += e.amount_cents
        elif e.kind is TransactionKind.REFUND:
            spent[e.posted_on] -= e.amount_cents  # a refund is positive and reduces the spending
        elif e.kind is TransactionKind.EXPENSE:
            items = (splits or {}).get(e.id)
            if items:  # an itemized expense counts each item by its own category
                spent[e.posted_on] += sum(
                    i.amount_cents for i in items if i.category_id not in neutral
                )
            elif e.category_id not in neutral:
                spent[e.posted_on] -= e.amount_cents
    return {d: DayTotals(income[d], spent[d]) for d in days}
