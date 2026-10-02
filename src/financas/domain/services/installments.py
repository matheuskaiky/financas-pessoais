"""Installment splitting and schedules (CLAUDE.md 9.4). Pure functions."""

from dataclasses import dataclass

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth


@dataclass(frozen=True)
class InstallmentLine:
    number: int
    statement_month: YearMonth
    amount_cents: int


def split_total(total_cents: int, count: int) -> list[int]:
    """Installments 2..N get ⌊total ÷ N⌋; the first one absorbs the whole remainder."""
    if count < 1:
        raise DomainError("INSTALLMENT_OUT_OF_RANGE", number=count, count=count)
    if total_cents <= 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")
    base = total_cents // count
    if base < 1:
        raise DomainError("INSTALLMENT_AMOUNT_TOO_SMALL", count=count)
    return [total_cents - base * (count - 1)] + [base] * (count - 1)


def build_schedule(
    *,
    count: int,
    first_number: int,
    first_statement: YearMonth,
    total_cents: int | None = None,
    installment_cents: int | None = None,
) -> list[InstallmentLine]:
    """Installments ``first_number..count``; installment k goes to ``first_statement + (k - n)``.

    ``first_statement`` is the statement of installment ``first_number`` (of installment 1 for a
    new purchase, of the current one for a purchase already running). Give either the purchase
    total (split by ``split_total``) or the installment value as printed on the statement.
    """
    if count < 1 or not 1 <= first_number <= count:
        raise DomainError("INSTALLMENT_OUT_OF_RANGE", number=first_number, count=count)
    if (total_cents is None) == (installment_cents is None):
        raise DomainError("AMOUNT_REQUIRED")
    if total_cents is not None:
        amounts = split_total(total_cents, count)
    else:
        assert installment_cents is not None
        if installment_cents <= 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        amounts = [installment_cents] * count
    return [
        InstallmentLine(
            number, first_statement.add_months(number - first_number), amounts[number - 1]
        )
        for number in range(first_number, count + 1)
    ]
