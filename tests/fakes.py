"""In-memory implementations of the ports, for unit tests and contract tests."""

import copy
import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import replace
from types import TracebackType
from typing import Self

from financas.domain.models import (
    Account,
    BalanceAnchor,
    Category,
    InstallmentPlan,
    Institution,
    InvestmentHolding,
    Statement,
    Transaction,
    TransactionKind,
    TransactionSplit,
)
from financas.domain.money import YearMonth
from financas.domain.services.images import detect_image_type


class FixedClock:
    def __init__(self, today: dt.date = dt.date(2026, 7, 25)) -> None:
        self._today = today

    def today(self) -> dt.date:
        return self._today

    def now(self) -> dt.datetime:
        return dt.datetime.combine(self._today, dt.time(12, 0))


class MemoryInstitutions:
    def __init__(self) -> None:
        self.items: dict[str, Institution] = {}

    def add(self, institution: Institution) -> None:
        self.items[institution.id] = institution

    def update(self, institution: Institution) -> None:
        self.items[institution.id] = institution

    def get(self, institution_id: str) -> Institution | None:
        return self.items.get(institution_id)

    def get_by_slug(self, slug: str) -> Institution | None:
        return next((i for i in self.items.values() if i.slug == slug), None)

    def list_all(self) -> list[Institution]:
        return list(self.items.values())


class MemoryAccounts:
    def __init__(self) -> None:
        self.items: dict[str, Account] = {}

    def add(self, account: Account) -> None:
        self.items[account.id] = account

    def update(self, account: Account) -> None:
        self.items[account.id] = account

    def get(self, account_id: str) -> Account | None:
        return self.items.get(account_id)

    def list_all(self) -> list[Account]:
        return list(self.items.values())


class MemoryCategories:
    def __init__(self) -> None:
        self.items: dict[str, Category] = {}

    def add(self, category: Category) -> None:
        self.items[category.id] = category

    def update(self, category: Category) -> None:
        self.items[category.id] = category

    def get(self, category_id: str) -> Category | None:
        return self.items.get(category_id)

    def get_by_slug(self, slug: str) -> Category | None:
        return next((c for c in self.items.values() if c.slug == slug), None)

    def list_all(self) -> list[Category]:
        return list(self.items.values())


class MemorySplits:
    """The items of itemized expenses, by parent id (its own ``items`` so the unit of work can
    snapshot and restore it like every repository)."""

    def __init__(self) -> None:
        self.items: dict[str, list[TransactionSplit]] = {}


