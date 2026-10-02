"""SQLAlchemy repositories and the unit of work."""

import datetime as dt
import threading
from collections.abc import Sequence
from types import TracebackType
from typing import Self

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

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
)
from financas.domain.money import YearMonth
from financas.infrastructure.db.orm import (
    AccountRow,
    BalanceAnchorRow,
    CategoryRow,
    InstallmentPlanRow,
    InstitutionRow,
    InvestmentHoldingRow,
    StatementRow,
    TransactionRow,
)


def _institution(r: InstitutionRow) -> Institution:
    return Institution(r.id, r.slug, r.name, r.group_slug, r.color, r.image_id)


def _account(r: AccountRow) -> Account:
    return Account(
        r.id,
        r.kind,
        r.institution_id,
        r.nickname,
        r.is_active,
        r.color,
        r.image_id,
        r.closing_days_before_due,
        r.due_day,
        r.credit_limit_cents,
        r.tracking,
        r.asset_class,
        r.is_emergency_fund,
    )


def _category(r: CategoryRow) -> Category:
    return Category(r.id, r.slug, r.name, r.group, r.kind, r.monthly_budget_cents, r.color)


def _transaction(r: TransactionRow) -> Transaction:
    return Transaction(
        r.id,
        r.account_id,
        r.posted_on,
        r.kind,
        r.category_id,
        r.amount_cents,
        r.description,
        r.description_search,
        r.is_recurring,
        r.transfer_id,
        r.notes,
        r.statement_id,
        r.plan_id,
        r.installment_number,
        r.holding_id,
    )


def _statement(r: StatementRow) -> Statement:
    return Statement(
        r.id,
        r.account_id,
        YearMonth.parse(r.month),
        r.closing_date,
        r.due_date,
        r.informed_total_cents,
    )


def _plan(r: InstallmentPlanRow) -> InstallmentPlan:
    return InstallmentPlan(
        r.id, r.account_id, r.description, r.category_id, r.installment_total, r.purchased_on
    )


def _holding(r: InvestmentHoldingRow) -> InvestmentHolding:
    return InvestmentHolding(
        r.id,
        r.account_id,
        r.name,
        r.instrument_type,
        r.issuer_id,
        r.indexer,
        r.rate_mode,
        r.rate_bps,
        r.applied_on,
        r.principal_cents,
        r.maturity_on,
        r.liquidity,
        r.liquid_from,
        r.fgc_covered,
        r.is_emergency_fund,
        r.asset_class,
        r.status,
    )


def _anchor(r: BalanceAnchorRow) -> BalanceAnchor:
    return BalanceAnchor(
        r.id,
        r.account_id,
        r.on_date,
        r.balance_cents,
        r.note,
        r.gross_balance_cents,
        r.holding_id,
    )


class SqlInstitutions:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, institution: Institution) -> None:
        self._s.add(InstitutionRow(**vars(institution)))
        self._s.flush()

    def update(self, institution: Institution) -> None:
        row = self._s.get(InstitutionRow, institution.id)
        assert row is not None
        for key, value in vars(institution).items():
            setattr(row, key, value)
        self._s.flush()

    def get(self, institution_id: str) -> Institution | None:
        row = self._s.get(InstitutionRow, institution_id)
        return _institution(row) if row else None

    def get_by_slug(self, slug: str) -> Institution | None:
        row = self._s.scalars(sa.select(InstitutionRow).where(InstitutionRow.slug == slug)).first()
        return _institution(row) if row else None

    def list_all(self) -> list[Institution]:
        rows = self._s.scalars(sa.select(InstitutionRow).order_by(sa.text("institutions.rowid")))
        return [_institution(r) for r in rows]


class SqlAccounts:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, account: Account) -> None:
        self._s.add(AccountRow(**vars(account)))
        self._s.flush()

    def update(self, account: Account) -> None:
        row = self._s.get(AccountRow, account.id)
        assert row is not None
        for key, value in vars(account).items():
            setattr(row, key, value)
        self._s.flush()

    def get(self, account_id: str) -> Account | None:
        row = self._s.get(AccountRow, account_id)
        return _account(row) if row else None

    def list_all(self) -> list[Account]:
        rows = self._s.scalars(sa.select(AccountRow).order_by(sa.text("accounts.rowid")))
        return [_account(r) for r in rows]


class SqlCategories:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, category: Category) -> None:
        self._s.add(CategoryRow(**vars(category)))
        self._s.flush()

    def update(self, category: Category) -> None:
        row = self._s.get(CategoryRow, category.id)
        assert row is not None
        for key, value in vars(category).items():
            setattr(row, key, value)
        self._s.flush()

    def get(self, category_id: str) -> Category | None:
        row = self._s.get(CategoryRow, category_id)
        return _category(row) if row else None

    def get_by_slug(self, slug: str) -> Category | None:
        row = self._s.scalars(sa.select(CategoryRow).where(CategoryRow.slug == slug)).first()
        return _category(row) if row else None

    def list_all(self) -> list[Category]:
        rows = self._s.scalars(sa.select(CategoryRow).order_by(sa.text("categories.rowid")))
        return [_category(r) for r in rows]


