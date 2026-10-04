"""Server-side currency formatting (``fp_money``): golden vectors shared with the browser test,
the ``.money`` markup and the privacy attribute."""

import json
from pathlib import Path

import pytest
from markupsafe import Markup

from financas.domain.money import format_brl
from financas.interfaces.web import fp_money

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "golden" / "money.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: f"{c['minor']}-{c.get('sign', 'auto')}")
def test_matches_the_golden_vectors(case: dict[str, object]) -> None:
    options = {k: case[k] for k in ("sign", "locale", "currency") if k in case}
    assert fp_money.format_currency(case["minor"], **options) == case["text"]  # type: ignore[arg-type]
    assert fp_money.format_amount(case["minor"], **options) == case["amount"]  # type: ignore[arg-type]


def test_absent_value_is_a_dash_never_zero() -> None:
    assert fp_money.format_currency(None) == "—"
    assert fp_money.money_html(None) == Markup('<span class="money money--empty">—</span>')


def test_brl_stays_the_plain_legacy_text() -> None:
    """The ``brl`` filter feeds attributes and 100+ assertions: ASCII minus and a plain space."""
    assert format_brl(-123456) == "-R$ 1.234,56"
    assert fp_money.format_currency(-123456) == "−R$ 1.234,56"


def test_money_markup_has_sign_symbol_and_amount_spans() -> None:
    html = fp_money.money_html(-18643, private="transactions")
    assert html == Markup(
        '<span class="money" data-neg data-cents="-18643" data-private="transactions">'
        '<span class="money-sign">−</span><span class="currency-symbol">R$ </span>'
        '<span class="currency-amount">186,43</span></span>'
    )


def test_privacy_attribute_is_opt_in_and_display_size_is_a_class() -> None:
    assert "data-private" not in fp_money.money_html(100)
    assert fp_money.money_html(14872341, size="display").startswith(
        '<span class="money money--display" data-cents="14872341">'
    )


def test_markup_escapes_the_group_name() -> None:
    html = fp_money.money_html(1, private='a"><script>')
    assert "<script>" not in html and 'data-private="a&quot;&gt;&lt;script&gt;"' in html


def test_install_registers_filters_and_config() -> None:
    from jinja2 import Environment

    env = Environment(autoescape=True)
    fp_money.install(env, locale="pt-BR", currency="BRL")
    out = env.from_string(
        "{{ v | money(sign='always') }}|{{ v | currency }}|{{ config.currency_symbol }}"
    ).render(v=35000)
    assert out.startswith('<span class="money" data-cents="35000" data-sign="always">')
    assert out.endswith("|R$ 350,00|R$")
    assert env.globals["config"].client == {"locale": "pt-BR", "currency": "BRL"}
