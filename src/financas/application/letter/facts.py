"""What the letter is built from: values the read queries computed. No domain logic here."""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

from financas.application.queries.summary import Summary
from financas.domain.money import YearMonth


@dataclass(frozen=True)
class CategoryFacts:
    id: str
    name: str
    goal_cents: int | None  # monthly budget goal of an expense category


@dataclass(frozen=True)
class ClosedStatementFact:
    card_name: str
    month: YearMonth
    due_date: dt.date
    outstanding_cents: int


@dataclass(frozen=True)
class ReconciliationFact:
    statement_id: str
    card_name: str
    month: YearMonth
    difference_cents: int  # informed - entered, never zero here


@dataclass(frozen=True)
class StaleValuationFact:
    account_id: str
    name: str
    age_days: int


@dataclass(frozen=True)
class PendingAccountFact:
    account_id: str
    name: str
    is_investment: bool


@dataclass(frozen=True)
class LetterFacts:
    month: YearMonth
    today: dt.date
    summary: Summary  # of ``month``
    previous: tuple[Summary, ...]  # the months before it, oldest first (up to 3)
    year_to_date: tuple[Summary, ...]  # January to ``month`` of its year, oldest first
    categories: Mapping[str, CategoryFacts]
    recurring_count: int | None  # None: the recurring view does not reach this month
    closed_statements: tuple[ClosedStatementFact, ...]  # unpaid, by due date
    cash_cents: int | None  # None: no checking account has an informed balance
    future_installments_cents: int
    uncategorized_count: int
    uncategorized_category_id: str | None
    reconciliations: tuple[ReconciliationFact, ...] = ()
    stale_valuations: tuple[StaleValuationFact, ...] = ()
    accounts_without_balance: tuple[PendingAccountFact, ...] = ()
    backup_age_days: int | None = None  # None: no backup yet
    backup_warn_days: int = 7
