"""Every repository behaves the same on the in-memory and on the SQLite implementation."""

import datetime as dt

import pytest

from fakes import MemoryUnitOfWork
from financas.domain.models import (
    Account,
    AccountKind,
    BalanceAnchor,
    Category,
    CategoryGroup,
    CategoryKind,
    Institution,
    Transaction,
    TransactionKind,
)
from financas.domain.ports import UnitOfWork
from financas.infrastructure.db.repositories import SqlUnitOfWork

D = dt.date


@pytest.fixture(params=["memory", "sqlite"])
def uow(request: pytest.FixtureRequest, sql_uow: SqlUnitOfWork) -> UnitOfWork:
    return MemoryUnitOfWork() if request.param == "memory" else sql_uow


def institution(slug: str = "bb", **kw: object) -> Institution:
    return Institution(id=f"i-{slug}".ljust(32, "0")[:32], slug=slug, name=slug.upper(), **kw)  # type: ignore[arg-type]


def account(inst: Institution, n: int = 1, **kw: object) -> Account:
    return Account(
        id=f"a{n}".ljust(32, "0"),
        kind=AccountKind.CHECKING,
        institution_id=inst.id,
        nickname=f"Conta {n}",
        **kw,  # type: ignore[arg-type]
    )


def category(slug: str = "food", kind: CategoryKind = CategoryKind.EXPENSE) -> Category:
    return Category(
        id=f"c-{slug}".ljust(32, "0")[:32],
        slug=slug,
        name="Alimentação",
        group=CategoryGroup.NON_ESSENTIAL if kind is CategoryKind.EXPENSE else CategoryGroup.INCOME,
        kind=kind,
    )


def tx(n: int, acc: Account, cat: Category, day: dt.date, cents: int, **kw: object) -> Transaction:
    kind = kw.pop("kind", TransactionKind.EXPENSE)
    description = str(kw.pop("description", f"item {n}"))
    return Transaction(
        id=f"t{n}".ljust(32, "0"),
        account_id=acc.id,
        posted_on=day,
        kind=kind,  # type: ignore[arg-type]
        category_id=cat.id,
        amount_cents=cents,
        description=description,
        description_search=description.lower(),
        **kw,  # type: ignore[arg-type]
    )


def populate(uow: UnitOfWork) -> tuple[Institution, Account, Category]:
    inst, cat = institution(), category()
    acc = account(inst)
    with uow as work:
        work.institutions.add(inst)
        work.accounts.add(acc)
        work.categories.add(cat)
        work.categories.add(category("salary", CategoryKind.INCOME))
        work.commit()
    return inst, acc, cat


def test_institution_round_trip_with_appearance_and_accents(uow: UnitOfWork) -> None:
    inst = Institution("i" * 32, "caixa", "Caixa Econômica", "caixa", "#1E395F", "f" * 32)
    with uow as work:
        work.institutions.add(inst)
        work.commit()
    with uow as work:
        assert work.institutions.get(inst.id) == inst
        assert work.institutions.get_by_slug("caixa") == inst
        assert work.institutions.get_by_slug("nope") is None
        assert work.institutions.get("x" * 32) is None
        assert work.institutions.list_all() == [inst]


def test_updates_are_persisted(uow: UnitOfWork) -> None:
    inst, acc, cat = populate(uow)
    with uow as work:
        work.institutions.update(Institution(inst.id, inst.slug, "Novo", None, "#112233", None))
        work.accounts.update(
            Account(acc.id, acc.kind, acc.institution_id, acc.nickname, False, "#445566", None)
        )
        work.categories.update(
            Category(cat.id, cat.slug, cat.name, cat.group, cat.kind, 50_000, "#778899")
        )
        work.commit()
    with uow as work:
        got_inst = work.institutions.get(inst.id)
        got_acc = work.accounts.get(acc.id)
        got_cat = work.categories.get(cat.id)
        assert got_inst and (got_inst.name, got_inst.color) == ("Novo", "#112233")
        assert got_acc and (got_acc.is_active, got_acc.color) == (False, "#445566")
        assert got_cat and (got_cat.monthly_budget_cents, got_cat.color) == (50_000, "#778899")


def test_without_commit_nothing_is_kept(uow: UnitOfWork) -> None:
    with uow as work:
        work.institutions.add(institution("temp"))
    with uow as work:
        assert work.institutions.list_all() == []


def test_an_exception_rolls_back_everything(uow: UnitOfWork) -> None:
    with pytest.raises(RuntimeError), uow as work:
        work.institutions.add(institution("temp"))
        raise RuntimeError
    with uow as work:
        assert work.institutions.list_all() == []


def test_categories_by_slug_and_order(uow: UnitOfWork) -> None:
    populate(uow)
    with uow as work:
        assert [c.slug for c in work.categories.list_all()] == ["food", "salary"]
        found = work.categories.get_by_slug("salary")
        assert found and found.kind is CategoryKind.INCOME