class SqlTransactions:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add_many(self, transactions: Sequence[Transaction]) -> None:
        self._s.add_all(TransactionRow(**vars(t)) for t in transactions)
        self._s.flush()

    def get(self, transaction_id: str) -> Transaction | None:
        row = self._s.get(TransactionRow, transaction_id)
        return _transaction(row) if row else None

    def delete(self, transaction_id: str) -> None:
        self._s.execute(sa.delete(TransactionRow).where(TransactionRow.id == transaction_id))

    def list_by_transfer(self, transfer_id: str) -> list[Transaction]:
        rows = self._s.scalars(
            sa.select(TransactionRow)
            .where(TransactionRow.transfer_id == transfer_id)
            .order_by(sa.text("transactions.rowid"))
        )
        return [_transaction(r) for r in rows]

    def list_between(
        self, start: dt.date, end: dt.date, account_id: str | None = None
    ) -> list[Transaction]:
        query = sa.select(TransactionRow).where(TransactionRow.posted_on.between(start, end))
        if account_id is not None:
            query = query.where(TransactionRow.account_id == account_id)
        query = query.order_by(TransactionRow.posted_on.desc(), sa.text("transactions.rowid DESC"))
        return [_transaction(r) for r in self._s.scalars(query)]

    def list_for_competence(self, start: dt.date, end: dt.date) -> list[Transaction]:
        first, last = str(YearMonth.from_date(start)), str(YearMonth.from_date(end))
        query = (
            sa.select(TransactionRow)
            .outerjoin(StatementRow, TransactionRow.statement_id == StatementRow.id)
            .where(
                sa.or_(
                    sa.and_(
                        TransactionRow.statement_id.is_(None),
                        TransactionRow.posted_on.between(start, end),
                    ),
                    StatementRow.month.between(first, last),
                )
            )
            .order_by(TransactionRow.posted_on.desc(), sa.text("transactions.rowid DESC"))
        )
        return [_transaction(r) for r in self._s.scalars(query)]

    def _list_where(self, *conditions: sa.ColumnElement[bool]) -> list[Transaction]:
        query = (
            sa.select(TransactionRow)
            .where(*conditions)
            .order_by(TransactionRow.posted_on.desc(), sa.text("transactions.rowid DESC"))
        )
        return [_transaction(r) for r in self._s.scalars(query)]

    def list_by_account(self, account_id: str) -> list[Transaction]:
        return self._list_where(TransactionRow.account_id == account_id)

    def list_by_statement(self, statement_id: str) -> list[Transaction]:
        return self._list_where(TransactionRow.statement_id == statement_id)

    def list_by_plan(self, plan_id: str) -> list[Transaction]:
        rows = self._s.scalars(
            sa.select(TransactionRow)
            .where(TransactionRow.plan_id == plan_id)
            .order_by(TransactionRow.installment_number)
        )
        return [_transaction(r) for r in rows]

    def update_amount(self, transaction_id: str, amount_cents: int) -> None:
        self._s.execute(
            sa.update(TransactionRow)
            .where(TransactionRow.id == transaction_id)
            .values(amount_cents=amount_cents)
        )
        self._s.flush()

    def set_statement(self, transaction_id: str, statement_id: str) -> None:
        self._s.execute(
            sa.update(TransactionRow)
            .where(TransactionRow.id == transaction_id)
            .values(statement_id=statement_id)
        )
        self._s.flush()

    def movements(self, account_id: str) -> list[tuple[dt.date, int]]:
        rows = self._s.execute(
            sa.select(TransactionRow.posted_on, TransactionRow.amount_cents).where(
                TransactionRow.account_id == account_id
            )
        )
        return [(posted_on, cents) for posted_on, cents in rows]

    def movements_for_holding(self, holding_id: str) -> list[tuple[dt.date, int]]:
        rows = self._s.execute(
            sa.select(TransactionRow.posted_on, TransactionRow.amount_cents).where(
                TransactionRow.holding_id == holding_id
            )
        )
        return [(posted_on, cents) for posted_on, cents in rows]

    def last_category_id(self, description_search: str, kind: TransactionKind) -> str | None:
        return self._s.scalars(
            sa.select(TransactionRow.category_id)
            .where(
                TransactionRow.description_search == description_search,
                TransactionRow.kind == kind,
            )
            .order_by(TransactionRow.posted_on.desc(), sa.text("transactions.rowid DESC"))
            .limit(1)
        ).first()


