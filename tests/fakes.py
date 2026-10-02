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
    Statement,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.services.images import detect_image_type


class FixedClock:
    def __init__(self, today: dt.date = dt.date(2026, 7, 25)) -> None:
        self._today = today

    def today(self) -> dt.date:
        return self._today


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


class MemoryTransactions:
    def __init__(self, statements: "MemoryStatements") -> None:
        self.items: dict[str, Transaction] = {}
        self.statements = statements

    def add_many(self, transactions: Sequence[Transaction]) -> None:
        for transaction in transactions:
            self.items[transaction.id] = transaction

    def get(self, transaction_id: str) -> Transaction | None:
        return self.items.get(transaction_id)

    def delete(self, transaction_id: str) -> None:
        self.items.pop(transaction_id, None)

    def list_by_transfer(self, transfer_id: str) -> list[Transaction]:
        return [t for t in self.items.values() if t.transfer_id == transfer_id]

    def list_between(
        self, start: dt.date, end: dt.date, account_id: str | None = None
    ) -> list[Transaction]:
        rows = [
            (n, t)
            for n, t in enumerate(self.items.values())
            if start <= t.posted_on <= end and (account_id is None or t.account_id == account_id)
        ]
        rows.sort(key=lambda r: (r[1].posted_on, r[0]), reverse=True)
        return [t for _, t in rows]

    def list_for_competence(self, start: dt.date, end: dt.date) -> list[Transaction]:
        months = {s.id: s.month for s in self.statements.items.values()}
        first, last = YearMonth.from_date(start), YearMonth.from_date(end)
        rows = []
        for n, t in enumerate(self.items.values()):
            if t.statement_id is not None:
                counts = first <= months[t.statement_id] <= last
            else:
                counts = start <= t.posted_on <= end
            if counts:
                rows.append((n, t))
        rows.sort(key=lambda r: (r[1].posted_on, r[0]), reverse=True)
        return [t for _, t in rows]

    def list_by_account(self, account_id: str) -> list[Transaction]:
        return self._sorted(t for t in self.items.values() if t.account_id == account_id)

    def list_by_statement(self, statement_id: str) -> list[Transaction]:
        return self._sorted(t for t in self.items.values() if t.statement_id == statement_id)

    def list_by_plan(self, plan_id: str) -> list[Transaction]:
        rows = [t for t in self.items.values() if t.plan_id == plan_id]
        return sorted(rows, key=lambda t: t.installment_number or 0)

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
            (t.posted_on, t.amount_cents) for t in self.items.values() if t.account_id == account_id
        ]

    def last_category_id(self, description_search: str, kind: TransactionKind) -> str | None:
        matches = [
            t
            for t in self.items.values()
            if t.description_search == description_search and t.kind is kind
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
        return list(self.items.values())


class MemoryPlans:
    def __init__(self) -> None:
        self.items: dict[str, InstallmentPlan] = {}

    def add(self, plan: InstallmentPlan) -> None:
        self.items[plan.id] = plan

    def get(self, plan_id: str) -> InstallmentPlan | None:
        return self.items.get(plan_id)

    def delete(self, plan_id: str) -> None:
        self.items.pop(plan_id, None)

    def list_all(self) -> list[InstallmentPlan]:
        return list(self.items.values())


class MemoryAnchors:
    def __init__(self) -> None:
        self.items: dict[tuple[str, dt.date], BalanceAnchor] = {}

    def upsert(self, anchor: BalanceAnchor) -> None:
        self.items[(anchor.account_id, anchor.on_date)] = anchor

    def list_for_account(self, account_id: str) -> list[BalanceAnchor]:
        return sorted(
            (a for a in self.items.values() if a.account_id == account_id),
            key=lambda a: a.on_date,
        )


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
        self.transactions = MemoryTransactions(self.statements)
        self.anchors = MemoryAnchors()
        self._snapshot: dict[str, object] | None = None
        self._committed = False

    def _repos(self) -> dict[str, object]:
        return {
            "institutions": self.institutions,
            "accounts": self.accounts,
            "categories": self.categories,
            "transactions": self.transactions,
            "anchors": self.anchors,
            "statements": self.statements,
            "plans": self.plans,
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
