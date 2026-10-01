import datetime as dt
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind, TransactionKind
from financas.infrastructure.db.engine import make_engine
from financas.infrastructure.db.migrate import upgrade_to_head
from financas.infrastructure.db.orm import Base
from financas.infrastructure.db.repositories import SqlUnitOfWork
from financas.infrastructure.db.seed import INITIAL_CATEGORIES, seed_categories

D = dt.date


def test_migrations_match_the_orm_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    engine = make_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"render_as_batch": True})
        assert compare_metadata(context, Base.metadata) == []


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    upgrade_to_head(url)


def test_every_connection_enforces_foreign_keys_and_reports_its_journal_mode(
    tmp_path: Path,
) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    with engine.connect() as connection:
        assert connection.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(sa.text("PRAGMA journal_mode")).scalar() in {"wal", "delete"}


def test_make_engine_creates_the_data_folder(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'new' / 'dir' / 'f.db'}")
    with engine.connect():
        pass
    assert (tmp_path / "new" / "dir" / "f.db").exists()


def test_database_rejects_what_the_domain_would_reject(sql_uow: SqlUnitOfWork) -> None:
    seed_categories(sql_uow)
    inst = CreateInstitution(sql_uow).execute(CreateInstitutionCommand(name="BB"))
    acc = CreateAccount(sql_uow).execute(CreateAccountCommand(AccountKind.CHECKING, inst.id, "CC"))
    engine_url = sql_uow._factory.kw["bind"].url  # type: ignore[attr-defined]
    raw = sqlite3.connect(engine_url.database)
    raw.execute("PRAGMA foreign_keys=ON")
    cat = raw.execute("select id from categories limit 1").fetchone()[0]
    insert = (
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring) values (?, ?, '2026-07-01', ?, ?, ?, 'x',"
        " 'x', 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # expense with a positive amount
        raw.execute(insert, ("1" * 32, acc.id, "expense", cat, 100))
    with pytest.raises(sqlite3.IntegrityError):  # unknown kind
        raw.execute(insert, ("2" * 32, acc.id, "gift", cat, 100))
    with pytest.raises(sqlite3.IntegrityError):  # unknown account (foreign key)
        raw.execute(insert, ("3" * 32, "9" * 32, "income", cat, 100))
    raw.close()


def test_seed_is_idempotent_on_sqlite(sql_uow: SqlUnitOfWork) -> None:
    assert seed_categories(sql_uow) == len(INITIAL_CATEGORIES)
    assert seed_categories(sql_uow) == 0
    with sql_uow as work:
        names = {c.slug: c.name for c in work.categories.list_all()}
    assert names["health"] == "Saúde"
    assert names["uncategorized"] == "Não categorizado"


def test_use_cases_end_to_end_and_atomic_transfer(sql_uow: SqlUnitOfWork) -> None:
    seed_categories(sql_uow)
    inst = CreateInstitution(sql_uow).execute(CreateInstitutionCommand(name="BB"))
    checking = CreateAccount(sql_uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, inst.id, "CC")
    )
    card = CreateAccount(sql_uow).execute(
        CreateAccountCommand(AccountKind.CREDIT_CARD, inst.id, "Cartão")
    )
    RegisterTransaction(sql_uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 7, 1), TransactionKind.EXPENSE, 1_234, "Café São João"
        )
    )
    with pytest.raises(DomainError):
        RegisterTransfer(sql_uow).execute(
            RegisterTransferCommand(checking.id, card.id, D(2026, 7, 2), 500)
        )
    with sql_uow as work:
        rows = work.transactions.list_between(D(2026, 7, 1), D(2026, 7, 31))
    assert [(r.amount_cents, r.description_search) for r in rows] == [(-1_234, "cafe sao joao")]
