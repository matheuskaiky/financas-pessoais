"""pt-BR formatting and input parsing, only at the edge (CLI and web)."""

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth, format_brl

MONTH_ABBREVIATIONS = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)
MONTH_NAMES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


def format_date(day: dt.date) -> str:
    return day.strftime("%d/%m/%Y")


def format_decimal_comma(cents: int) -> str:
    """``231846`` → ``2318,46``: the value for an input field (no currency symbol)."""
    whole, frac = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{whole},{frac:02d}"


def format_date_short(day: dt.date) -> str:
    return day.strftime("%d/%m")


def format_signed(cents: int) -> str:
    """``+ R$ 3.437,42`` / ``− R$ 187,43``: the sign is never only a color (design board)."""
    if cents == 0:
        return format_brl(0)
    return f"{'+' if cents > 0 else '−'} {format_brl(abs(cents))}"


def format_month(month: YearMonth) -> str:
    """``jul/2026``."""
    return f"{MONTH_ABBREVIATIONS[month.month - 1]}/{month.year}"


def format_month_long(month: YearMonth) -> str:
    """``julho de 2026``."""
    return f"{MONTH_NAMES[month.month - 1]} de {month.year}"


def format_percent(ratio: float | None) -> str:
    return "—" if ratio is None else f"{ratio * 100:.1f}".replace(".", ",") + "%"


def parse_date(text: str, today: dt.date) -> dt.date:
    """Accepts ``dd/mm/aaaa``, ``dd/mm`` (current year), ``aaaa-mm-dd`` and ``hoje``/``ontem``."""
    value = text.strip().lower()
    if value in {"", "hoje"}:
        return today
    if value == "ontem":
        return today - dt.timedelta(days=1)
    try:
        if match := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", value):
            day, month, year = match.groups()
            return dt.date(int(year) if year else today.year, int(month), int(day))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return dt.date.fromisoformat(value)
    except ValueError:
        pass
    raise DomainError("INVALID_DATE")


def parse_percent_bps(text: str) -> int:
    """``110`` / ``110,5`` / ``6.5%`` → basis points (11000 / 11050 / 650). Never rounds."""
    cleaned = text.strip().removesuffix("%").strip().replace(",", ".")
    try:
        bps = Decimal(cleaned) * 100
    except InvalidOperation:
        raise DomainError("INVALID_RATE") from None
    if bps != bps.to_integral_value() or bps < 0:
        raise DomainError("INVALID_RATE")
    return int(bps)


def format_bps(bps: int, keep_decimals: bool = True) -> str:
    """``1230`` → ``12,30%``; without decimals when whole and ``keep_decimals`` is false."""
    whole, frac = divmod(bps, 100)
    return f"{whole}%" if frac == 0 and not keep_decimals else f"{whole},{frac:02d}%"
