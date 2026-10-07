"""Calendar distance between today and a date, as a code (the UI writes the sentence). Pure."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class CountdownKind(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    IN_DAYS = "in_days"  # days >= 2 ahead
    PAST = "past"  # days >= 1 ago


@dataclass(frozen=True)
class Countdown:
    kind: CountdownKind
    days: int  # IN_DAYS: days ahead; PAST: days ago; otherwise 0


def countdown(target: dt.date, today: dt.date) -> Countdown:
    """Where ``target`` stands relative to ``today``: today, tomorrow, N days ahead or N ago."""
    delta = (target - today).days
    if delta == 0:
        return Countdown(CountdownKind.TODAY, 0)
    if delta == 1:
        return Countdown(CountdownKind.TOMORROW, 0)
    if delta > 1:
        return Countdown(CountdownKind.IN_DAYS, delta)
    return Countdown(CountdownKind.PAST, -delta)
