import datetime as dt
import re
from pathlib import Path

import pytest

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.interfaces import messages
from financas.interfaces.formatting import (
    format_date,
    format_date_short,
    format_day_header,
    format_day_label,
    format_month,
    format_month_long,
    format_percent,
    format_signed,
    parse_date,
)

TODAY = dt.date(2026, 7, 25)


def test_formatting() -> None:
    assert format_date(dt.date(2026, 7, 5)) == "05/07/2026"
    assert format_month(YearMonth(2026, 7)) == "jul/2026"
    assert format_month_long(YearMonth(2027, 3)) == "março de 2027"
    assert format_percent(0.7) == "70,0%"
    assert format_percent(0.12345) == "12,3%"
    assert format_percent(None) == "—"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10/07/2026", dt.date(2026, 7, 10)),
        ("5/7/2026", dt.date(2026, 7, 5)),
        ("10/07", dt.date(2026, 7, 10)),
        ("2026-07-10", dt.date(2026, 7, 10)),
        ("", TODAY),
        ("hoje", TODAY),
        ("Ontem", dt.date(2026, 7, 24)),
    ],
)
def test_parse_date(text: str, expected: dt.date) -> None:
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize("text", ["31/02/2026", "abc", "2026/07/10", "10-07-2026", "32/01"])
def test_parse_date_rejects(text: str) -> None:
    with pytest.raises(DomainError) as exc:
        parse_date(text, TODAY)
    assert exc.value.code == "INVALID_DATE"


def test_every_error_code_raised_in_the_code_base_has_a_message() -> None:
    root = Path(__file__).parents[2] / "src" / "financas"
    codes: set[str] = set()
    for path in root.rglob("*.py"):
        codes |= set(re.findall(r'DomainError\(\s*"([A-Z_]+)"', path.read_text(encoding="utf-8")))
    assert codes, "no error codes found"
    assert codes - set(messages.ERROR_MESSAGES) == set()


def test_messages_render_for_codes_with_parameters() -> None:
    err = DomainError
    assert (
        messages.render_error(err("NOT_FOUND", entity="account"))
        == "Conta: registro não encontrado."
    )
    assert "máximo de 512 KB" in messages.render_error(err("IMAGE_TOO_LARGE", max_bytes=524288))
    assert "Cartão de crédito" in messages.render_error(
        err("ACCOUNT_KIND_NOT_ALLOWED", account_kind="credit_card")
    )
    assert "Essencial" in messages.render_error(
        err("CATEGORY_GROUP_KIND_MISMATCH", group="essential", kind="income")
    )


def test_signed_and_short_date() -> None:
    assert format_signed(343742) == "+ R$ 3.437,42"
    assert format_signed(-18743) == "− R$ 187,43"
    assert format_signed(0) == "R$ 0,00"
    assert format_date_short(dt.date(2026, 7, 5)) == "05/07"


@pytest.mark.parametrize(
    ("text", "bps"),
    [("110", 11_000), ("110,5", 11_050), ("6.5%", 650), (" 12,30 % ", 1_230), ("0", 0)],
)
def test_parse_percent_bps(text: str, bps: int) -> None:
    from financas.interfaces.formatting import parse_percent_bps

    assert parse_percent_bps(text) == bps


@pytest.mark.parametrize("text", ["", "abc", "-1", "6,555", "1e2x"])
def test_parse_percent_bps_rejects(text: str) -> None:
    from financas.interfaces.formatting import parse_percent_bps

    with pytest.raises(DomainError) as exc:
        parse_percent_bps(text)
    assert exc.value.code == "INVALID_RATE"


def test_format_rate() -> None:
    from financas.domain.models import Indexer, RateMode
    from financas.interfaces.messages import format_rate

    assert format_rate(RateMode.PERCENT_OF_INDEX, Indexer.CDI, 11_000) == "110% do CDI"
    assert format_rate(RateMode.PERCENT_OF_INDEX, Indexer.CDI, 10_050) == "100,50% do CDI"
    assert format_rate(RateMode.SPREAD_OVER_INDEX, Indexer.IPCA, 650) == "IPCA + 6,50%"
    assert format_rate(RateMode.FIXED_ANNUAL, Indexer.PREFIXED, 1_230) == "12,30% a.a."
    assert format_rate(None, None, None) == "—"


def test_day_label_names_the_weekday_in_portuguese() -> None:
    assert format_day_label(dt.date(2026, 9, 24)) == "quinta-feira, 24 de setembro"
    assert format_day_label(dt.date(2026, 3, 1)) == "domingo, 1 de março"


def test_day_header_is_relative_for_today_and_yesterday() -> None:
    today = dt.date(2026, 10, 6)
    assert format_day_header(today, today) == "Hoje · 06 de outubro"
    assert format_day_header(dt.date(2026, 10, 5), today) == "Ontem · 05 de outubro"
    assert format_day_header(dt.date(2026, 10, 4), today) == "04 de outubro · Domingo"
    assert format_day_header(dt.date(2026, 3, 1), today) == "01 de março · Domingo"
