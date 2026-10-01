"""Balance at a date from informed anchors and movements (section 9.7)."""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class AnchorPoint:
    on_date: dt.date
    balance_cents: int


def balance_on(
    anchors: Iterable[AnchorPoint],
    movements: Iterable[tuple[dt.date, int]],
    day: dt.date,
) -> int | None:
    """Nearest anchor ± the movements between it and ``day``; ``None`` without any anchor.

    An anchor is the balance at the end of its date: movements dated on it are already inside.
    A tie between two anchors goes to the earlier one.
    """
    nearest = min(anchors, key=lambda a: (abs((a.on_date - day).days), a.on_date), default=None)
    if nearest is None:
        return None
    moves = list(movements)
    if nearest.on_date <= day:
        delta = sum(c for d, c in moves if nearest.on_date < d <= day)
        return nearest.balance_cents + delta
    delta = sum(c for d, c in moves if day < d <= nearest.on_date)
    return nearest.balance_cents - delta
