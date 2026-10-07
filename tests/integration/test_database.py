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
        CreateAccountCommand(
            AccountKind.CREDIT_CARD, inst.id, "Cartão", closing_days_before_due=11, due_day=5
        )
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


def test_migrating_an_old_database_takes_a_backup_first_and_keeps_the_data(tmp_path: Path) -> None:
    from alembic import command

    from financas.container import Container
    from financas.infrastructure.db.migrate import alembic_config, needs_upgrade
    from financas.infrastructure.settings import Settings

    url = f"sqlite:///{tmp_path / 'data' / 'f.db'}"
    settings = Settings(db_url=url, data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
    container = Container(settings)
    assert needs_upgrade(url) is False  # nothing there yet
    assert container.migrate() is None  # a new database needs no backup
    # go back to the Phase 1 schema and put a row in it
    command.downgrade(alembic_config(url), "ba63e5e4254f")
    raw = sqlite3.connect(tmp_path / "data" / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.commit()
    raw.close()
    assert needs_upgrade(url) is True
    backup = container.migrate()
    assert backup is not None and (backup / "financas.db").exists()
    assert needs_upgrade(url) is False
    with sqlite3.connect(tmp_path / "data" / "f.db") as db:
        assert db.execute("select name from institutions").fetchall() == [("BB",)]
    with sqlite3.connect(backup / "financas.db") as old:  # the backup is the old schema
        assert "closing_day" not in [r[1] for r in old.execute("pragma table_info(accounts)")]


def test_database_rejects_invalid_card_rows(sql_uow: SqlUnitOfWork) -> None:
    seed_categories(sql_uow)
    inst = CreateInstitution(sql_uow).execute(CreateInstitutionCommand(name="BB"))
    engine_url = sql_uow._factory.kw["bind"].url  # type: ignore[attr-defined]
    raw = sqlite3.connect(engine_url.database)
    raw.execute("PRAGMA foreign_keys=ON")
    insert = (
        "insert into accounts (id, kind, institution_id, nickname, is_active,"
        " closing_days_before_due, due_day, credit_limit_cents) values (?, ?, ?, 'x', 1, ?, ?, ?)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # a card needs both days
        raw.execute(insert, ("1" * 32, "credit_card", inst.id, 11, None, None))
    with pytest.raises(sqlite3.IntegrityError):  # more than 27 days before the due date
        raw.execute(insert, ("2" * 32, "credit_card", inst.id, 28, 5, None))
    with pytest.raises(sqlite3.IntegrityError):  # a checking account has no card fields
        raw.execute(insert, ("3" * 32, "checking", inst.id, 11, 5, None))
    with pytest.raises(sqlite3.IntegrityError):  # negative limit
        raw.execute(insert, ("4" * 32, "credit_card", inst.id, 11, 5, -1))
    raw.execute(insert, ("5" * 32, "credit_card", inst.id, 11, 5, 100))
    entry = (
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring, plan_id, installment_number)"
        " values (?, ?, '2026-07-01', 'expense', (select id from categories limit 1), -1, 'x',"
        " 'x', 0, ?, ?)"
    )
    raw.execute(
        "insert into installment_plans"
        " (id, account_id, category_id, description, installment_total)"
        " values (?, ?, (select id from categories limit 1), 'x', 3)",
        ("p" * 32, "5" * 32),
    )
    with pytest.raises(sqlite3.IntegrityError):  # an installment number without a plan
        raw.execute(entry, ("e" * 32, "5" * 32, None, 1))
    with pytest.raises(sqlite3.IntegrityError):  # a plan without an installment number
        raw.execute(entry, ("f" * 32, "5" * 32, "p" * 32, None))
    with pytest.raises(sqlite3.IntegrityError):  # installment numbers start at 1
        raw.execute(entry, ("g" * 32, "5" * 32, "p" * 32, 0))
    raw.execute(entry, ("h" * 32, "5" * 32, "p" * 32, 2))
    stmt = (
        "insert into statements (id, account_id, month, closing_date, due_date)"
        " values (?, ?, ?, ?, ?)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # bad month format
        raw.execute(stmt, ("a" * 32, "5" * 32, "2026-7", "2026-07-25", "2026-08-05"))
    with pytest.raises(sqlite3.IntegrityError):  # due must be after closing
        raw.execute(stmt, ("b" * 32, "5" * 32, "2026-07", "2026-07-25", "2026-07-25"))
    raw.execute(stmt, ("c" * 32, "5" * 32, "2026-07", "2026-07-25", "2026-08-05"))
    with pytest.raises(sqlite3.IntegrityError):  # one statement per card and month
        raw.execute(stmt, ("d" * 32, "5" * 32, "2026-07", "2026-07-25", "2026-08-05"))
    raw.close()


def test_investment_migration_keeps_existing_investment_accounts(tmp_path: Path) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    command.upgrade(alembic_config(url), "04545ce71be4")  # the Phase 2 schema
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a1', 'investment', 'i', 'Caixinha', 1), ('a2', 'checking', 'i', 'CC', 1)"
    )
    raw.commit()
    raw.close()
    upgrade_to_head(url)
    with sqlite3.connect(tmp_path / "f.db") as db:
        rows = db.execute(
            "select id, tracking, asset_class, is_emergency_fund from accounts order by id"
        ).fetchall()
    assert rows == [("a1", "account", "other", 0), ("a2", None, None, 0)]


def test_closing_days_migration_converts_cards_and_keeps_statements(tmp_path: Path) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    command.upgrade(alembic_config(url), "b7d2c4e1a9f3")  # closing_day still exists
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active, closing_day,"
        " due_day) values ('a', 'credit_card', 'i', 'A', 1, 20, 5),"
        " ('b', 'credit_card', 'i', 'B', 1, 10, 17), ('c', 'credit_card', 'i', 'C', 1, 25, 5)"
    )
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('k', 'checking', 'i', 'CC', 1)"
    )
    raw.execute(  # card A's latest statement shows its real gap: 10 days
        "insert into statements (id, account_id, month, closing_date, due_date) values"
        " ('s1', 'a', '2026-06', '2026-06-25', '2026-07-05'),"
        " ('s2', 'a', '2026-07', '2026-07-26', '2026-08-05')"
    )
    raw.commit()
    raw.close()
    upgrade_to_head(url)
    with sqlite3.connect(tmp_path / "f.db") as db:
        cards = db.execute(
            "select id, closing_days_before_due, due_day from accounts order by id"
        ).fetchall()
        statements = db.execute(
            "select id, closing_date, due_date from statements order by id"
        ).fetchall()
        assert "closing_day" not in [r[1] for r in db.execute("pragma table_info(accounts)")]
    # A: latest statement (10 days); B: 10 -> due 17 in the same month (7); C: 25 -> 5 (11)
    assert cards == [("a", 10, 5), ("b", 7, 17), ("c", 11, 5), ("k", None, None)]
    assert statements == [  # frozen history is untouched
        ("s1", "2026-06-25", "2026-07-05"),
        ("s2", "2026-07-26", "2026-08-05"),
    ]
    command.downgrade(alembic_config(url), "b7d2c4e1a9f3")
    with sqlite3.connect(tmp_path / "f.db") as db:
        old = db.execute("select id, closing_day, due_day from accounts order by id").fetchall()
    assert old == [("a", 26, 5), ("b", 10, 17), ("c", 25, 5), ("k", None, None)]
    upgrade_to_head(url)  # and forward again


