"""Data types of the one-off spreadsheet import (CLAUDE.md 13.1). Pure data, no I/O.

``LegacyRow`` is one line of the old workbook, already normalised by the reader into codes and
cents; the planner turns the rows into ``PlannedAction`` objects; the apply step runs them through
the existing use cases. Nothing here knows Portuguese: source-specific wording (statement payment,
Pix, the investment sweep) is recognised by the reader and handed over as a ``TransferHint``.
"""

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum

from financas.domain.models import AccountKind, AssetClass
from financas.domain.money import YearMonth


class Origin(StrEnum):
    CHECKING = "checking"
    CARD = "card"


class RowKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    REFUND = "refund"
    TRANSFER = "transfer"


class TransferHint(StrEnum):
    """What the reader recognised in the description of a ``transfer`` row."""

    NONE = "none"
    STATEMENT_PAYMENT = "statement_payment"  # paying a card statement (either side)
    SWEEP = "sweep"  # the automatic investment of idle checking balance (BB Rende Fácil)
    PIX = "pix"  # a Pix or transfer with a counterparty


@dataclass(frozen=True)
class LegacyRow:
    """One row of ``fato_transacoes``. ``amount_cents`` keeps the sheet's sign."""

    row_no: int  # spreadsheet row number, for messages
    sheet_id: str  # ``id_transacao``
    date: dt.date
    statement: YearMonth | None  # ``fatura_ref`` (cards)
    institution: str  # raw name, a data value
    origin: Origin
    kind: RowKind
    category: str  # raw name, a data value
    recurring: bool
    description: str
    place: str | None
    amount_cents: int
    installment_number: int | None = None
    installment_total: int | None = None
    transfer_hint: TransferHint = TransferHint.NONE
    counterparty: str = ""  # normalised key (casefolded, no accents); "" when unknown


@dataclass(frozen=True)
class LegacyAccountRow:
    """One line of ``dim_contas``, raw: the planner never relies on it being filled in."""

    institution: str
    origin: str
    nickname: str
    credit_limit_cents: int | None  # ``PREENCHER`` and empty cells become ``None``
    opening_balance_cents: int | None


@dataclass(frozen=True)
class LegacyWorkbook:
    rows: list[LegacyRow]
    accounts: list[LegacyAccountRow]
    categories: list[str]


@dataclass(frozen=True)
class AccountSpec:
    """An account to create (or reuse, matched by nickname) before the entries go in."""

    key: str  # stable ASCII key used by the plan, e.g. ``bb_checking``
    kind: AccountKind
    institution: str  # name of the institution to create or reuse
    nickname: str
    legacy_institution: str | None = None  # name as written in the workbook rows
    due_day: int | None = None  # cards
    closes_before_due: int | None = None  # cards
    limit_cents: int | None = None  # cards; ``None`` = not informed
    opening_cents: int | None = None  # informed balance (not cards)
    opening_on: dt.date | None = None
    asset_class: AssetClass | None = None  # investment accounts
    institution_group: str | None = None  # FGC group


class CounterpartyDecision(StrEnum):
    OWN = "own"  # an account of the holder: a transfer
    THIRD_PARTY = "third_party"  # provisional expense/income in ``uncategorized``/``other_income``


@dataclass(frozen=True)
class ImportConfig:
    """Everything the planner needs besides the rows. The reviewed CSV files fill it."""

    year: int
    accounts: tuple[AccountSpec, ...]
    category_map: dict[str, str]  # raw category name -> slug
    category_kinds: dict[str, str]  # slug -> "expense" | "income" | "neutral"
    holder_aliases: tuple[str, ...] = ()  # normalised names of the holder
    # per counterparty key: "own", "third_party" or "category:<slug>"
    counterparty_decisions: dict[str, str] = field(default_factory=lambda: {})
    # per sheet id overrides coming from the reviewed entries file
    skip_ids: frozenset[str] = frozenset()
    category_overrides: dict[str, str] = field(default_factory=lambda: {})
    # key of the account that receives the sweep movements, and the legacy checking it comes from
    sweep_account_key: str | None = None