class SqlStatements:
    def __init__(self, session: Session) -> None:
        self._s = session

    @staticmethod
    def _values(statement: Statement) -> dict[str, object]:
        return {**vars(statement), "month": str(statement.month)}

    def add(self, statement: Statement) -> None:
        self._s.add(StatementRow(**self._values(statement)))
        self._s.flush()

    def update(self, statement: Statement) -> None:
        row = self._s.get(StatementRow, statement.id)
        assert row is not None
        for key, value in self._values(statement).items():
            setattr(row, key, value)
        self._s.flush()

    def get(self, statement_id: str) -> Statement | None:
        row = self._s.get(StatementRow, statement_id)
        return _statement(row) if row else None

    def get_by_card_month(self, account_id: str, month: YearMonth) -> Statement | None:
        row = self._s.scalars(
            sa.select(StatementRow).where(
                StatementRow.account_id == account_id, StatementRow.month == str(month)
            )
        ).first()
        return _statement(row) if row else None

    def list_for_card(self, account_id: str) -> list[Statement]:
        rows = self._s.scalars(
            sa.select(StatementRow)
            .where(StatementRow.account_id == account_id)
            .order_by(StatementRow.month)
        )
        return [_statement(r) for r in rows]

    def list_all(self) -> list[Statement]:
        rows = self._s.scalars(
            sa.select(StatementRow).order_by(StatementRow.month, sa.text("statements.rowid"))
        )
        return [_statement(r) for r in rows]


class SqlPlans:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, plan: InstallmentPlan) -> None:
        self._s.add(InstallmentPlanRow(**vars(plan)))
        self._s.flush()

    def get(self, plan_id: str) -> InstallmentPlan | None:
        row = self._s.get(InstallmentPlanRow, plan_id)
        return _plan(row) if row else None

    def delete(self, plan_id: str) -> None:
        self._s.execute(sa.delete(InstallmentPlanRow).where(InstallmentPlanRow.id == plan_id))

    def list_all(self) -> list[InstallmentPlan]:
        rows = self._s.scalars(
            sa.select(InstallmentPlanRow).order_by(sa.text("installment_plans.rowid"))
        )
        return [_plan(r) for r in rows]


class SqlAnchors:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(self, anchor: BalanceAnchor) -> None:
        target = (
            BalanceAnchorRow.holding_id == anchor.holding_id
            if anchor.holding_id is not None
            else sa.and_(
                BalanceAnchorRow.account_id == anchor.account_id,
                BalanceAnchorRow.holding_id.is_(None),
            )
        )
        existing = self._s.scalars(
            sa.select(BalanceAnchorRow).where(target, BalanceAnchorRow.on_date == anchor.on_date)
        ).first()
        if existing is None:
            self._s.add(BalanceAnchorRow(**vars(anchor)))
        else:
            existing.balance_cents = anchor.balance_cents
            existing.note = anchor.note
            existing.gross_balance_cents = anchor.gross_balance_cents
        self._s.flush()

    def list_for_account(self, account_id: str) -> list[BalanceAnchor]:
        rows = self._s.scalars(
            sa.select(BalanceAnchorRow)
            .where(BalanceAnchorRow.account_id == account_id, BalanceAnchorRow.holding_id.is_(None))
            .order_by(BalanceAnchorRow.on_date)
        )
        return [_anchor(r) for r in rows]

    def list_for_holding(self, holding_id: str) -> list[BalanceAnchor]:
        rows = self._s.scalars(
            sa.select(BalanceAnchorRow)
            .where(BalanceAnchorRow.holding_id == holding_id)
            .order_by(BalanceAnchorRow.on_date)
        )
        return [_anchor(r) for r in rows]


class SqlHoldings:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, holding: InvestmentHolding) -> None:
        self._s.add(InvestmentHoldingRow(**vars(holding)))
        self._s.flush()

    def update(self, holding: InvestmentHolding) -> None:
        row = self._s.get(InvestmentHoldingRow, holding.id)
        assert row is not None
        for key, value in vars(holding).items():
            setattr(row, key, value)
        self._s.flush()

    def get(self, holding_id: str) -> InvestmentHolding | None:
        row = self._s.get(InvestmentHoldingRow, holding_id)
        return _holding(row) if row else None

    def list_all(self) -> list[InvestmentHolding]:
        rows = self._s.scalars(
            sa.select(InvestmentHoldingRow).order_by(sa.text("investment_holdings.rowid"))
        )
        return [_holding(r) for r in rows]

    def list_for_account(self, account_id: str) -> list[InvestmentHolding]:
        rows = self._s.scalars(
            sa.select(InvestmentHoldingRow)
            .where(InvestmentHoldingRow.account_id == account_id)
            .order_by(sa.text("investment_holdings.rowid"))
        )
        return [_holding(r) for r in rows]


class SqlWork:
    """The repositories of one open session (what ``with uow as work`` hands out)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.institutions = SqlInstitutions(session)
        self.accounts = SqlAccounts(session)
        self.categories = SqlCategories(session)
        self.transactions = SqlTransactions(session)
        self.anchors = SqlAnchors(session)
        self.statements = SqlStatements(session)
        self.plans = SqlPlans(session)
        self.holdings = SqlHoldings(session)

    def commit(self) -> None:
        self._session.commit()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class SqlUnitOfWork:
    """One session per ``with`` block; rolled back unless ``commit()`` was called.

    The object itself holds no session: every ``with`` gets its own, kept on a per-thread stack,
    so the web server's worker threads never share one, and blocks may nest.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory
        self._local = threading.local()

    def _stack(self) -> list[Session]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    def __enter__(self) -> SqlWork:
        session = self._factory()
        self._stack().append(session)
        return SqlWork(session)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._stack().pop()
        session.rollback()  # no-op after commit
        session.close()