def test_database_rejects_invalid_investment_rows(sql_uow: SqlUnitOfWork) -> None:
    seed_categories(sql_uow)
    inst = CreateInstitution(sql_uow).execute(CreateInstitutionCommand(name="BB"))
    engine_url = sql_uow._factory.kw["bind"].url  # type: ignore[attr-defined]
    raw = sqlite3.connect(engine_url.database)
    raw.execute("PRAGMA foreign_keys=ON")
    insert = (
        "insert into accounts (id, kind, institution_id, nickname, is_active, tracking,"
        " asset_class, is_emergency_fund) values (?, ?, ?, 'x', 1, ?, ?, ?)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # an investment account needs both fields
        raw.execute(insert, ("1" * 32, "investment", inst.id, None, "other", 0))
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(insert, ("2" * 32, "investment", inst.id, "account", None, 0))
    with pytest.raises(sqlite3.IntegrityError):  # unknown class
        raw.execute(insert, ("3" * 32, "investment", inst.id, "account", "gold", 0))
    with pytest.raises(sqlite3.IntegrityError):  # unknown tracking
        raw.execute(insert, ("4" * 32, "investment", inst.id, "everything", "other", 0))
    with pytest.raises(sqlite3.IntegrityError):  # a checking account has none of them
        raw.execute(insert, ("5" * 32, "checking", inst.id, "account", "other", 0))
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(insert, ("6" * 32, "checking", inst.id, None, None, 1))
    raw.execute(insert, ("7" * 32, "investment", inst.id, "holdings", "fixed_income", 1))
    raw.execute(insert, ("8" * 32, "checking", inst.id, None, None, 0))
    anchor = (
        "insert into balance_anchors (id, account_id, on_date, balance_cents, gross_balance_cents)"
        " values (?, ?, ?, ?, ?)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # gross below net
        raw.execute(anchor, ("a" * 32, "7" * 32, "2026-07-01", 100, 99))
    raw.execute(anchor, ("b" * 32, "7" * 32, "2026-07-01", 100, 120))
    raw.execute(anchor, ("c" * 32, "7" * 32, "2026-07-02", 100, None))
    raw.close()


def test_holdings_migration_keeps_existing_valuations(tmp_path: Path) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    command.upgrade(alembic_config(url), "8cd33775afe5")  # the Phase 3a schema
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active, tracking,"
        " asset_class,"
        " is_emergency_fund) values ('a', 'investment', 'i', 'Cx', 1, 'account', 'other', 0)"
    )
    raw.execute(
        "insert into balance_anchors (id, account_id, on_date, balance_cents)"
        " values ('b1', 'a', '2026-07-01', 100)"
    )
    raw.commit()
    raw.close()
    upgrade_to_head(url)
    with sqlite3.connect(tmp_path / "f.db") as db:
        assert db.execute(
            "select id, holding_id, balance_cents from balance_anchors"
        ).fetchall() == [("b1", None, 100)]
        with pytest.raises(sqlite3.IntegrityError):  # still one valuation per account and date
            db.execute(
                "insert into balance_anchors (id, account_id, on_date, balance_cents)"
                " values ('b2', 'a', '2026-07-01', 200)"
            )


def test_database_rejects_invalid_holding_rows(sql_uow: SqlUnitOfWork) -> None:
    seed_categories(sql_uow)
    inst = CreateInstitution(sql_uow).execute(CreateInstitutionCommand(name="BB"))
    engine_url = sql_uow._factory.kw["bind"].url  # type: ignore[attr-defined]
    raw = sqlite3.connect(engine_url.database)
    raw.execute("PRAGMA foreign_keys=ON")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active, tracking,"
        " asset_class,"
        " is_emergency_fund) values ('a', 'investment', ?, 'Cx', 1, 'holdings', 'fixed_income', 0)",
        (inst.id,),
    )
    insert = (
        "insert into investment_holdings (id, account_id, name, instrument_type, issuer_id,"
        " indexer,"
        " rate_mode, rate_bps, applied_on, principal_cents, maturity_on, liquidity, liquid_from,"
        " fgc_covered, is_emergency_fund, asset_class, status)"
        " values (?, 'a', 'x', ?, ?, ?, ?, ?, '2026-03-01', ?, ?, ?, ?, 1, 0, 'fixed_income', ?)"
    )

    def row(**overrides: object) -> tuple[object, ...]:
        values: dict[str, object] = {
            "id": "h1", "type": "cdb", "issuer": inst.id, "indexer": "cdi",
            "mode": "percent_of_index", "bps": 11_000, "principal": 1_000, "maturity": "2028-03-01",
            "liquidity": "at_maturity", "liquid_from": None, "status": "active",
        }  # fmt: skip
        values.update(overrides)
        return tuple(values.values())

    bad_cases = [
        {"type": "gold"},
        {"mode": "percent_of_index", "bps": None},  # a mode without a rate
        {"mode": None, "bps": 11_000},  # a rate without a mode
        {"bps": -1},
        {"principal": 0},
        {"maturity": None},  # at maturity needs the date
        {"liquid_from": "2026-06-01"},  # a grace period only with daily liquidity
        {"maturity": "2026-03-01"},  # not after the application
        {"status": "gone"},
        {"liquidity": "weekly"},
    ]
    for n, overrides in enumerate(bad_cases):
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(insert, row(id=f"bad{n}", **overrides))
    raw.execute(insert, row(id="ok1"))
    raw.execute(
        insert, row(id="ok2", liquidity="daily", maturity=None, mode=None, bps=None, indexer=None)
    )
    raw.close()