def test_transactions_round_trip_and_ranges(uow: UnitOfWork) -> None:
    _, acc, cat = populate(uow)
    rows = [
        tx(1, acc, cat, D(2026, 7, 1), -100, is_recurring=True, notes="nota"),
        tx(2, acc, cat, D(2026, 7, 31), -200),
        tx(3, acc, cat, D(2026, 7, 31), -300),
        tx(4, acc, cat, D(2026, 8, 1), -400),
    ]
    with uow as work:
        work.transactions.add_many(rows)
        work.commit()
    with uow as work:
        assert work.transactions.get(rows[0].id) == rows[0]
        assert work.transactions.get("z" * 32) is None
        july = work.transactions.list_between(D(2026, 7, 1), D(2026, 7, 31))
        # newest date first; same day: last inserted first
        assert [t.id for t in july] == [rows[2].id, rows[1].id, rows[0].id]
        assert work.transactions.list_between(D(2026, 7, 1), D(2026, 7, 31), "nope") == []
        assert len(work.transactions.list_between(D(2026, 7, 1), D(2026, 8, 1), acc.id)) == 4


def test_transfer_legs_and_delete(uow: UnitOfWork) -> None:
    inst, acc, cat = populate(uow)
    acc2 = account(inst, 2)
    legs = [
        tx(1, acc, cat, D(2026, 7, 1), -500, kind=TransactionKind.TRANSFER, transfer_id="x" * 32),
        tx(2, acc2, cat, D(2026, 7, 1), 500, kind=TransactionKind.TRANSFER, transfer_id="x" * 32),
        tx(3, acc, cat, D(2026, 7, 2), -1),
    ]
    with uow as work:
        work.accounts.add(acc2)
        work.transactions.add_many(legs)
        work.commit()
    with uow as work:
        assert [t.id for t in work.transactions.list_by_transfer("x" * 32)] == [
            legs[0].id,
            legs[1].id,
        ]
        work.transactions.delete(legs[0].id)
        work.commit()
    with uow as work:
        assert work.transactions.get(legs[0].id) is None
        assert work.transactions.get(legs[1].id) is not None


def test_movements_per_account(uow: UnitOfWork) -> None:
    inst, acc, cat = populate(uow)
    acc2 = account(inst, 2)
    with uow as work:
        work.accounts.add(acc2)
        work.transactions.add_many(
            [tx(1, acc, cat, D(2026, 7, 1), -100), tx(2, acc2, cat, D(2026, 7, 2), -7)]
        )
        work.commit()
    with uow as work:
        assert work.transactions.movements(acc.id) == [(D(2026, 7, 1), -100)]


def test_last_category_is_the_latest_by_date_for_the_same_key_and_kind(uow: UnitOfWork) -> None:
    _, acc, food = populate(uow)
    other = category("other")
    with uow as work:
        work.categories.add(other)
        work.transactions.add_many(
            [
                tx(1, acc, food, D(2026, 6, 1), -1, description="padaria"),
                tx(2, acc, other, D(2026, 7, 1), -1, description="padaria"),
                tx(3, acc, food, D(2026, 5, 1), -1, description="padaria"),
            ]
        )
        work.commit()
    with uow as work:
        assert work.transactions.last_category_id("padaria", TransactionKind.EXPENSE) == other.id
        assert work.transactions.last_category_id("padaria", TransactionKind.INCOME) is None
        assert work.transactions.last_category_id("outra", TransactionKind.EXPENSE) is None


def test_accented_text_survives_storage(uow: UnitOfWork) -> None:
    _, acc, cat = populate(uow)
    row = tx(1, acc, cat, D(2026, 7, 1), -1, description="Café São João, Ação", notes="Não")
    with uow as work:
        work.transactions.add_many([row])
        work.commit()
    with uow as work:
        got = work.transactions.get(row.id)
        assert got and got.description == "Café São João, Ação" and got.notes == "Não"


def test_anchor_upsert_is_unique_per_account_and_date(uow: UnitOfWork) -> None:
    _, acc, _ = populate(uow)
    with uow as work:
        work.anchors.upsert(BalanceAnchor("a" * 32, acc.id, D(2026, 7, 10), 100, "um"))
        work.anchors.upsert(BalanceAnchor("b" * 32, acc.id, D(2026, 7, 1), 50))
        work.anchors.upsert(BalanceAnchor("c" * 32, acc.id, D(2026, 7, 10), 999, "dois"))
        work.commit()
    with uow as work:
        got = work.anchors.list_for_account(acc.id)
        assert [(a.on_date, a.balance_cents, a.note) for a in got] == [
            (D(2026, 7, 1), 50, None),
            (D(2026, 7, 10), 999, "dois"),
        ]
        assert work.anchors.list_for_account("nope") == []
