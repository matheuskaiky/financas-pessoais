import datetime as dt

import pytest

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth, format_brl, parse_brl


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("0", 0),
        ("1", 100),
        ("1,5", 150),
        ("1,50", 150),
        ("1234,56", 123456),
        ("1.234,56", 123456),
        ("1.234.567,89", 123456789),
        ("R$ 1.234,56", 123456),
        ("  R$1.234,56  ", 123456),
        ("12.50", 1250),
        ("1.234", 123400),
        ("-301,00", -30100),
        ("-R$ 7,82", -782),
        ("+7,82", 782),
        ("0,01", 1),
        ("301.00", 30100),
    ],
)
def test_parse_brl(text: str, cents: int) -> None:
    assert parse_brl(text) == cents


@pytest.mark.parametrize(
    "text",
    ["", "abc", "1,234", "1,2,3", "1.23.4", "1,50,", "--1", "R$", "1,505", "1e3", "1 000,00"],
)
def test_parse_brl_rejects_invalid_or_lossy_input(text: str) -> None:
    with pytest.raises(DomainError) as exc:
        parse_brl(text)
    assert exc.value.code == "INVALID_AMOUNT"


def test_parse_brl_never_returns_float() -> None:
    assert isinstance(parse_brl("10,10"), int)


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (0, "R$ 0,00"),
        (1, "R$ 0,01"),
        (99, "R$ 0,99"),
        (100, "R$ 1,00"),
        (123456, "R$ 1.234,56"),
        (123456789, "R$ 1.234.567,89"),
        (-30100, "-R$ 301,00"),
        (-5, "-R$ 0,05"),
    ],
)
def test_format_brl(cents: int, text: str) -> None:
    assert format_brl(cents) == text


@pytest.mark.parametrize("cents", [0, 1, 99, 123456, -30100, -5, 10**12])
def test_format_then_parse_round_trips(cents: int) -> None:
    assert parse_brl(format_brl(cents)) == cents


def test_year_month_parse_and_str() -> None:
    assert str(YearMonth.parse("2026-07")) == "2026-07"
    assert YearMonth.parse("2026-07") == YearMonth(2026, 7)


@pytest.mark.parametrize("text", ["2026-7", "2026-13", "2026-00", "07/2026", "", "2026-07-01"])
def test_year_month_rejects_invalid(text: str) -> None:
    with pytest.raises(DomainError) as exc:
        YearMonth.parse(text)
    assert exc.value.code == "INVALID_YEAR_MONTH"


def test_year_month_from_date() -> None:
    assert YearMonth.from_date(dt.date(2026, 7, 25)) == YearMonth(2026, 7)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        ("2026-07", 1, "2026-08"),
        ("2026-12", 1, "2027-01"),
        ("2026-01", -1, "2025-12"),
        ("2026-09", 7, "2027-04"),
        ("2026-07", 0, "2026-07"),
        ("2026-07", 24, "2028-07"),
        ("2026-07", -19, "2024-12"),
    ],
)
def test_year_month_add_months(start: str, months: int, expected: str) -> None:
    assert YearMonth.parse(start).add_months(months) == YearMonth.parse(expected)


def test_year_month_ordering_and_months_until() -> None:
    assert YearMonth(2026, 12) < YearMonth(2027, 1)
    assert YearMonth(2026, 9).months_until(YearMonth(2027, 4)) == 7
    assert YearMonth(2027, 4).months_until(YearMonth(2026, 9)) == -7


@pytest.mark.parametrize(
    ("ym", "last_day"),
    [
        (YearMonth(2026, 2), 28),
        (YearMonth(2028, 2), 29),
        (YearMonth(2026, 4), 30),
        (YearMonth(2026, 12), 31),
    ],
)
def test_year_month_last_day(ym: YearMonth, last_day: int) -> None:
    assert ym.last_day() == dt.date(ym.year, ym.month, last_day)
    assert ym.day(31) == dt.date(ym.year, ym.month, last_day)


def test_year_month_day_clamps_to_month_end() -> None:
    assert YearMonth(2026, 7).day(25) == dt.date(2026, 7, 25)
    assert YearMonth(2026, 2).day(31) == dt.date(2026, 2, 28)