def test_migrating_a_database_with_data_in_every_table(tmp_path: Path) -> None:
    """Batch migrations recreate tables: parents with child rows must survive (foreign keys)."""
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    command.upgrade(alembic_config(url), "ba63e5e4254f")  # the Phase 1 schema
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        'insert into categories (id, slug, name, "group", kind)'
        " values ('c', 'food', 'Alimentação',"
        " 'non_essential', 'expense')"
    )
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a', 'checking', 'i', 'CC', 1), ('s', 'investment', 'i', 'Caixinha', 1)"
    )
    raw.execute(
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring)"
        " values ('t', 'a', '2026-07-01', 'expense', 'c', -500, 'Café', 'cafe', 0)"
    )
    raw.execute(
        "insert into balance_anchors (id, account_id, on_date, balance_cents)"
        " values ('b', 'a', '2026-07-01', 1000), ('b2', 's', '2026-07-01', 5000)"
    )
    raw.commit()
    raw.close()
    upgrade_to_head(url)  # must not fail and must not leave a half-migrated database
    with sqlite3.connect(tmp_path / "f.db") as db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("select count(*) from transactions").fetchone() == (1,)
        assert db.execute("select count(*) from balance_anchors").fetchone() == (2,)
        assert db.execute("select id, tracking from accounts order by id").fetchall() == [
            ("a", None),
            ("s", "account"),
        ]
        assert (
            db.execute("select name from sqlite_master where name like '_alembic_tmp%'").fetchall()
            == []
        )
        version = db.execute("select version_num from alembic_version").fetchone()[0]
    from financas.infrastructure.db.migrate import head_revision

    assert version == head_revision(url)


