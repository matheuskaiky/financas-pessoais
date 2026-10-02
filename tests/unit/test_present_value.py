from decimal import Decimal

import pytest

from financas.domain.errors import DomainError
from financas.domain.services.present_value import (
    annual_rate,
    implied_monthly_rate,
    present_value,
)


def test_zero_rate_is_the_plain_sum() -> None:
    assert present_value([(10, 100), (40, 250)], Decimal(0)) == 350


def test_one_month_at_one_percent() -> None:
    assert present_value([(30, 10100)], Decimal("0.01")) == 10000


def test_two_months_compound_and_round_to_the_cent() -> None:
    assert present_value([(60, 10201)], Decimal("0.01")) == 10000
    assert present_value([(15, 1000)], Decimal("0.01")) == 995  # 1000 / 1.01 ** 0.5 = 995.04


def test_negative_rate_and_days_are_refused() -> None:
    with pytest.raises(DomainError):
        present_value([(10, 100)], Decimal("-0.01"))
    with pytest.raises(DomainError):
        present_value([(-1, 100)], Decimal("0.01"))


def test_implied_rate_recovers_a_known_rate() -> None:
    rate = Decimal("0.02")
    payments = [(30 * k, 10000) for k in range(1, 7)]
    price = present_value(payments, rate)
    assert abs(implied_monthly_rate(price, payments) - rate) < Decimal("0.0001")


def test_no_interest_when_the_plan_costs_no_more_than_the_price() -> None:
    assert implied_monthly_rate(100000, [(30, 50000), (60, 50000)]) == 0
    assert implied_monthly_rate(100000, [(30, 40000), (60, 50000)]) == 0


def test_implied_rate_with_real_dates_is_positive_when_the_plan_costs_more() -> None:
    payments = [(35 + 30 * k, 45000) for k in range(10)]
    rate = implied_monthly_rate(420000, payments)
    assert 0 < rate < Decimal("0.05")
    assert abs(present_value(payments, rate) - 420000) <= 5


def test_annual_equivalent() -> None:
    assert annual_rate(Decimal(0)) == 0
    assert abs(annual_rate(Decimal("0.01")) - Decimal("0.126825")) < Decimal("0.000001")
