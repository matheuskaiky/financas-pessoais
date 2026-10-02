"""The E se… simulator: schedule, limit, free cash, due months and present value."""

import datetime as dt
from decimal import Decimal

import pytest

from financas.application.whatif import (
    CardFacts,
    Purchase,
    WhatIfFacts,
    monthly_dues,
    simulate,
    tightest,
)
from financas.domain.errors import DomainError
from financas.domain.money import YearMonth

TODAY = dt.date(2026, 10, 1)


def card(
    name: str = "BB",
    due_day: int = 5,
    n: int = 11,
    limit: int | None = 1_200_000,
    committed: int = 600_000,
    known: dict[YearMonth, tuple[dt.date, dt.date]] | None = None,
) -> CardFacts:
    return CardFacts(name.lower(), name, due_day, n, limit, committed, known or {})


def facts(
    *cards: CardFacts,
    today: dt.date = TODAY,
    cash: int | None = 760_045,
    closed: int = 418_856,
    registered: dict[YearMonth, int] | None = None,
) -> WhatIfFacts:
    return WhatIfFacts(today, cash, closed, cards, registered or {})


def purchase(**kw: object) -> Purchase:
    base: dict[str, object] = {
        "description": "Geladeira",
        "installments": 10,
        "purchased_on": TODAY,
        "price_cents": 420_000,
        "installment_cents": None,
    }
    return Purchase(**{**base, **kw})  # type: ignore[arg-type]


def test_claude_md_example_schedule_with_remainder_on_the_first_installment() -> None:
    # CLAUDE.md 9.4: 30,100 in 3x bought on 2026-07-26, due day 5, closing 11 days before
    sim = simulate(
        purchase(price_cents=30_100, installments=3, purchased_on=dt.date(2026, 7, 26)),
        facts(card(), today=dt.date(2026, 7, 26)),
        Decimal(0),
    )
    scenario = sim.cards[0]
    assert [line.amount_cents for line in scenario.lines] == [10_034, 10_033, 10_033]
    assert [str(line.month) for line in scenario.lines] == ["2026-08", "2026-09", "2026-10"]
    assert [line.due for line in scenario.lines] == [
        dt.date(2026, 9, 5),
        dt.date(2026, 10, 5),
        dt.date(2026, 11, 5),
    ]
    assert scenario.total_cents == 30_100


def test_purchase_on_the_closing_date_goes_to_the_next_statement() -> None:
    # due day 5, 11 days before: closes on the 25th
    sim = simulate(
        purchase(installments=1, purchased_on=dt.date(2026, 7, 25), price_cents=5_000),
        facts(card(), today=dt.date(2026, 7, 25)),
        Decimal(0),
    )
    assert str(sim.cards[0].lines[0].month) == "2026-08"


def test_stored_statement_dates_win_over_the_settings() -> None:
    # the stored October statement closes on 2026-10-03 although the settings say the 24th
    stored = {
        YearMonth(2026, 10): (dt.date(2026, 10, 3), dt.date(2026, 10, 14)),
        YearMonth(2026, 11): (dt.date(2026, 11, 3), dt.date(2026, 11, 14)),
    }
    sim = simulate(
        purchase(installments=2, price_cents=10_000, purchased_on=dt.date(2026, 10, 2)),
        facts(card(known=stored)),
        Decimal(0),
    )
    first, second = sim.cards[0].lines
    assert (first.month, first.closing, first.due) == (
        YearMonth(2026, 10),
        dt.date(2026, 10, 3),
        dt.date(2026, 10, 14),
    )
    assert second.due == dt.date(2026, 11, 14)


def test_installment_value_given_by_the_user_is_repeated() -> None:
    sim = simulate(purchase(installment_cents=45_000), facts(card()), Decimal(0))
    scenario = sim.cards[0]
    assert {line.amount_cents for line in scenario.lines} == {45_000}
    assert scenario.total_cents == 450_000
    assert scenario.difference_cents == 30_000  # 450,000 - 420,000 at a zero rate


def test_only_the_installment_value_leaves_no_cash_price() -> None:
    sim = simulate(purchase(price_cents=None, installment_cents=45_000), facts(card()), Decimal(0))
    assert sim.cash.fits is None and sim.cash.price_cents is None
    assert sim.cards[0].implied_rate is None and sim.cards[0].difference_cents is None