def test_database_rejects_more_invalid_rows(sql_uow: SqlUnitOfWork) -> None:
    seed_categories(sql_uow)
    inst = CreateInstitution(sql_uow).execute(CreateInstitutionCommand(name="BB"))
    engine_url = sql_uow._factory.kw["bind"].url  # type: ignore[attr-defined]
    raw = sqlite3.connect(engine_url.database)
    raw.execute("PRAGMA foreign_keys=ON")

    def fails(sql: str, *params: object) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(sql, params)

    fails("update institutions set color = 'red' where id = ?", inst.id)
    fails("update institutions set color = '#abcdef' where id = ?", inst.id)  # uppercase only
    raw.execute("update institutions set color = '#ABCDEF' where id = ?", (inst.id,))
    fails("update categories set monthly_budget_cents = 0 where slug = 'food'")
    fails("update categories set monthly_budget_cents = -5 where slug = 'food'")
    fails("update categories set kind = 'income' where slug = 'food'")  # group x kind
    fails("update categories set \"group\" = 'movement' where slug = 'salary'")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active) values"
        " ('a', 'checking', ?, 'CC', 1)",
        (inst.id,),
    )
    cat = raw.execute("select id from categories where slug = 'food'").fetchone()[0]
    fails(  # a transfer id only on transfers
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring, transfer_id)"
        " values ('t', 'a', '2026-07-01', 'expense', ?, -1, 'x', 'x', 0, 'trf')",
        cat,
    )
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active,"
        " closing_days_before_due, due_day) values ('k', 'credit_card', ?, 'K', 1, 11, 5)",
        (inst.id,),
    )
    for month in ("2026-00", "2026-13", "2026-19", "2026-7"):
        fails(
            "insert into statements (id, account_id, month, closing_date, due_date)"
            " values (?, 'k', ?, '2026-07-25', '2026-08-05')",
            f"s{month}",
            month,
        )
    raw.execute(
        "insert into statements (id, account_id, month, closing_date, due_date)"
        " values ('ok', 'k', '2026-12', '2026-12-25', '2027-01-05')"
    )
    raw.close()