class MemoryTransactions:
    def __init__(self, statements: "MemoryStatements", splits: MemorySplits | None = None) -> None:
        self.items: dict[str, Transaction] = {}
        self.statements = statements
        self.split_store = splits or MemorySplits()

    def set_splits(self, transaction_id: str, splits: Sequence[TransactionSplit]) -> None:
        if splits:
            self.split_store.items[transaction_id] = list(splits)
        else:
            self.split_store.items.pop(transaction_id, None)

    def splits_for(self, transaction_ids: Iterable[str]) -> dict[str, list[TransactionSplit]]:
        return {
            i: list(self.split_store.items[i])
            for i in transaction_ids
            if i in self.split_store.items
        }

    def add_many(self, transactions: Sequence[Transaction]) -> None:
        for transaction in transactions:
            self.items[transaction.id] = transaction

    def get(self, transaction_id: str) -> Transaction | None:
        return self.items.get(transaction_id)

    def delete(self, transaction_id: str) -> None:
        self.items.pop(transaction_id, None)
        self.split_store.items.pop(transaction_id, None)  # ON DELETE CASCADE

    def list_by_transfer(self, transfer_id: str) -> list[Transaction]:
        return [t for t in self.items.values() if t.transfer_id == transfer_id]

    def list_between(
        self,
        start: dt.date,
        end: dt.date,
        account_id: str | None = None,
        include_refunded: bool = False,
    ) -> list[Transaction]:
        rows = [
            (n, t)
            for n, t in enumerate(self.items.values())
            if start <= t.posted_on <= end
            and (account_id is None or t.account_id == account_id)
            and (include_refunded or not t.is_refunded)
        ]
        rows.sort(key=lambda r: (r[1].posted_on, r[0]), reverse=True)
        return [t for _, t in rows]

    def list_for_competence(
        self, start: dt.date, end: dt.date, include_refunded: bool = False
    ) -> list[Transaction]:
        months = {s.id: s.month for s in self.statements.items.values()}
        first, last = YearMonth.from_date(start), YearMonth.from_date(end)
        rows = []
        for n, t in enumerate(self.items.values()):
            if t.is_refunded and not include_refunded:
                continue
            if t.statement_id is not None:
                counts = first <= months[t.statement_id] <= last
            else:
                counts = start <= t.posted_on <= end
            if counts:
                rows.append((n, t))
        rows.sort(key=lambda r: (r[1].posted_on, r[0]), reverse=True)
        return [t for _, t in rows]

    def list_by_account(self, account_id: str, include_refunded: bool = False) -> list[Transaction]:
        return self._sorted(
            t
            for t in self.items.values()
            if t.account_id == account_id and (include_refunded or not t.is_refunded)
        )

    def list_by_statement(
        self, statement_id: str, include_refunded: bool = False
    ) -> list[Transaction]:
        return self._sorted(
            t
            for t in self.items.values()
            if t.statement_id == statement_id and (include_refunded or not t.is_refunded)
        )

    def list_by_plan(self, plan_id: str, include_refunded: bool = False) -> list[Transaction]:
        rows = [
            t
            for t in self.items.values()
            if t.plan_id == plan_id and (include_refunded or not t.is_refunded)
        ]
        return sorted(rows, key=lambda t: t.installment_number or 0)

    def update(self, transaction: Transaction) -> None:
        self.items[transaction.id] = transaction

    def count_pending_review(self, uncategorized_category_id: str | None) -> int:
        return sum(
            1
            for t in self.items.values()
            if t.kind is TransactionKind.EXPENSE
            and not t.is_refunded
            and t.transfer_id is None
            and (not t.merchant or t.category_id == uncategorized_category_id)
        )

    def merchant_counts(self, account_id: str | None = None) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for t in self.items.values():
            if t.merchant and (account_id is None or t.account_id == account_id):
                counts[t.merchant] = counts.get(t.merchant, 0) + 1
        return list(counts.items())

    def update_amount(self, transaction_id: str, amount_cents: int) -> None:
        self.items[transaction_id] = replace(self.items[transaction_id], amount_cents=amount_cents)

    def set_statement(self, transaction_id: str, statement_id: str) -> None:
        self.items[transaction_id] = replace(self.items[transaction_id], statement_id=statement_id)

    @staticmethod
    def _sorted(rows: Iterable[Transaction]) -> list[Transaction]:
        indexed = list(enumerate(rows))
        indexed.sort(key=lambda r: (r[1].posted_on, r[0]), reverse=True)
        return [t for _, t in indexed]

    def movements(self, account_id: str) -> list[tuple[dt.date, int]]:
        return [
            (t.posted_on, t.amount_cents)
            for t in self.items.values()
            if t.account_id == account_id and not t.is_refunded
        ]

    def movements_for_holding(self, holding_id: str) -> list[tuple[dt.date, int]]:
        return [
            (t.posted_on, t.amount_cents) for t in self.items.values() if t.holding_id == holding_id
        ]

    def last_category_id(self, description_search: str, kind: TransactionKind) -> str | None:
        matches = [
            t
            for t in self.items.values()
            if t.description_search == description_search
            and t.kind is kind
            and t.category_id is not None
        ]
        if not matches:
            return None
        return max(enumerate(matches), key=lambda r: (r[1].posted_on, r[0]))[1].category_id


class MemoryStatements:
    def __init__(self) -> None:
        self.items: dict[str, Statement] = {}

    def add(self, statement: Statement) -> None:
        if self.get_by_card_month(statement.account_id, statement.month) is not None:
            raise ValueError("duplicate statement")
        self.items[statement.id] = statement

    def update(self, statement: Statement) -> None:
        self.items[statement.id] = statement

    def get(self, statement_id: str) -> Statement | None:
        return self.items.get(statement_id)

    def get_by_card_month(self, account_id: str, month: YearMonth) -> Statement | None:
        return next(
            (s for s in self.items.values() if s.account_id == account_id and s.month == month),
            None,
        )

    def list_for_card(self, account_id: str) -> list[Statement]:
        rows = (s for s in self.items.values() if s.account_id == account_id)
        return sorted(rows, key=lambda s: s.month)

    def list_all(self) -> list[Statement]:
        # like SQL: by month, then insertion order (sorted() is stable)
        return sorted(self.items.values(), key=lambda s: s.month)


