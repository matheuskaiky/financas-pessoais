"""The letter as data: slots, notes, sentences, sections and the charts of the margin."""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.money import YearMonth


class SlotKind(StrEnum):
    MONEY = "money"  # int cents, never negative here: the sentence says which way it goes
    SIGNED_MONEY = "signed_money"  # int cents with a sign
    PERCENT = "percent"  # a ratio: 0.5 is 50,0%
    COUNT = "count"
    DAYS = "days"
    DATE = "date"  # dd/mm
    MONTH = "month"  # the month name ("setembro")
    MONTHS = "months"  # several months, abbreviated ("jun, jul e ago")
    NAME = "name"  # a category, card or account name (user data, shown as typed)
    NAMES = "names"  # several names ("Casa e Supermercado")
    SIGNAL = "signal"  # a qualitative code the wording maps to a word ("below", "above")


type SlotValue = int | float | str | dt.date | YearMonth | tuple[str, ...] | tuple[YearMonth, ...]


@dataclass(frozen=True)
class Slot:
    kind: SlotKind
    value: SlotValue


@dataclass(frozen=True)
class Note:
    """ "À margem": the calculation behind a sentence. ``key`` selects the wording of the rows; the
    numbers come from ``slots`` (the same kinds as in the text)."""

    key: str
    slots: Mapping[str, Slot]


@dataclass(frozen=True)
class Sentence:
    code: str  # key of the template in the message catalogue
    slots: Mapping[str, Slot]
    note: Note | None = None


@dataclass(frozen=True)
class Section:
    id: str  # panorama, where, agreed, ahead, before
    sentences: tuple[Sentence, ...]


@dataclass(frozen=True)
class SparkBar:
    month: YearMonth
    cents: int
    is_current: bool  # the letter's own month


@dataclass(frozen=True)
class CategoryBar:
    category_id: str
    name: str
    cents: int


@dataclass(frozen=True)
class GoalChart:
    """Spending of one category over the last months against its monthly goal."""

    category_id: str
    name: str
    goal_cents: int
    months: tuple[YearMonth, ...]
    spending_cents: tuple[int, ...]


@dataclass(frozen=True)
class CashStep:
    """Cash before and after each closed statement is paid."""

    code: str  # "today" or "after_statement"
    name: str | None
    on: dt.date | None
    cents: int


class TodoTarget(StrEnum):
    UNCATEGORIZED_ENTRIES = "uncategorized_entries"
    STATEMENT = "statement"
    ACCOUNTS = "accounts"
    INVESTMENTS = "investments"
    BACKUP = "backup"


@dataclass(frozen=True)
class Todo:
    code: str  # difference, uncategorized, stale_valuation, no_balance, backup_old, backup_none
    slots: Mapping[str, Slot]
    target: TodoTarget
    target_id: str | None = None


@dataclass(frozen=True)
class Letter:
    month: YearMonth
    written_on: dt.date
    has_entries: bool
    balance_cents: int
    savings_rate: float | None
    entry_count: int
    average_signal: str | None  # "below" | "above" | "equal" | None (no months to compare)
    average_difference_cents: int
    sections: tuple[Section, ...]
    spark: tuple[SparkBar, ...]
    spark_average_cents: int | None
    top_categories: tuple[CategoryBar, ...]
    goal_chart: GoalChart | None
    cash_steps: tuple[CashStep, ...]
    todos: tuple[Todo, ...]
    fingerprint: str  # short hash of the totals: changes when an entry of the month changes
    signal_count: int
