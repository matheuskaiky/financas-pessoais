"""fp_charts (front v3 patch): whole-percent carriers and the Jinja filters."""

from jinja2 import Environment

from financas.interfaces.web import fp_charts, fp_money


def test_pct_cls_rounds_to_a_whole_percent_class() -> None:
    assert fp_charts.pct_cls(36.6) == "pct-37"
    assert fp_charts.pct_cls(63, "at") == "at-63"


def test_shares_close_at_100() -> None:
    assert sum(fp_charts.shares([1, 1, 1])) == 100


def test_install_registers_the_cx_filters() -> None:
    env = Environment()
    fp_money.install(env, locale="pt-BR", currency="BRL")
    fp_charts.install(env)
    assert {"cx_odo", "cx_parts", "cx_compact", "cx_pct", "cx_cls"} <= set(env.filters)
    assert "cx_seal_roses" in env.globals
    assert env.from_string("{{ 36.6|cx_cls }}").render() == "pct-37"


def test_compact_uses_the_pt_br_locale() -> None:
    assert "," in fp_charts.pct1(34.2)