class Flag(StrEnum):
    """Why an entry needs the user's eye (listed in the review file)."""

    KIND_CATEGORY_MISMATCH = "kind_category_mismatch"
    STATEMENT_DISAGREES = "statement_disagrees"  # the sheet's statement differs from the rule
    PROVISIONAL = "provisional"  # third-party Pix kept as an expense/income to review
    UNMATCHED_PAYMENT = "unmatched_payment"
    UNMATCHED_OWN_TRANSFER = "unmatched_own_transfer"  # one leg only
    PLAN_GAP = "plan_gap"  # an installment between two present ones is missing in the sheet
    PLAN_OVERLAP = "plan_overlap"  # two plans of the same item cover the same statements
    PAYMENT_ABOVE_OUTSTANDING = "payment_above_outstanding"
    NO_CARD_FOR_PAYMENT = "no_card_for_payment"


class IssueLevel(StrEnum):
    ERROR = "error"  # the plan cannot be applied
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    level: IssueLevel
    code: str  # e.g. UNKNOWN_CATEGORY (the interface translates)
    sheet_id: str | None = None
    detail: str = ""  # a data value (a category name, an account key...)


@dataclass(frozen=True)
class EntryAction:
    """An income, expense or refund on a checking account or a card (single payment)."""

    sheet_id: str
    date: dt.date
    account_key: str
    kind: RowKind  # expense | income | refund
    amount_cents: int  # positive magnitude
    description: str
    category_slug: str
    recurring: bool
    place: str | None = None
    statement: YearMonth | None = None  # cards: the statement written in the sheet
    original_category: str | None = None  # set when the category was replaced (mismatch)
    flags: tuple[Flag, ...] = ()


@dataclass(frozen=True)
class TransferAction:
    """A transfer with one or two legs (``None`` = an account the system does not track)."""

    sheet_ids: tuple[str, ...]
    date: dt.date
    from_key: str | None
    to_key: str | None
    amount_cents: int  # positive
    description: str
    flags: tuple[Flag, ...] = ()


@dataclass(frozen=True)
class PlanInstallment:
    number: int
    sheet_id: str | None  # ``None`` = generated (not in the sheet)
    amount_cents: int
    statement: YearMonth


@dataclass(frozen=True)
class PlanAction:
    """An installment purchase: the missing installments are generated (9.4)."""

    account_key: str
    description: str
    category_slug: str
    recurring: bool
    total_installments: int
    first_number: int  # the first installment that exists in the sheet
    purchased_on: dt.date | None  # known only when installment 1 is in the sheet
    first_statement: YearMonth  # statement of ``first_number``
    installment_cents: int  # amount given to the generated ones
    installments: tuple[PlanInstallment, ...]  # present ones, with the sheet's amounts
    original_category: str | None = None
    flags: tuple[Flag, ...] = ()


@dataclass(frozen=True)
class PaymentAction:
    """A statement payment: a transfer from a checking account to a card, linked to a statement."""

    sheet_ids: tuple[str, ...]
    date: dt.date
    card_key: str
    from_key: str | None  # ``None``: only the card leg is in the sheet
    amount_cents: int
    statement: YearMonth | None  # chosen by the planner; ``None`` when no statement exists
    flags: tuple[Flag, ...] = ()


PlannedAction = EntryAction | TransferAction | PlanAction | PaymentAction


@dataclass(frozen=True)
class CounterpartyLine:
    """One line of the counterparty review file."""

    key: str
    count: int
    out_cents: int
    in_cents: int
    decision: str  # the one in force: "own" | "third_party" | "category:<slug>"
    suggested: str = ""  # what the planner would choose without the user's decision


@dataclass(frozen=True)
class ImportPlan:
    accounts: tuple[AccountSpec, ...]
    actions: tuple[PlannedAction, ...]
    counterparties: tuple[CounterpartyLine, ...]
    categories_used: dict[str, int]  # raw category name -> rows
    issues: tuple[Issue, ...]
    scope_rows: int  # rows of the workbook inside the scope
    skipped_rows: int  # rows outside the scope (other years, other statements)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.level is IssueLevel.ERROR)
