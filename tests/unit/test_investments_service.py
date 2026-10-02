"""Phase 3a acceptance tests: CLAUDE.md 9.6 (valuations, yield, allocation, staleness)."""

import datetime as dt

import pytest

from financas.domain.errors import DomainError
from financas.domain.models import AssetClass
from financas.domain.rules import validate_gross_balance
from financas.domain.services.balances import AnchorPoint
from financas.domain.services.investments import (
    allocate,
    current_value,
    is_stale,
    period_yield,
    valuation_age_days,
)

D = dt.date


def val(day: dt.date, cents: int) -> AnchorPoint:
    return AnchorPoint(day, cents)


# --- current value: last valuation + net flows after it ---


def test_no_valuation_means_no_value_never_zero() -> None:
    assert current_value([], [(D(2026, 7, 1), 500)], D(2026, 7, 10)) is None


def test_current_value_adds_the_net_flows_after_the_last_valuation() -> None:
    valuations = [val(D(2026, 6, 30), 1_000_000), val(D(2026, 7, 31), 1_112_000)]
    flows = [
        (D(2026, 7, 10), 100_000),  # before the last valuation: already inside it
        (D(2026, 7, 31), 5_000),  # on the valuation date: already inside it
        (D(2026, 8, 5), 50_000),  # contribution after it
        (D(2026, 8, 20), -20_000),  # withdrawal after it
        (D(2026, 9, 5), 999_999),  # after "today"
    ]
    assert current_value(valuations, flows, D(2026, 8, 31)) == 1_112_000 + 50_000 - 20_000


def test_a_valuation_in_the_future_is_ignored() -> None:
    valuations = [val(D(2026, 7, 31), 100), val(D(2026, 12, 31), 999)]
    assert current_value(valuations, [], D(2026, 8, 15)) == 100


# --- yield (period) = end - start - net contributions ---


def test_yield_example_with_a_contribution_in_the_month() -> None:
    valuations = [val(D(2026, 7, 1), 1_000_000), val(D(2026, 7, 31), 1_112_000)]
    flows = [(D(2026, 7, 10), 100_000)]
    result = period_yield(valuations, flows, D(2026, 7, 1), D(2026, 7, 31))
    assert result is not None
    assert result.start_value_cents == 1_000_000 and result.end_value_cents == 1_112_000
    assert result.net_contributions_cents == 100_000
    assert result.yield_cents == 12_000
    assert result.simple_return == pytest.approx(12_000 / 1_100_000)


def test_yield_counts_withdrawals_as_negative_contributions() -> None:
    valuations = [val(D(2026, 7, 1), 1_000_000), val(D(2026, 7, 31), 880_000)]
    flows = [(D(2026, 7, 15), -150_000)]
    result = period_yield(valuations, flows, D(2026, 7, 1), D(2026, 7, 31))
    assert result is not None and result.yield_cents == 30_000
    assert result.simple_return == pytest.approx(30_000 / 850_000)


def test_yield_is_measured_from_the_first_valuation_when_none_precedes_the_period() -> None:
    valuations = [val(D(2026, 3, 15), 500_000), val(D(2026, 12, 31), 560_000)]
    flows = [(D(2026, 2, 1), 500_000), (D(2026, 5, 1), 40_000)]  # the first one is before the start
    result = period_yield(valuations, flows, D(2026, 1, 1), D(2026, 12, 31))
    assert result is not None
    assert result.baseline_date == D(2026, 3, 15)
    assert result.net_contributions_cents == 40_000
    assert result.yield_cents == 560_000 - 500_000 - 40_000


def test_yield_uses_the_valuation_before_the_period_as_the_start() -> None:
    valuations = [val(D(2025, 12, 31), 700_000), val(D(2026, 12, 31), 800_000)]
    flows = [(D(2025, 12, 31), 10_000), (D(2026, 6, 1), 60_000)]
    result = period_yield(valuations, flows, D(2026, 1, 1), D(2026, 12, 31))
    assert result is not None
    assert (result.start_value_cents, result.net_contributions_cents) == (700_000, 60_000)
    assert result.yield_cents == 40_000


def test_yield_needs_two_valuations() -> None:
    assert period_yield([], [], D(2026, 1, 1), D(2026, 12, 31)) is None
    one = [val(D(2026, 6, 1), 100)]
    assert period_yield(one, [], D(2026, 1, 1), D(2026, 12, 31)) is None
    after = [val(D(2027, 3, 1), 100)]
    assert period_yield(after, [], D(2026, 1, 1), D(2026, 12, 31)) is None


def test_simple_return_is_blank_without_a_base() -> None:
    valuations = [val(D(2026, 7, 1), 0), val(D(2026, 7, 31), 500)]
    result = period_yield(valuations, [], D(2026, 7, 1), D(2026, 7, 31))
    assert result is not None and result.yield_cents == 500 and result.simple_return is None


def test_a_loss_is_a_negative_yield() -> None:
    valuations = [val(D(2026, 7, 1), 1_000_000), val(D(2026, 7, 31), 990_000)]
    result = period_yield(valuations, [], D(2026, 7, 1), D(2026, 7, 31))
    assert result is not None and result.yield_cents == -10_000 and result.simple_return < 0  # type: ignore[operator]


# --- staleness ---


@pytest.mark.parametrize(
    ("last", "today", "age", "stale"),
    [
        (D(2026, 9, 1), D(2026, 9, 1), 0, False),
        (D(2026, 8, 1), D(2026, 9, 5), 35, False),
        (D(2026, 8, 1), D(2026, 9, 6), 36, True),
        (D(2026, 7, 25), D(2026, 9, 1), 38, True),
    ],
)
def test_valuation_age_and_staleness(last: dt.date, today: dt.date, age: int, stale: bool) -> None:
    assert valuation_age_days(last, today) == age
    assert is_stale(age, 35) is stale


# --- allocation by class ---


def test_allocation_by_class_with_percentages_and_pending() -> None:
    items = [
        (AssetClass.FIXED_INCOME, 82_226_57),
        (AssetClass.REAL_ESTATE_FUNDS, 9_480_00),
        (AssetClass.FIXED_INCOME, None),  # no valuation: left out and counted as pending
        (AssetClass.CRYPTO, None),
    ]
    result = allocate(items)
    assert result.total_cents == 91_706_57
    assert [(r.asset_class, r.value_cents) for r in result.rows] == [
        (AssetClass.FIXED_INCOME, 82_226_57),
        (AssetClass.REAL_ESTATE_FUNDS, 9_480_00),
    ]
    assert result.rows[0].share == pytest.approx(82_226_57 / 91_706_57)
    assert (
        round(result.rows[0].share * 100, 1) == 89.7
        and round(result.rows[1].share * 100, 1) == 10.3
    )
    assert result.pending_count == 2


def test_allocation_is_empty_without_values() -> None:
    result = allocate([(AssetClass.CRYPTO, None)])
    assert result.total_cents == 0 and result.rows == [] and result.pending_count == 1


# --- gross balance rule ---


def test_gross_balance_must_not_be_below_net() -> None:
    validate_gross_balance(100, None)
    validate_gross_balance(100, 100)
    validate_gross_balance(100, 120)
    with pytest.raises(DomainError) as exc:
        validate_gross_balance(100, 99)
    assert exc.value.code == "INVALID_GROSS_BALANCE"
