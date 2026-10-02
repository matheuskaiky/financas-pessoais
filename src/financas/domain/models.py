"""Domain entities (frozen dataclasses). Phase 1: manual core, checking and plain accounts."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.money import YearMonth


class TransactionKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    REFUND = "refund"
    TRANSFER = "transfer"


class CategoryKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    NEUTRAL = "neutral"


class CategoryGroup(StrEnum):
    ESSENTIAL = "essential"
    NON_ESSENTIAL = "non_essential"
    CHARGES = "charges"
    INCOME = "income"
    MOVEMENT = "movement"
    REVIEW = "review"


class StatementStatus(StrEnum):
    FUTURE = "future"
    OPEN = "open"
    CLOSED = "closed"
    PAID = "paid"


class AccountKind(StrEnum):
    CHECKING = "checking"
    CREDIT_CARD = "credit_card"
    INVESTMENT = "investment"


@dataclass(frozen=True)
class Institution:
    id: str
    slug: str
    name: str
    group_slug: str | None = None
    color: str | None = None
    image_id: str | None = None


@dataclass(frozen=True)
class Account:
    id: str
    kind: AccountKind
    institution_id: str
    nickname: str
    is_active: bool = True
    color: str | None = None
    image_id: str | None = None
    closing_day: int | None = None  # credit cards only
    due_day: int | None = None
    credit_limit_cents: int | None = None


@dataclass(frozen=True)
class Category:
    id: str
    slug: str
    name: str
    group: CategoryGroup
    kind: CategoryKind
    monthly_budget_cents: int | None = None
    color: str | None = None


@dataclass(frozen=True)
class Transaction:
    id: str
    account_id: str
    posted_on: dt.date
    kind: TransactionKind
    category_id: str
    amount_cents: int
    description: str
    description_search: str
    is_recurring: bool = False
    transfer_id: str | None = None
    notes: str | None = None
    statement_id: str | None = None  # card accounts only
    plan_id: str | None = None
    installment_number: int | None = None


@dataclass(frozen=True)
class BalanceAnchor:
    id: str
    account_id: str
    on_date: dt.date
    balance_cents: int
    note: str | None = None


@dataclass(frozen=True)
class Statement:
    """A card statement, identified by its closing month. Dates are stored at creation."""

    id: str
    account_id: str
    month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    informed_total_cents: int | None = None


@dataclass(frozen=True)
class InstallmentPlan:
    """Exists only when the purchase has more than one installment."""

    id: str
    account_id: str
    description: str
    category_id: str
    installment_total: int
    purchased_on: dt.date | None = None