def test_refunded_migration_keeps_rows_defaults_to_false_and_round_trips(tmp_path: Path) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    config = alembic_config(url)
    command.upgrade(config, "c3a9d5f7e2b1")  # the schema before ``is_refunded``
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a', 'checking', 'i', 'CC', 1)"
    )
    raw.execute(
        'insert into categories (id, slug, name, "group", kind) values'
        " ('c', 'food', 'Alimentação', 'non_essential', 'expense')"
    )
    raw.execute(
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring)"
        " values ('t1', 'a', '2026-07-01', 'expense', 'c', -500, 'x', 'x', 0)"
    )
    raw.commit()
    raw.close()

    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "f.db") as db:
        assert db.execute("select id, amount_cents, is_refunded from transactions").fetchall() == [
            ("t1", -500, 0)
        ]
        db.execute("update transactions set is_refunded = 1 where id = 't1'")  # an expense: fine
        with pytest.raises(sqlite3.IntegrityError):  # only expenses can be refunded
            db.execute(
                "insert into transactions (id, account_id, posted_on, kind, category_id,"
                " amount_cents, description, description_search, is_recurring, is_refunded)"
                " values ('t2', 'a', '2026-07-01', 'income', 'c', 500, 'x', 'x', 0, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # and the flag cannot be NULL
            db.execute("update transactions set is_refunded = NULL where id = 't1'")

    command.downgrade(config, "c3a9d5f7e2b1")
    with sqlite3.connect(tmp_path / "f.db") as db:
        columns = [r[1] for r in db.execute("pragma table_info(transactions)")]
        assert "is_refunded" not in columns
        assert db.execute("select count(*) from transactions").fetchone() == (1,)
    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "f.db") as db:
        assert db.execute("select is_refunded from transactions").fetchall() == [(0,)]


def test_splits_migration_adds_the_table_with_cascade_and_check_and_round_trips(
    tmp_path: Path,
) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    config = alembic_config(url)
    command.upgrade(config, "d4e8f1a2b6c7")  # the schema before ``transaction_splits``
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a', 'checking', 'i', 'CC', 1)"
    )
    raw.execute(
        'insert into categories (id, slug, name, "group", kind) values'
        " ('c', 'food', 'Alimentação', 'non_essential', 'expense')"
    )
    raw.execute(
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring)"
        " values ('t1', 'a', '2026-07-01', 'expense', 'c', -1000, 'x', 'x', 0)"
    )
    raw.commit()
    raw.close()

    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "f.db") as db:
        db.execute("pragma foreign_keys = on")
        assert db.execute("select count(*) from transactions").fetchone() == (1,)
        db.execute(
            "insert into transaction_splits values ('s1', 't1', 'item 1', 'c', 600),"
            " ('s2', 't1', 'item 2', 'c', 400)"
        )
        with pytest.raises(sqlite3.IntegrityError):  # an item is a positive amount
            db.execute("insert into transaction_splits values ('s3', 't1', 'x', 'c', 0)")
        with pytest.raises(sqlite3.IntegrityError):  # and belongs to a real entry
            db.execute("insert into transaction_splits values ('s4', 'nope', 'x', 'c', 5)")
        db.execute("delete from transactions where id = 't1'")  # ON DELETE CASCADE
        assert db.execute("select count(*) from transaction_splits").fetchone() == (0,)

    command.downgrade(config, "d4e8f1a2b6c7")
    with sqlite3.connect(tmp_path / "f.db") as db:
        tables = {r[0] for r in db.execute("select name from sqlite_master where type = 'table'")}
        assert "transaction_splits" not in tables
    command.upgrade(config, "head")