def test_limit_after_the_purchase_and_fits() -> None:
    sim = simulate(purchase(), facts(card(limit=1_200_000, committed=600_000)), Decimal(0))
    scenario = sim.cards[0]
    assert scenario.usage_before.committed_cents == 600_000
    assert scenario.usage_after.committed_cents == 1_020_000
    assert scenario.usage_after.available_cents == 180_000
    assert scenario.usage_after.percent == pytest.approx(85.0)
    assert scenario.fits_limit is True


def test_does_not_fit_when_the_total_passes_the_limit() -> None:
    sim = simulate(purchase(), facts(card(limit=600_000, committed=491_230)), Decimal(0))
    assert sim.cards[0].fits_limit is False
    assert sim.cards[0].usage_after.available_cents == 600_000 - 491_230 - 420_000


def test_card_without_limit_is_unknown_never_fits() -> None:
    sim = simulate(purchase(), facts(card(limit=None)), Decimal(0))
    assert sim.cards[0].fits_limit is None
    assert sim.cards[0].usage_after.percent is None


def test_free_cash_is_cash_minus_closed_statements() -> None:
    sim = simulate(purchase(), facts(card(), cash=760_045, closed=418_856), Decimal(0))
    assert sim.free_cash_cents == 341_189
    assert sim.cash.fits is False  # 4,200 > 3,411.89
    assert sim.cash.free_after_cents == 341_189 - 420_000


def test_cash_fits_when_free_cash_covers_the_price() -> None:
    sim = simulate(purchase(price_cents=300_000), facts(card()), Decimal(0))
    assert sim.cash.fits is True and sim.cash.free_after_cents == 41_189


def test_no_balance_means_unknown_cash_not_zero() -> None:
    sim = simulate(purchase(), facts(card(), cash=None), Decimal(0))
    assert sim.free_cash_cents is None and sim.cash.fits is None


def test_zero_rate_makes_cash_cheaper_only_when_installments_cost_more() -> None:
    same = simulate(purchase(installment_cents=42_000), facts(card()), Decimal(0))
    assert same.cards[0].difference_cents == 0
    assert same.cards[0].implied_rate == 0


def test_a_higher_assumed_rate_makes_installments_cheaper() -> None:
    plan = purchase(installment_cents=45_000)
    low = simulate(plan, facts(card()), Decimal("0.001")).cards[0]
    high = simulate(plan, facts(card()), Decimal("0.02")).cards[0]
    assert low.difference_cents is not None and low.difference_cents > 0
    assert high.difference_cents is not None and high.difference_cents < 0
    assert high.implied_rate is not None
    # at the implied rate the two ways cost the same (within rounding)
    at_implied = simulate(plan, facts(card()), high.implied_rate).cards[0]
    assert at_implied.difference_cents is not None and abs(at_implied.difference_cents) <= 5


def test_cards_without_cycle_settings_are_left_out_by_the_caller_not_crashing() -> None:
    sim = simulate(purchase(), facts(), Decimal(0))
    assert sim.cards == ()


def test_too_small_installments_are_a_domain_error() -> None:
    with pytest.raises(DomainError) as error:
        simulate(purchase(price_cents=5, installments=10), facts(card()), Decimal(0))
    assert error.value.code == "INSTALLMENT_AMOUNT_TOO_SMALL"


def test_monthly_dues_stack_the_purchase_on_what_is_registered() -> None:
    registered = {YearMonth(2026, 10): 40_000, YearMonth(2026, 11): 144_023}
    f = facts(card(), registered=registered)
    scenario = simulate(purchase(installment_cents=45_000), f, Decimal(0)).cards[0]
    dues = monthly_dues(f, scenario)
    assert dues[0].month == YearMonth(2026, 10) and len(dues) >= 11
    by_month = {d.month: d for d in dues}
    assert by_month[YearMonth(2026, 11)].registered_cents == 144_023
    assert sum(d.new_cents for d in dues) == 450_000
    first_due = YearMonth.from_date(scenario.lines[0].due)
    assert by_month[first_due].new_cents == 45_000
    assert tightest(dues) is not None


def test_monthly_dues_without_a_purchase_and_empty_state() -> None:
    dues = monthly_dues(facts(card()), None)
    assert len(dues) == 11 and all(d.total_cents == 0 for d in dues)
    assert tightest(dues) is None


def test_monthly_dues_grow_to_cover_a_long_plan_and_stop_at_the_cap() -> None:
    f = facts(card())
    long_plan = simulate(purchase(installments=18, price_cents=180_000), f, Decimal(0)).cards[0]
    assert len(monthly_dues(f, long_plan)) >= 18
    huge = simulate(purchase(installments=60, price_cents=600_000), f, Decimal(0)).cards[0]
    assert len(monthly_dues(f, huge)) == 24
