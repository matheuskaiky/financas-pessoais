"""Brazilian fixed-income views: liquidity, maturities, FGC exposure, emergency fund (9.6).

Pure functions. Nothing here projects maturity values, computes tax or knows a guarantee limit:
the FGC limit is a setting (``FINANCAS_FGC_LIMIT_CENTS``) the user confirms at fgc.org.br.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.models import InstrumentType, Liquidity
from financas.domain.money import YearMonth

_FGC_COVERED = {
    InstrumentType.CDB,
    InstrumentType.LC,
    InstrumentType.LCI,
    InstrumentType.LCA,
    InstrumentType.SAVINGS_ACCOUNT,
}


def suggest_fgc_covered(instrument_type: InstrumentType) -> bool:
    """The default for ``fgc_covered``; the user can change it. Not a legal statement."""
    return instrument_type in _FGC_COVERED


def available_from(
    liquidity: Liquidity, liquid_from: dt.date | None, maturity_on: dt.date | None
) -> dt.date | None:
    """When the money can be taken out: ``liquid_from`` (daily) or the maturity (at maturity).

    ``None`` (or a past date) means available today.
    """
    return liquid_from if liquidity is Liquidity.DAILY else maturity_on


class LiquidityBucket(StrEnum):
    TODAY = "today"
    WITHIN_30 = "within_30"
    WITHIN_90 = "within_90"
    WITHIN_180 = "within_180"
    WITHIN_365 = "within_365"
    LATER = "later"


_LIMITS = (
    (LiquidityBucket.WITHIN_30, 30),
    (LiquidityBucket.WITHIN_90, 90),
    (LiquidityBucket.WITHIN_180, 180),
    (LiquidityBucket.WITHIN_365, 365),
)


def bucket_for(available: dt.date | None, today: dt.date) -> LiquidityBucket:
    if available is None or available <= today:
        return LiquidityBucket.TODAY
    days = (available - today).days
    return next((b for b, limit in _LIMITS if days <= limit), LiquidityBucket.LATER)


@dataclass(frozen=True)
class BucketRow:
    bucket: LiquidityBucket
    value_cents: int


def liquidity_buckets(
    items: Iterable[tuple[int, dt.date | None]], today: dt.date
) -> list[BucketRow]:
    """Value per liquidity range: today, up to 30, 90, 180 and 365 days, later (all listed)."""
    totals = {b: 0 for b in LiquidityBucket}
    for value, available in items:
        totals[bucket_for(available, today)] += value
    return [BucketRow(b, totals[b]) for b in LiquidityBucket]


@dataclass(frozen=True)
class LadderRow:
    month: YearMonth
    value_cents: int


def maturity_ladder(items: Iterable[tuple[int, dt.date | None]]) -> list[LadderRow]:
    """Value maturing per month, oldest first; items without a maturity date are not listed."""
    totals: dict[YearMonth, int] = {}
    for value, maturity in items:
        if maturity is not None:
            month = YearMonth.from_date(maturity)
            totals[month] = totals.get(month, 0) + value
    return [LadderRow(m, v) for m, v in sorted(totals.items())]


@dataclass(frozen=True)
class FgcRow:
    group: str  # institution group (its slug when the institution has no group)
    covered_holdings_cents: int
    checking_cents: int
    exposure_cents: int
    limit_cents: int
    percent_of_limit: float

    @property
    def exceeded(self) -> bool:
        return self.exposure_cents > self.limit_cents


def fgc_exposure(
    covered_holdings: Iterable[tuple[str, int]],
    checking_balances: Iterable[tuple[str, int]],
    limit_cents: int,
) -> list[FgcRow]:
    """Per institution group: covered holdings + checking balances there, against the limit.

    The limit applies per CPF and per group and sums every covered product at that group. The
    global multi-year cap is not modeled. A group appears only when it has a covered holding.
    """
    holdings: dict[str, int] = {}
    for group, value in covered_holdings:
        holdings[group] = holdings.get(group, 0) + value
    checking: dict[str, int] = {}
    for group, balance in checking_balances:
        if group in holdings and balance > 0:  # an overdrawn account adds nothing
            checking[group] = checking.get(group, 0) + balance
    rows = [
        FgcRow(
            group,
            held,
            checking.get(group, 0),
            held + checking.get(group, 0),
            limit_cents,
            (held + checking.get(group, 0)) / limit_cents * 100 if limit_cents else 0.0,
        )
        for group, held in holdings.items()
    ]
    return sorted(rows, key=lambda r: (-r.exposure_cents, r.group))


def emergency_coverage_months(
    emergency_value_cents: int, average_essential_expenses_cents: int
) -> float | None:
    """Months the emergency fund covers: its net value ÷ the average monthly essential spending.

    ``None`` when there is no essential spending to divide by.
    """
    if average_essential_expenses_cents <= 0:
        return None
    return emergency_value_cents / average_essential_expenses_cents
