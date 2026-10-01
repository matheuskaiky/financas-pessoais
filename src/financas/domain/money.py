"""Money as integer BRL cents, and the ``YearMonth`` value object."""

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

from financas.domain.errors import DomainError

# Comma is the decimal separator; dots only group thousands (exactly 3 digits).
# Without a comma, a dot followed by 1 or 2 digits is read as a decimal point ("12.50").
_WITH_COMMA = re.compile(r"(?P<int>\d+|\d{1,3}(?:\.\d{3})+),(?P<frac>\d{1,2})")
_WITHOUT_COMMA = re.compile(
    r"(?P<int>\d+|\d{1,3}(?:\.\d{3})+)|(?P<dot_int>\d+)\.(?P<dot_frac>\d{1,2})"
)
_SIGN = re.compile(r"[+-]")


def parse_brl(text: str) -> int:
    """Parse a user-typed BRL amount into cents. Never rounds: extra decimals are an error."""
    invalid = DomainError("INVALID_AMOUNT")
    compact = text.strip().replace("R$", "", 1).strip()
    signs = _SIGN.findall(text)
    if len(signs) > 1:
        raise invalid
    body = _SIGN.sub("", compact, count=1).strip()
    if signs and not compact.startswith(signs[0]):
        raise invalid
    if match := _WITH_COMMA.fullmatch(body):
        whole, frac = match["int"].replace(".", ""), match["frac"]
    elif match := _WITHOUT_COMMA.fullmatch(body):
        if match["dot_int"] is not None:
            whole, frac = match["dot_int"], match["dot_frac"]
        else:
            whole, frac = match["int"].replace(".", ""), "0"
    else:
        raise invalid
    cents = int(Decimal(f"{whole}.{frac.ljust(2, '0')}") * 100)
    return -cents if signs and signs[0] == "-" else cents


def format_brl(cents: int) -> str:
    """Format cents as ``R$ 1.234,56`` (negative: ``-R$ 1.234,56``)."""
    whole, frac = divmod(abs(cents), 100)
    grouped = f"{whole:,}".replace(",", ".")
    return f"{'-' if cents < 0 else ''}R$ {grouped},{frac:02d}"


@dataclass(frozen=True, order=True)
class YearMonth:
    """A calendar month, printed and parsed as ``YYYY-MM``."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12 or not 1 <= self.year <= 9999:
            raise DomainError("INVALID_YEAR_MONTH")

    @classmethod
    def parse(cls, text: str) -> "YearMonth":
        match = re.fullmatch(r"(\d{4})-(\d{2})", text)
        if match is None:
            raise DomainError("INVALID_YEAR_MONTH")
        return cls(int(match[1]), int(match[2]))

    @classmethod
    def from_date(cls, day: dt.date) -> "YearMonth":
        return cls(day.year, day.month)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def add_months(self, months: int) -> "YearMonth":
        index = self.year * 12 + (self.month - 1) + months
        return YearMonth(index // 12, index % 12 + 1)

    def months_until(self, other: "YearMonth") -> int:
        return (other.year - self.year) * 12 + (other.month - self.month)

    def last_day(self) -> dt.date:
        return dt.date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def day(self, day_of_month: int) -> dt.date:
        """The given day of this month, or the last day when the month is shorter."""
        return dt.date(self.year, self.month, min(day_of_month, self.last_day().day))