def test_merchant_migration_adds_an_indexed_nullable_column_and_round_trips(tmp_path: Path) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    config = alembic_config(url)
    command.upgrade(config, "e5f9a2b3c8d1")  # the schema before ``merchant``
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a', 'checking', 'i', 'CC', 1)"
    )
    raw.execute(
        'insert into categories (id, slug, name, "group", kind) values'
        " ('c', 'food', 'Alimentação', 'non_essential', 'expense')"
    )
    raw.execute(
        "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
        " description, description_search, is_recurring)"
        " values ('t1', 'a', '2026-07-01', 'expense', 'c', -1000, 'x', 'x', 0)"
    )
    raw.commit()
    raw.close()

    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "f.db") as db:
        assert db.execute("select id, merchant from transactions").fetchall() == [("t1", None)]
        db.execute("update transactions set merchant = 'Amazon' where id = 't1'")
        indexes = {r[1] for r in db.execute("pragma index_list(transactions)")}
        assert "ix_transactions_merchant" in indexes
        columns = {r[1]: r for r in db.execute("pragma table_info(transactions)")}
        assert columns["merchant"][3] == 0  # nullable

    command.downgrade(config, "e5f9a2b3c8d1")
    with sqlite3.connect(tmp_path / "f.db") as db:
        assert "merchant" not in [r[1] for r in db.execute("pragma table_info(transactions)")]
        assert db.execute("select count(*) from transactions").fetchone() == (1,)
    command.upgrade(config, "head")


def test_backfill_migration_enriches_legacy_rows_once_and_keeps_the_rest(tmp_path: Path) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    config = alembic_config(url)
    command.upgrade(config, "f6a1b3c9d2e4")  # the schema before the backfill
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a', 'checking', 'i', 'CC', 1)"
    )
    raw.execute(
        'insert into categories (id, slug, name, "group", kind) values'
        " ('c', 'food', 'Alimentação', 'non_essential', 'expense'),"
        " ('s', 'salary', 'Salário', 'income', 'income')"
    )
    raw.execute(
        "insert into installment_plans"
        " (id, account_id, description, category_id, installment_total)"
        " values ('p', 'a', 'Fone - Amazon', 'c', 2)"
    )
    legacy = [
        ("t1", "expense", "Mouse - Kabum", None, None),
        ("t2", "expense", "PAG*MERCADOLIVRE 123", None, None),
        ("t3", "expense", "Remédio", "Drogasil", None),
        ("t4", "expense", "Remédio", None, None),  # learns "Drogasil" from t3
        ("t5", "expense", "Almoço", "mercadolivre", None),  # an alias spelling
        ("t6", "expense", "Almoço", "Restaurante do Zé", None),  # typed: untouched
        ("t7", "income", "Salário - Empresa", None, None),  # not an expense: untouched
        ("t8", "expense", "Fone - Amazon", None, "p"),  # an installment: the plan follows
        ("t9", "expense", "Aluguel - Outubro", None, None),  # a month, not a merchant
    ]
    for entry_id, kind, text, merchant, plan in legacy:
        category = "s" if kind == "income" else "c"
        sign = 1 if kind == "income" else -1
        raw.execute(
            "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
            " description, description_search, is_recurring, merchant, plan_id, installment_number)"
            " values (?, 'a', '2026-07-01', ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (
                entry_id,
                kind,
                category,
                sign * 1000,
                text,
                text.lower(),
                merchant,
                plan,
                1 if plan else None,
            ),
        )
    raw.commit()
    raw.close()

    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "f.db") as db:
        rows = {
            r[0]: r[1:]
            for r in db.execute(
                "select id, description, description_search, merchant from transactions"
            )
        }
        plan_text = db.execute("select description from installment_plans").fetchone()[0]
    assert rows["t1"] == ("Mouse", "mouse", "Kabum")
    assert rows["t2"] == ("PAG*MERCADOLIVRE 123", "pag*mercadolivre 123", "Mercado Livre")
    assert rows["t3"][2] == "Drogasil" and rows["t4"][2] == "Drogasil"
    assert rows["t5"][2] == "Mercado Livre"
    assert rows["t6"][2] == "Restaurante do Zé"
    assert rows["t7"] == ("Salário - Empresa", "salário - empresa", None)
    assert rows["t8"][0] == "Fone" and rows["t8"][2] == "Amazon" and plan_text == "Fone"
    assert rows["t9"] == ("Aluguel - Outubro", "aluguel - outubro", None)

    command.downgrade(config, "f6a1b3c9d2e4")  # the schema goes back; the data stays as it is
    command.upgrade(config, "head")  # and a second pass finds nothing left to do
    with sqlite3.connect(tmp_path / "f.db") as db:
        assert db.execute("select description from transactions where id = 't1'").fetchone() == (
            "Mouse",
        )


