"""Filters of the entries list that need no database round trip.

The payment-method filter ("Forma de pagamento"): ``cartao_credito`` is every entry of every
credit card (an account fact, so purchases entered before the column existed still match); the
others match the stored ``payment_method`` on any bank account.
"""

from collections.abc import Collection, Sequence

from financas.domain.models import PaymentMethod, Transaction


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
