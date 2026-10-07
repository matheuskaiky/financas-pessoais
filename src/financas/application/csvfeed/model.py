"""Data of the CSV feed (CLAUDE.md 13.3): columns, raw and typed rows, context, plan.

Nothing here does I/O or writes Portuguese. Problems are ``FeedIssue`` values: a stable ASCII code
(the same string as the matching ``DomainError`` code when the domain has one), the line of the
file, the column and parameters. ``interfaces/`` turns them into pt-BR sentences.
"""

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum

from financas.domain.models import (
    AccountKind,
    CategoryKind,
    InvestmentTracking,
    PaymentMethod,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import AssignmentReason

MAX_DATA_ROWS = 20_000
MAX_DESCRIPTION_LENGTH = 300
MAX_NOTES_LENGTH = 2_000
MAX_MERCHANT_LENGTH = 120
MAX_AMOUNT_CENTS = 10**12  # exclusive
MAX_DAYS_AHEAD = 366
MIN_DATE = dt.date(1900, 1, 1)


class FeedColumn(StrEnum):
    DATE = "date"
    KIND = "kind"
    ACCOUNT = "account"
    TO_ACCOUNT = "to_account"
    AMOUNT = "amount"
    DESCRIPTION = "description"
    CATEGORY = "category"
    RECURRING = "recurring"
    NOTES = "notes"
    STATEMENT = "statement"
    INSTALLMENTS = "installments"
    INSTALLMENT_NUMBER = "installment_number"
    AMOUNT_TYPE = "amount_type"
    GROSS_AMOUNT = "gross_amount"
    PAYMENT_METHOD = "payment_method"  # optional: pix, debito, boleto, ... (blank: inferred)
    MERCHANT = "merchant"  # optional (v1.1.0): where the money went
    REFUNDED = "refunded"  # optional: an expense kept on record but counted nowhere
    GROUP = "group"  # optional: rows sharing it are ONE itemized purchase (parent row + items)


COLUMN_ORDER: tuple[FeedColumn, ...] = tuple(FeedColumn)
REQUIRED_COLUMNS = (FeedColumn.DATE, FeedColumn.KIND, FeedColumn.ACCOUNT, FeedColumn.AMOUNT)


class FeedKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    REFUND = "refund"
    TRANSFER = "transfer"
    BALANCE = "balance"


class AmountType(StrEnum):
    TOTAL = "total"
    INSTALLMENT = "installment"


@dataclass(frozen=True)
class FeedIssue:
    """An error or a warning: ``line`` is the line of the file (1-based), ``None`` for the file."""

    code: str
    line: int | None = None
    column: str | None = None
    params: dict[str, str | int] = field(default_factory=lambda: {})


@dataclass(frozen=True)
class RawRow:
    """Cells of one data row, trimmed and NFC-normalised, keyed by column (blank = ``""``)."""

    line: int
    values: dict[FeedColumn, str]

    def get(self, column: FeedColumn) -> str:
        return self.values.get(column, "")


@dataclass(frozen=True)
class ParsedTable:
    delimiter: str
    columns: tuple[FeedColumn, ...]
    rows: tuple[RawRow, ...]
    issues: tuple[FeedIssue, ...]


@dataclass(frozen=True)
class FeedSplit:
    """One item of an itemized purchase: line, description, category reference and amount."""

    line: int
    description: str
    category: str  # name or slug as typed
    amount_cents: int  # magnitude


@dataclass(frozen=True)
class FeedRow:
    """A row that passed the syntax checks. ``amount_cents`` is the magnitude (balance: signed)."""

    line: int
    kind: FeedKind
    date: dt.date
    account: str  # nickname as typed ("" only for a transfer leg that is not tracked)
    to_account: str
    amount_cents: int
    description: str
    category: str
    recurring: bool
    notes: str
    statement: YearMonth | None
    installments: int | None
    installment_number: int
    amount_type: AmountType | None
    gross_cents: int | None
    merchant: str = ""
    refunded: bool = False
    group: str = ""  # the shared id of an itemized purchase ("" for a flat row)
    splits: tuple[FeedSplit, ...] = ()  # the items of an itemized purchase (this row is its parent)
    payment_method: PaymentMethod | None = None  # as typed; blank is filled by the planner


# ---- what the database holds (loaded by the adapter, never queried by the planner) -----------


@dataclass(frozen=True)
class AccountInfo:
    id: str
    nickname: str
    kind: AccountKind
    is_active: bool
    due_day: int | None = None
    closing_days_before_due: int | None = None
    tracking: InvestmentTracking | None = None


@dataclass(frozen=True)
class CategoryInfo:
    id: str
    slug: str
    name: str
    kind: CategoryKind


@dataclass(frozen=True)
class StatementInfo:
    """A stored statement: its frozen dates, whether it is paid history, and its amounts."""

    account_id: str
    month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    is_locked: bool
    total_cents: int  # purchases minus refunds
    paid_cents: int


@dataclass(frozen=True)
class FeedContext:
    today: dt.date
    accounts: tuple[AccountInfo, ...]
    categories: tuple[CategoryInfo, ...]
    statements: tuple[StatementInfo, ...] = ()
    anchors: frozenset[tuple[str, dt.date]] = frozenset()  # (account id, date) already informed


# ---- the plan --------------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementChoice:
    """The statement an entry lands on and why (the UI turns it into a sentence)."""

    month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    reason: AssignmentReason
    cycle_month: (
        YearMonth | None
    )  # what the card cycle gives for the date; ``None``: not comparable
    exists: bool  # already in the database (else this file creates it)


@dataclass(frozen=True)
class SplitAction:
    description: str
    category_id: str
    amount_cents: int  # magnitude


@dataclass(frozen=True)
class EntryAction:
    line: int
    account_id: str
    kind: TransactionKind  # expense, income or refund
    posted_on: dt.date
    amount_cents: int  # magnitude
    description: str
    category_id: str | None  # ``None`` on an itemized purchase
    recurring: bool
    notes: str | None
    statement: StatementChoice | None  # card entries only
    merchant: str | None = None  # already normalised (canonical name), or ``None``
    refunded: bool = False
    splits: tuple[SplitAction, ...] = ()  # an itemized purchase: the entry has no category
    payment_method: PaymentMethod | None = None  # typed, or inferred (card, wording, else PIX)


@dataclass(frozen=True)
class InstallmentLine:
    number: int
    month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    amount_cents: int
    posted_on: dt.date


@dataclass(frozen=True)
class PurchaseAction:
    """A card purchase in installments: one plan and every installment from ``first_number`` on."""

    line: int
    account_id: str
    purchased_on: dt.date
    description: str
    category_id: str | None  # ``None`` when the purchase is itemized
    notes: str | None
    installments: int
    first_number: int
    amount_type: AmountType
    total_cents: int | None
    installment_cents: int | None
    statement: StatementChoice  # of the first generated installment
    explicit_statement: YearMonth | None
    lines: tuple[InstallmentLine, ...]
    merchant: str | None = None
    splits: tuple[SplitAction, ...] = ()  # items of the whole purchase (see ``distribute_items``)


@dataclass(frozen=True)
class TransferAction:
    line: int
    from_id: str | None
    to_id: str | None
    posted_on: dt.date
    amount_cents: int
    description: str
    notes: str | None


@dataclass(frozen=True)
class PaymentAction:
    line: int
    card_id: str
    from_id: str | None
    month: YearMonth
    paid_on: dt.date
    amount_cents: int
    description: str  # "" when the file gave none (the adapter supplies the stored text)


@dataclass(frozen=True)
class BalanceAction:
    line: int
    account_id: str
    on_date: dt.date
    balance_cents: int
    gross_cents: int | None
    note: str | None


FeedAction = EntryAction | PurchaseAction | TransferAction | PaymentAction | BalanceAction


@dataclass(frozen=True)
class AccountTotal:
    """What the plan promises for one account: transactions created and their signed sum."""

    account_id: str
    count: int
    sum_cents: int


@dataclass(frozen=True)
class StatementTouch:
    """A statement that receives entries or payments from the file."""

    account_id: str
    month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    exists: bool
    entries: int  # purchases, installments and refunds
    payments: int
    owed_cents: int  # purchases minus refunds coming from the file
    paid_cents: int  # payments coming from the file
    signed_sum_cents: int  # sum of the amounts of every transaction linked to it (verification)


@dataclass(frozen=True)
class FeedPlan:
    actions: tuple[FeedAction, ...]
    warnings: tuple[FeedIssue, ...]
    rows_by_kind: dict[FeedKind, int]
    account_totals: tuple[AccountTotal, ...]
    statements: tuple[StatementTouch, ...]
    reasons: dict[AssignmentReason, int]
    duplicate_rows: int
    itemized: int = 0  # purchases (flat or installments) entered with items
    refunded: int = 0  # expenses entered as refunded purchases

    @property
    def transactions(self) -> int:
        return sum(t.count for t in self.account_totals)

    @property
    def purchases(self) -> int:
        return sum(1 for a in self.actions if isinstance(a, PurchaseAction))

    @property
    def payments(self) -> int:
        return sum(1 for a in self.actions if isinstance(a, PaymentAction))

    @property
    def balances(self) -> int:
        return sum(1 for a in self.actions if isinstance(a, BalanceAction))


@dataclass(frozen=True)
class FeedAnalysis:
    """Everything the adapter needs: the issues (no plan when there are any) or the plan."""

    delimiter: str
    sha256: str
    data_rows: int
    issues: tuple[FeedIssue, ...]
    plan: FeedPlan | None

    @property
    def ok(self) -> bool:
        return not self.issues and self.plan is not None
