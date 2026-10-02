"""Phase 3b acceptance tests: liquidity buckets, maturity ladder, FGC exposure, emergency fund."""

import datetime as dt

import pytest

from financas.domain.errors import DomainError
from financas.domain.models import (
    AssetClass,
    HoldingStatus,
    Indexer,
    InstrumentType,
    InvestmentHolding,
    Liquidity,
    RateMode,
)
from financas.domain.money import YearMonth
from financas.domain.rules import validate_holding
from financas.domain.services.holdings import (
    LiquidityBucket,
    available_from,
    emergency_coverage_months,
    fgc_exposure,
    liquidity_buckets,
    maturity_ladder,
    suggest_fgc_covered,
)

D = dt.date
TODAY = D(2026, 10, 1)


# --- availability and liquidity buckets ---


@pytest.mark.parametrize(
    ("liquidity", "liquid_from", "maturity", "expected"),
    [
        (Liquidity.DAILY, None, D(2028, 3, 1), None),  # liquid today
        (Liquidity.DAILY, D(2026, 12, 1), D(2028, 3, 1), D(2026, 12, 1)),  # after the grace period
        (Liquidity.AT_MATURITY, None, D(2028, 3, 1), D(2028, 3, 1)),
        (Liquidity.AT_MATURITY, D(2026, 12, 1), D(2028, 3, 1), D(2028, 3, 1)),
    ],
)
def test_available_from(
    liquidity: Liquidity,
    liquid_from: dt.date | None,
    maturity: dt.date | None,
    expected: dt.date | None,
) -> None:
    assert available_from(liquidity, liquid_from, maturity) == expected


@pytest.mark.parametrize(
    ("available", "bucket"),
    [
        (None, LiquidityBucket.TODAY),
        (D(2026, 9, 1), LiquidityBucket.TODAY),  # past: available today
        (D(2026, 10, 1), LiquidityBucket.TODAY),
        (D(2026, 10, 2), LiquidityBucket.WITHIN_30),
        (D(2026, 10, 31), LiquidityBucket.WITHIN_30),  # 30 days
        (D(2026, 11, 1), LiquidityBucket.WITHIN_90),  # 31 days
        (D(2026, 12, 30), LiquidityBucket.WITHIN_90),  # 90 days
        (D(2026, 12, 31), LiquidityBucket.WITHIN_180),
        (D(2027, 3, 30), LiquidityBucket.WITHIN_180),  # 180 days
        (D(2027, 3, 31), LiquidityBucket.WITHIN_365),
        (D(2027, 10, 1), LiquidityBucket.WITHIN_365),  # 365 days
        (D(2027, 10, 2), LiquidityBucket.LATER),
        (D(2035, 1, 1), LiquidityBucket.LATER),
    ],
)
def test_liquidity_bucket_boundaries(available: dt.date | None, bucket: LiquidityBucket) -> None:
    rows = liquidity_buckets([(1_000, available)], TODAY)
    assert {r.bucket: r.value_cents for r in rows}[bucket] == 1_000


def test_buckets_are_listed_in_order_and_sum_values() -> None:
    items = [(500, None), (300, D(2026, 10, 15)), (200, D(2026, 10, 20)), (700, D(2028, 1, 1))]
    rows = liquidity_buckets(items, TODAY)
    assert [r.bucket for r in rows] == list(LiquidityBucket)
    assert [r.value_cents for r in rows] == [500, 500, 0, 0, 0, 700]
    assert sum(r.value_cents for r in rows) == 1_700


# --- maturity ladder ---


def test_maturity_ladder_groups_by_month_and_skips_items_without_a_date() -> None:
    items = [
        (12_720_25, D(2027, 9, 10)),
        (15_920_30, D(2028, 3, 1)),
        (1_000_00, D(2027, 9, 30)),
        (500_00, None),  # no maturity (daily liquidity, no date): not on the ladder
    ]
    ladder = maturity_ladder(items)
    assert [(str(r.month), r.value_cents) for r in ladder] == [
        ("2027-09", 13_720_25),
        ("2028-03", 15_920_30),
    ]
    assert maturity_ladder([]) == []


# --- FGC exposure per institution group ---


def test_fgc_exposure_sums_covered_holdings_and_checking_per_group() -> None:
    covered = [("inter", 29_960_55 - 1_320_00), ("inter", 0), ("bb", 100_000_00)]
    checking = [("inter", 1_320_00), ("bb", -50_00)]  # an overdrawn balance adds nothing
    rows = fgc_exposure(covered, checking, limit_cents=250_000_00)
    by_group = {r.group: r for r in rows}
    assert by_group["inter"].exposure_cents == 29_960_55
    assert (
        by_group["inter"].covered_holdings_cents == 28_640_55
        and by_group["inter"].checking_cents == 1_320_00
    )
    assert round(by_group["inter"].percent_of_limit, 1) == 12.0
    assert by_group["bb"].exposure_cents == 100_000_00
    assert [r.group for r in rows] == ["bb", "inter"]  # biggest exposure first
    assert not by_group["inter"].exceeded and not by_group["bb"].exceeded