def test_itemized_parent_migration_clears_their_category_and_restores_it_on_downgrade(
    tmp_path: Path,
) -> None:
    from alembic import command

    from financas.infrastructure.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'f.db'}"
    config = alembic_config(url)
    command.upgrade(config, "a8d4e6f1b2c3")  # category_id is still NOT NULL here
    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("insert into institutions (id, slug, name) values ('i', 'bb', 'BB')")
    raw.execute(
        "insert into accounts (id, kind, institution_id, nickname, is_active)"
        " values ('a', 'checking', 'i', 'CC', 1)"
    )
    raw.execute(
        'insert into categories (id, slug, name, "group", kind) values'
        " ('g', 'groceries', 'Supermercado', 'essential', 'expense'),"
        " ('h', 'health', 'Saúde', 'essential', 'expense'),"
        " ('s', 'salary', 'Salário', 'income', 'income'),"
        " ('u', 'uncategorized', 'Não categorizado', 'review', 'expense')"
    )
    for entry_id, kind, category, cents in (
        ("big", "expense", "g", -1000),  # itemized below: loses its category
        ("plain", "expense", "g", -500),
        ("pay", "income", "s", 900),
    ):
        raw.execute(
            "insert into transactions (id, account_id, posted_on, kind, category_id, amount_cents,"
            " description, description_search, is_recurring)"
            " values (?, 'a', '2026-07-01', ?, ?, ?, 'x', 'x', 0)",
            (entry_id, kind, category, cents),
        )
    raw.execute(
        "insert into transaction_splits values ('s1', 'big', 'Feira', 'g', 300),"
        " ('s2', 'big', 'Remédio', 'h', 700)"
    )
    raw.commit()
    raw.close()

    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "f.db") as db:
        categories = dict(db.execute("select id, category_id from transactions"))
        assert categories == {"big": None, "plain": "g", "pay": "s"}  # only the itemized parent
        assert db.execute("select count(*) from transaction_splits").fetchone() == (2,)  # kept
        db.execute("update transactions set category_id = NULL where id = 'plain'")  # an expense
        with pytest.raises(sqlite3.IntegrityError):  # but an income always has its category
            db.execute("update transactions set category_id = NULL where id = 'pay'")

    command.downgrade(config, "a8d4e6f1b2c3")  # each such parent gets its biggest item's category
    with sqlite3.connect(tmp_path / "f.db") as db:
        restored = dict(db.execute("select id, category_id from transactions where id = 'big'"))
        assert restored == {"big": "h"}  # 7,00 of Saúde is the biggest item
        # the expense the test emptied by hand is "uncategorized" again: nothing is left NULL
        assert db.execute("select category_id from transactions where id = 'plain'").fetchone() == (
            "u",
        )
        info = {r[1]: r for r in db.execute("pragma table_info(transactions)")}
        assert info["category_id"][3] == 1  # NOT NULL again
    command.upgrade(config, "head")
