"""Present value of payments in the future (E se…, "Compensa?"). Pure functions.

``monthly_rate`` is what the user assumes their money earns per month (an assumption typed by the
user, never market data). A payment ``days`` away is worth ``amount / (1 + rate) ** (days / 30)``
today. The implied rate of an installment plan is the monthly rate at which the present value of
the installments equals the cash price: the interest embedded in the plan.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

from financas.domain.errors import DomainError

DAYS_PER_MONTH = 30
_MAX_RATE = Decimal(1)  # 100% a month: past that a bisection is meaningless


def present_value(payments: Sequence[tuple[int, int]], monthly_rate: Decimal) -> int:
    """Cents today of ``(days from today, cents)`` payments, rounded to the cent."""
    if monthly_rate < 0:
        raise DomainError("INVALID_RATE")
    with localcontext() as ctx:
        ctx.prec = 40
        base = Decimal(1) + monthly_rate
        total = Decimal(0)
        for days, cents in payments:
            if days < 0:
                raise DomainError("INVALID_DAYS", days=days)
            total += Decimal(cents) / base ** (Decimal(days) / DAYS_PER_MONTH)
        return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def implied_monthly_rate(price_cents: int, payments: Sequence[tuple[int, int]]) -> Decimal:
    """The monthly rate that makes the payments worth ``price_cents`` today.

    Zero when the payments total no more than the price (no interest). Bisection to 1e-10.
    """
    if price_cents <= 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")
    if sum(cents for _, cents in payments) <= price_cents:
        return Decimal(0)
    low, high = Decimal(0), _MAX_RATE
    if present_value(payments, high) > price_cents:
        return high  # absurdly expensive plan: capped
    with localcontext() as ctx:
        ctx.prec = 40
        for _ in range(70):
            middle = (low + high) / 2
            if present_value(payments, middle) > price_cents:
                low = middle
            else:
                high = middle
        return (low + high) / 2


def annual_rate(monthly_rate: Decimal) -> Decimal:
    """Compounded yearly equivalent of a monthly rate."""
    with localcontext() as ctx:
        ctx.prec = 40
        return (Decimal(1) + monthly_rate) ** 12 - 1
