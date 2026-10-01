"""Domain entities (frozen dataclasses). Phase 1: manual core, checking and plain accounts."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


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


@dataclass(frozen=True)
class BalanceAnchor:
    id: str
    account_id: str
    on_date: dt.date
    balance_cents: int
    note: str | None = None
