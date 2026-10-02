"""Alerts for recurring charges that disappeared, changed amount or appeared (CLAUDE.md 9.8).

Pure function over the monthly amount of each recurring item. The comparison is always the last
**closed** month against the one before it, so a month in progress never raises a false alert.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.money import YearMonth


class AlertKind(StrEnum):
    DISAPPEARED = "disappeared"  # charged the month before, not in the last closed month
    CHANGED = "changed"  # charged in both months with a different amount
    APPEARED = "appeared"  # charged in the last closed month and never before


_ORDER = {AlertKind.DISAPPEARED: 0, AlertKind.CHANGED: 1, AlertKind.APPEARED: 2}


@dataclass(frozen=True)
class RecurringAlert:
    key: str
    kind: AlertKind
    previous_cents: int | None
    current_cents: int | None


def recurring_alerts(
    amounts: Mapping[str, Mapping[YearMonth, int]], last_closed: YearMonth
) -> list[RecurringAlert]:
    """``amounts[key][month]`` is what the item cost in that month (absent: not charged)."""
    previous = last_closed.add_months(-1)
    # without any recurring data in the month before there is no baseline: in the first month of
    # use everything would "appear", which is noise
    has_baseline = any(previous in series for series in amounts.values())
    alerts: list[RecurringAlert] = []
    for key, series in amounts.items():
        before, now = series.get(previous), series.get(last_closed)
        if before is not None and now is None:
            alerts.append(RecurringAlert(key, AlertKind.DISAPPEARED, before, None))
        elif before is not None and now is not None and before != now:
            alerts.append(RecurringAlert(key, AlertKind.CHANGED, before, now))
        elif (
            has_baseline
            and now is not None
            and before is None
            and not any(m < last_closed for m in series)
        ):
            alerts.append(RecurringAlert(key, AlertKind.APPEARED, None, now))
    return sorted(alerts, key=lambda a: (_ORDER[a.kind], a.key))
