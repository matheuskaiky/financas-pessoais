"""Strict parsing of single cells: amounts, dates, booleans, numbers, months, headers.

Pure functions. A bad value raises ``DomainError`` with a stable code; ambiguity is an error and
never a guess (CLAUDE.md rule 8). Money goes through ``Decimal``, never ``float``.
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

from financas.application.csvfeed.model import (
    MAX_AMOUNT_CENTS,
    MAX_DAYS_AHEAD,
    MIN_DATE,
    AmountType,
    FeedColumn,
    FeedKind,
)
from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.domain.services.text import normalize_search

# ---- headers and enumerations ----------------------------------------------------------------

HEADER_ALIASES: dict[str, FeedColumn] = {
    "date": FeedColumn.DATE,
    "data": FeedColumn.DATE,
    "kind": FeedColumn.KIND,
    "tipo": FeedColumn.KIND,
    "account": FeedColumn.ACCOUNT,
    "conta": FeedColumn.ACCOUNT,
    "to_account": FeedColumn.TO_ACCOUNT,
    "conta_destino": FeedColumn.TO_ACCOUNT,
    "amount": FeedColumn.AMOUNT,
    "valor": FeedColumn.AMOUNT,
    "description": FeedColumn.DESCRIPTION,
    "descricao": FeedColumn.DESCRIPTION,
    "category": FeedColumn.CATEGORY,
    "categoria": FeedColumn.CATEGORY,
    "recurring": FeedColumn.RECURRING,
    "recorrente": FeedColumn.RECURRING,
    "notes": FeedColumn.NOTES,
    "observacoes": FeedColumn.NOTES,
    "statement": FeedColumn.STATEMENT,
    "fatura": FeedColumn.STATEMENT,
    "installments": FeedColumn.INSTALLMENTS,
    "parcelas": FeedColumn.INSTALLMENTS,
    "installment_number": FeedColumn.INSTALLMENT_NUMBER,
    "parcela_atual": FeedColumn.INSTALLMENT_NUMBER,
    "amount_type": FeedColumn.AMOUNT_TYPE,
    "tipo_valor": FeedColumn.AMOUNT_TYPE,
    "gross_amount": FeedColumn.GROSS_AMOUNT,
    "valor_bruto": FeedColumn.GROSS_AMOUNT,
}

KIND_ALIASES: dict[str, FeedKind] = {
    "expense": FeedKind.EXPENSE,
    "despesa": FeedKind.EXPENSE,
    "income": FeedKind.INCOME,
    "receita": FeedKind.INCOME,
    "refund": FeedKind.REFUND,
    "estorno": FeedKind.REFUND,
    "transfer": FeedKind.TRANSFER,
    "transferencia": FeedKind.TRANSFER,
    "balance": FeedKind.BALANCE,
    "saldo": FeedKind.BALANCE,
}

AMOUNT_TYPE_ALIASES: dict[str, AmountType] = {
    "total": AmountType.TOTAL,
    "installment": AmountType.INSTALLMENT,
    "parcela": AmountType.INSTALLMENT,
}

_TRUE = {"yes", "true", "1", "sim", "s", "x"}
_FALSE = {"no", "false", "0", "nao", "n", ""}


def normalize_header(text: str) -> str:
    """``" Conta Destino "`` → ``conta_destino`` (case, accents, spaces and hyphens ignored)."""
    return normalize_search(text).replace("-", "_").replace(" ", "_")


def resolve_header(text: str) -> FeedColumn | None:
    return HEADER_ALIASES.get(normalize_header(text))


def parse_kind(text: str) -> FeedKind:
    kind = KIND_ALIASES.get(normalize_search(text))
    if kind is None:
        raise DomainError("INVALID_CHOICE")
    return kind


def parse_amount_type(text: str) -> AmountType:
    value = AMOUNT_TYPE_ALIASES.get(normalize_search(text))
    if value is None:
        raise DomainError("INVALID_CHOICE")
    return value


def parse_bool(text: str) -> bool:
    """yes/no/true/false/1/0/sim/não/nao/s/n/x; blank is no."""
    key = normalize_search(text)
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    raise DomainError("INVALID_BOOLEAN")


# ---- numbers ---------------------------------------------------------------------------------

_INTEGER = re.compile(r"[0-9]{1,6}")


def parse_int(text: str) -> int:
    """A plain non-negative integer in ASCII digits (no sign, no decimals, no separators)."""
    if _INTEGER.fullmatch(text.strip()) is None:
        raise DomainError("INVALID_NUMBER")
    return int(text.strip())


_AMOUNT = re.compile(
    r"(?P<lead>[+-])?\s*(?:R\$)?\s*(?P<trail>[+-])?\s*(?P<body>[0-9.,]+)",
    re.IGNORECASE | re.ASCII,
)
_GROUPED = {
    ".": re.compile(r"[0-9]{1,3}(?:\.[0-9]{3})+"),
    ",": re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+"),
}
_PLAIN = re.compile(r"[0-9]+")
_ONE_TO_THREE_DIGITS = re.compile(r"[0-9]{1,3}")


@dataclass(frozen=True)
class Amount:
    """``magnitude_cents`` is never negative; ``sign`` is what the user typed (``None``: none)."""

    magnitude_cents: int
    sign: str | None

    @property
    def signed_cents(self) -> int:
        return -self.magnitude_cents if self.sign == "-" else self.magnitude_cents


def _whole_part(head: str, group: str) -> int:
    """The integer part: plain digits, or digits grouped by thousands with the ``group`` char."""
    if _PLAIN.fullmatch(head):
        return int(head)
    if _GROUPED[group].fullmatch(head):
        return int(head.replace(group, ""))
    raise DomainError("INVALID_AMOUNT")


def _split_decimal(body: str) -> tuple[int, str]:
    """``(whole, fraction digits)`` of the unsigned text, by the rule in docs/IMPORTACAO_DADOS.md.

    The last ``.`` or ``,`` is the decimal separator when followed by exactly 1 or 2 digits;
    groups of exactly 3 digits are thousands separators; a lone ``1.234`` is ambiguous.
    """
    separators = [c for c in body if c in ".,"]
    if not separators:
        return int(body), ""
    last = max(body.rfind("."), body.rfind(","))
    mark, head, tail = body[last], body[:last], body[last + 1 :]
    other = "," if mark == "." else "."
    if not head or not tail:
        raise DomainError("INVALID_AMOUNT")
    if len(tail) in (1, 2):
        if mark in head:
            raise DomainError("INVALID_AMOUNT")
        return _whole_part(head, other), tail
    if len(tail) == 3:
        if len(set(separators)) == 2:  # "1.234,567": the last one is decimal, with 3 digits
            _whole_part(head, other)
            raise DomainError("AMOUNT_TOO_MANY_DECIMALS")
        if len(separators) >= 2:  # "1.234.567": thousands only
            return _whole_part(body, mark), ""
        if _ONE_TO_THREE_DIGITS.fullmatch(head):  # "1.234": thousands or decimals? never guess
            raise DomainError("AMOUNT_AMBIGUOUS")
        if _PLAIN.fullmatch(head):  # "1234.567"
            raise DomainError("AMOUNT_TOO_MANY_DECIMALS")
        raise DomainError("INVALID_AMOUNT")
    if len(separators) == 1 and _PLAIN.fullmatch(head):
        raise DomainError("AMOUNT_TOO_MANY_DECIMALS")
    raise DomainError("INVALID_AMOUNT")


def parse_amount(text: str) -> Amount:
    """``1234.56``, ``1234,56``, ``1.234,56``, ``1,234.56``, ``R$ 1.234,56``, optional sign.

    Never rounds and never guesses: more than two decimals and the lone ``1.234`` are errors.
    The result is within 0 .. 10**12 - 1 cents (zero is allowed here; the row rules decide).
    """
    cleaned = text.replace(" ", " ").replace(" ", " ").replace("−", "-").strip()
    match = _AMOUNT.fullmatch(cleaned)
    if match is None or (match["lead"] and match["trail"]):
        raise DomainError("INVALID_AMOUNT")
    whole, fraction = _split_decimal(match["body"])
    cents = int(Decimal(whole) * 100 + Decimal(fraction.ljust(2, "0") or "0"))
    if cents >= MAX_AMOUNT_CENTS:
        raise DomainError("AMOUNT_TOO_LARGE")
    return Amount(cents, match["lead"] or match["trail"])


# ---- dates -----------------------------------------------------------------------------------

_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_BR_DATE = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})")


def parse_date(text: str, today: dt.date) -> dt.date:
    """ISO ``YYYY-MM-DD`` or ``dd/mm/yyyy`` (4-digit year); at most 366 days after ``today``."""
    value = text.strip()
    try:
        if match := _ISO_DATE.fullmatch(value):
            day = dt.date(int(match[1]), int(match[2]), int(match[3]))
        elif match := _BR_DATE.fullmatch(value):
            day = dt.date(int(match[3]), int(match[2]), int(match[1]))
        else:
            raise DomainError("INVALID_DATE")
    except ValueError:
        raise DomainError("INVALID_DATE") from None
    if day < MIN_DATE:
        raise DomainError("DATE_TOO_OLD")
    if day > today + dt.timedelta(days=MAX_DAYS_AHEAD):
        raise DomainError("DATE_TOO_FAR")
    return day


_MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")


def parse_statement_month(text: str) -> YearMonth:
    """``YYYY-MM``, the closing month of a card statement (years 1900 to 2200)."""
    value = text.strip()
    if _MONTH.fullmatch(value) is None:
        raise DomainError("INVALID_YEAR_MONTH")
    month = YearMonth.parse(value)
    if not 1900 <= month.year <= 2200:
        raise DomainError("INVALID_YEAR_MONTH")
    return month