def test_fgc_exposure_flags_a_group_above_the_limit() -> None:
    (row,) = fgc_exposure([("x", 260_000_00)], [], limit_cents=250_000_00)
    assert row.exceeded and row.percent_of_limit > 100


def test_fgc_exposure_is_empty_without_covered_items() -> None:
    assert fgc_exposure([], [("bb", 1_000)], limit_cents=250_000_00) == []


def test_fgc_suggestions_by_instrument_type() -> None:
    covered = {
        InstrumentType.CDB, InstrumentType.LC, InstrumentType.LCI, InstrumentType.LCA,
        InstrumentType.SAVINGS_ACCOUNT,
    }  # fmt: skip
    for kind in InstrumentType:
        assert suggest_fgc_covered(kind) is (kind in covered), kind


# --- emergency fund coverage ---


def test_emergency_coverage_in_months() -> None:
    # R$ 24.926,15 ÷ R$ 4.150,00 = 6,0 months (design board example)
    months = emergency_coverage_months(24_926_15, 4_150_00)
    assert months is not None and round(months, 1) == 6.0


def test_emergency_coverage_needs_essential_spending() -> None:
    assert emergency_coverage_months(100_000, 0) is None
    assert emergency_coverage_months(0, 1_000) == 0.0


# --- holding rules ---


def holding(**kw: object) -> InvestmentHolding:
    base: dict[str, object] = {
        "id": "h" * 32,
        "account_id": "a" * 32,
        "name": "CDB Banco X 110% CDI",
        "instrument_type": InstrumentType.CDB,
        "issuer_id": "i" * 32,
        "indexer": Indexer.CDI,
        "rate_mode": RateMode.PERCENT_OF_INDEX,
        "rate_bps": 11_000,
        "applied_on": D(2026, 3, 1),
        "principal_cents": 1_000_000,
        "maturity_on": D(2028, 3, 1),
        "liquidity": Liquidity.AT_MATURITY,
        "liquid_from": None,
        "fgc_covered": True,
        "is_emergency_fund": False,
        "asset_class": AssetClass.FIXED_INCOME,
        "status": HoldingStatus.ACTIVE,
    }
    base.update(kw)
    return InvestmentHolding(**base)  # type: ignore[arg-type]


def test_a_valid_holding_passes() -> None:
    validate_holding(holding())
    validate_holding(
        holding(
            instrument_type=InstrumentType.STOCK,
            indexer=None,
            rate_mode=None,
            rate_bps=None,
            maturity_on=None,
            liquidity=Liquidity.DAILY,
            fgc_covered=False,
        )
    )
    validate_holding(
        holding(indexer=Indexer.PREFIXED, rate_mode=RateMode.FIXED_ANNUAL, rate_bps=1_230)
    )
    validate_holding(
        holding(indexer=Indexer.IPCA, rate_mode=RateMode.SPREAD_OVER_INDEX, rate_bps=650)
    )
    validate_holding(
        holding(liquidity=Liquidity.DAILY, liquid_from=D(2026, 9, 1), maturity_on=None)
    )


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"name": "  "}, "EMPTY_NAME"),
        ({"principal_cents": 0}, "AMOUNT_NOT_POSITIVE"),
        ({"rate_bps": None}, "INVALID_RATE"),
        ({"rate_mode": None}, "INVALID_RATE"),
        ({"rate_bps": -1}, "INVALID_RATE"),
        ({"indexer": None}, "INVALID_RATE"),  # percent of an index needs the index
        ({"indexer": Indexer.PREFIXED}, "INVALID_RATE"),  # ...and it cannot be "prefixed"
        ({"rate_mode": RateMode.FIXED_ANNUAL, "indexer": Indexer.CDI}, "INVALID_RATE"),
        ({"maturity_on": None, "liquidity": Liquidity.AT_MATURITY}, "MATURITY_REQUIRED"),
        ({"maturity_on": D(2026, 2, 1)}, "INVALID_MATURITY"),
        ({"liquid_from": D(2026, 9, 1), "liquidity": Liquidity.AT_MATURITY}, "INVALID_LIQUIDITY"),
        ({"liquidity": Liquidity.DAILY, "liquid_from": D(2026, 2, 1)}, "INVALID_LIQUIDITY"),
    ],
)
def test_invalid_holdings(overrides: dict[str, object], code: str) -> None:
    with pytest.raises(DomainError) as exc:
        validate_holding(holding(**overrides))
    assert exc.value.code == code


def test_ladder_month_type() -> None:
    assert isinstance(maturity_ladder([(1, D(2027, 1, 1))])[0].month, YearMonth)