class MemoryPlans:
    def __init__(self) -> None:
        self.items: dict[str, InstallmentPlan] = {}

    def add(self, plan: InstallmentPlan) -> None:
        self.items[plan.id] = plan

    def update(self, plan: InstallmentPlan) -> None:
        self.items[plan.id] = plan

    def get(self, plan_id: str) -> InstallmentPlan | None:
        return self.items.get(plan_id)

    def delete(self, plan_id: str) -> None:
        self.items.pop(plan_id, None)

    def list_all(self) -> list[InstallmentPlan]:
        return list(self.items.values())


class MemoryAnchors:
    def __init__(self) -> None:
        # the target is the holding when there is one, else the account
        self.items: dict[tuple[str, dt.date], BalanceAnchor] = {}

    @staticmethod
    def _key(anchor: BalanceAnchor) -> tuple[str, dt.date]:
        return (anchor.holding_id or anchor.account_id, anchor.on_date)

    def upsert(self, anchor: BalanceAnchor) -> None:
        self.items[self._key(anchor)] = anchor

    def list_for_account(self, account_id: str) -> list[BalanceAnchor]:
        return sorted(
            (a for a in self.items.values() if a.account_id == account_id and a.holding_id is None),
            key=lambda a: a.on_date,
        )

    def list_for_holding(self, holding_id: str) -> list[BalanceAnchor]:
        return sorted(
            (a for a in self.items.values() if a.holding_id == holding_id),
            key=lambda a: a.on_date,
        )


class MemoryHoldings:
    def __init__(self) -> None:
        self.items: dict[str, InvestmentHolding] = {}

    def add(self, holding: InvestmentHolding) -> None:
        self.items[holding.id] = holding

    def update(self, holding: InvestmentHolding) -> None:
        self.items[holding.id] = holding

    def get(self, holding_id: str) -> InvestmentHolding | None:
        return self.items.get(holding_id)

    def list_all(self) -> list[InvestmentHolding]:
        return list(self.items.values())

    def list_for_account(self, account_id: str) -> list[InvestmentHolding]:
        return [h for h in self.items.values() if h.account_id == account_id]


class MemoryImageStore:
    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    def save(self, data: bytes) -> str:
        image_type = detect_image_type(data)
        image_id = uuid.uuid4().hex
        self.files[image_id] = (data, image_type)
        return image_id

    def open(self, image_id: str) -> tuple[bytes, str] | None:
        return self.files.get(image_id)

    def delete(self, image_id: str) -> None:
        self.files.pop(image_id, None)


class MemoryUnitOfWork:
    """Snapshot on enter, restore on exit unless committed: gives real atomicity to tests."""

    def __init__(self) -> None:
        self.institutions = MemoryInstitutions()
        self.accounts = MemoryAccounts()
        self.categories = MemoryCategories()
        self.statements = MemoryStatements()
        self.plans = MemoryPlans()
        self.holdings = MemoryHoldings()
        self.splits = MemorySplits()
        self.transactions = MemoryTransactions(self.statements, self.splits)
        self.anchors = MemoryAnchors()
        self._snapshot: dict[str, object] | None = None
        self._committed = False

    def _repos(self) -> dict[str, object]:
        return {
            "institutions": self.institutions,
            "accounts": self.accounts,
            "categories": self.categories,
            "transactions": self.transactions,
            "splits": self.splits,
            "anchors": self.anchors,
            "statements": self.statements,
            "plans": self.plans,
            "holdings": self.holdings,
        }

    def __enter__(self) -> Self:
        self._snapshot = {k: copy.deepcopy(v.items) for k, v in self._repos().items()}  # type: ignore[attr-defined]
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._committed and self._snapshot is not None:
            for name, repo in self._repos().items():
                repo.items = self._snapshot[name]  # type: ignore[attr-defined]
        self._snapshot = None

    def commit(self) -> None:
        self._committed = True
