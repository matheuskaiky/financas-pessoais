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


class PaymentMethod(StrEnum):
    """How an entry was paid. The codes are the owner's vocabulary (also the CSV's): a deliberate
    exception to the English-codes rule, since "pix" and "boleto" have no English names.

    Bank accounts use the first six; ``CREDIT_CARD`` is what every card purchase carries.
    """

    PIX = "pix"
    DEBIT = "debito"
    BOLETO = "boleto"
    TRANSFER = "transferencia"  # TED, DOC to a third party
    CASH = "dinheiro"
    OTHER = "outro"
    CREDIT_CARD = "cartao_credito"


BANK_PAYMENT_METHODS = (
    PaymentMethod.PIX,
    PaymentMethod.DEBIT,
    PaymentMethod.BOLETO,
    PaymentMethod.TRANSFER,
    PaymentMethod.CASH,
    PaymentMethod.OTHER,
)


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


class AssetClass(StrEnum):
    FIXED_INCOME = "fixed_income"
    EQUITIES = "equities"
    REAL_ESTATE_FUNDS = "real_estate_funds"
    CRYPTO = "crypto"
    OTHER = "other"


class InvestmentTracking(StrEnum):
    """One level per investment account, so totals never count the same money twice (9.6)."""

    ACCOUNT = "account"
    HOLDINGS = "holdings"


class InstrumentType(StrEnum):
    CDB = "cdb"
    LC = "lc"
    LCI = "lci"
    LCA = "lca"
    CRI = "cri"
    CRA = "cra"
    DEBENTURE = "debenture"
    TREASURY_SELIC = "treasury_selic"
    TREASURY_IPCA = "treasury_ipca"
    TREASURY_PREFIXED = "treasury_prefixed"
    SAVINGS_ACCOUNT = "savings_account"
    FUND = "fund"
    PENSION = "pension"
    STOCK = "stock"
    REIT = "reit"
    ETF = "etf"
    CRYPTO = "crypto"
    OTHER = "other"


class Indexer(StrEnum):
    CDI = "cdi"
    SELIC = "selic"
    IPCA = "ipca"
    PREFIXED = "prefixed"
    OTHER = "other"


class RateMode(StrEnum):
    PERCENT_OF_INDEX = "percent_of_index"  # 110% of CDI: rate_bps = 11000
    SPREAD_OVER_INDEX = "spread_over_index"  # IPCA + 6.5%: rate_bps = 650
    FIXED_ANNUAL = "fixed_annual"  # 12.3% a year: rate_bps = 1230


class Liquidity(StrEnum):
    DAILY = "daily"
    AT_MATURITY = "at_maturity"


class HoldingStatus(StrEnum):
    ACTIVE = "active"
    REDEEMED = "redeemed"


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
    closing_days_before_due: int | None = None  # credit cards only (CLAUDE.md 9.3)
    due_day: int | None = None
    credit_limit_cents: int | None = None
    tracking: InvestmentTracking | None = None  # investment accounts only
    asset_class: AssetClass | None = None
    is_emergency_fund: bool = False


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
    category_id: str | None  # ``None`` only on an itemized expense: its items carry the categories
    amount_cents: int
    description: str
    description_search: str
    is_recurring: bool = False
    transfer_id: str | None = None
    notes: str | None = None
    statement_id: str | None = None  # card accounts only
    plan_id: str | None = None
    installment_number: int | None = None
    holding_id: str | None = None  # investment legs on a holdings-level account
    is_refunded: bool = False  # an expense that was given back: kept as a record, counted nowhere
    merchant: str | None = None  # where the money went ("Amazon", "Posto Ipiranga"), optional
    payment_method: PaymentMethod | None = None  # how it was paid; ``None``: not informed


@dataclass(frozen=True)
class TransactionSplit:
    """One item of an itemized expense: its own description and category, a positive amount.

    The items of an expense add up to its amount. The statement, the balance and the limit see
    only the parent entry; spending by category sees the items.
    """

    id: str
    transaction_id: str
    description: str
    category_id: str
    amount_cents: int


@dataclass(frozen=True)
class BalanceAnchor:
    id: str
    account_id: str
    on_date: dt.date
    balance_cents: int  # net: for an investment, what the institution would pay out today
    note: str | None = None
    gross_balance_cents: int | None = None  # investments only; estimated tax = gross - net
    holding_id: str | None = None  # a valuation of one holding (else of the whole account)


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


@dataclass(frozen=True)
class InvestmentHolding:
    """One application (a CDB, an LCI, a Treasury bond...). Contract data is display only."""

    id: str
    account_id: str
    name: str
    instrument_type: InstrumentType
    issuer_id: str  # an Institution: FGC exposure sums by its group
    indexer: Indexer | None
    rate_mode: RateMode | None
    rate_bps: int | None  # basis points: 110% of CDI = 11000, IPCA + 6.5% = 650
    applied_on: dt.date
    principal_cents: int
    maturity_on: dt.date | None
    liquidity: Liquidity
    liquid_from: dt.date | None  # end of a grace period (daily liquidity only)
    fgc_covered: bool
    is_emergency_fund: bool
    asset_class: AssetClass
    status: HoldingStatus = HoldingStatus.ACTIVE
