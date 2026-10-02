"""Every repository behaves the same on the in-memory and on the SQLite implementation."""

import datetime as dt

import pytest

from fakes import MemoryUnitOfWork
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    BalanceAnchor,
    Category,
    CategoryGroup,
    CategoryKind,
    HoldingStatus,
    Indexer,
    InstallmentPlan,
    Institution,
    InstrumentType,
    InvestmentHolding,
    InvestmentTracking,
    Liquidity,
    RateMode,
    Statement,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
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


# --- cards (Phase 2) ---


def card(inst: Institution, n: int = 9) -> Account:
    return Account(
        id=f"k{n}".ljust(32, "0"),
        kind=AccountKind.CREDIT_CARD,
        institution_id=inst.id,
        nickname="Cartão",
        closing_days_before_due=11,
        due_day=5,
        credit_limit_cents=1_200_000,
    )


def statement(acc: Account, month: str, n: int = 1) -> Statement:
    ym = YearMonth.parse(month)
    return Statement(f"s{n}".ljust(32, "0"), acc.id, ym, ym.day(25), ym.add_months(1).day(5))


def test_card_account_round_trip(uow: UnitOfWork) -> None:
    inst, _, _ = populate(uow)
    k = card(inst)
    with uow as work:
        work.accounts.add(k)
        work.commit()
    with uow as work:
        assert work.accounts.get(k.id) == k
        work.accounts.update(
            Account(k.id, k.kind, k.institution_id, k.nickname, True, None, None, 7, 17, None)
        )
        work.commit()
    with uow as work:
        got = work.accounts.get(k.id)
        assert got and (got.closing_days_before_due, got.due_day, got.credit_limit_cents) == (
            7,
            17,
            None,
        )


def test_statements_are_unique_per_card_and_month_and_ordered(uow: UnitOfWork) -> None:
    inst, _, _ = populate(uow)
    k = card(inst)
    with uow as work:
        work.accounts.add(k)
        work.statements.add(statement(k, "2026-09", 2))
        work.statements.add(statement(k, "2026-08", 1))
        work.commit()
    with uow as work:
        assert [str(s.month) for s in work.statements.list_for_card(k.id)] == ["2026-08", "2026-09"]
        found = work.statements.get_by_card_month(k.id, YearMonth(2026, 9))
        assert found and found.due_date == D(2026, 10, 5)
        assert work.statements.get_by_card_month(k.id, YearMonth(2026, 10)) is None
        assert work.statements.get("z" * 32) is None
        assert len(work.statements.list_all()) == 2
    with pytest.raises(Exception), uow as work:  # noqa: B017  (IntegrityError or ValueError)
        work.statements.add(statement(k, "2026-09", 3))
        work.commit()


def test_statement_update_keeps_informed_total(uow: UnitOfWork) -> None:
    inst, _, _ = populate(uow)
    k = card(inst)
    st = statement(k, "2026-08")
    with uow as work:
        work.accounts.add(k)
        work.statements.add(st)
        work.commit()
    with uow as work:
        work.statements.update(
            Statement(st.id, st.account_id, st.month, D(2026, 8, 24), D(2026, 9, 6), 235_646)
        )
        work.commit()
    with uow as work:
        got = work.statements.get(st.id)
        assert got and (got.closing_date, got.due_date, got.informed_total_cents) == (
            D(2026, 8, 24),
            D(2026, 9, 6),
            235_646,
        )


def test_plan_transactions_and_lookups(uow: UnitOfWork) -> None:
    inst, acc, cat = populate(uow)
    k = card(inst)
    st1, st2 = statement(k, "2026-08", 1), statement(k, "2026-09", 2)
    plan = InstallmentPlan("p" * 32, k.id, "Fone", cat.id, 2, D(2026, 7, 26))
    rows = [
        tx(
            1,
            k,
            cat,
            D(2026, 7, 26),
            -10_034,
            plan_id=plan.id,
            installment_number=1,
            statement_id=st1.id,
        ),
        tx(
            2,
            k,
            cat,
            D(2026, 9, 25),
            -10_033,
            plan_id=plan.id,
            installment_number=2,
            statement_id=st2.id,
        ),
        tx(3, k, cat, D(2026, 8, 3), -500, statement_id=st1.id),
    ]
    with uow as work:
        work.accounts.add(k)
        work.statements.add(st1)
        work.statements.add(st2)
        work.plans.add(plan)
        work.transactions.add_many(rows)
        work.commit()
    with uow as work:
        assert work.plans.get(plan.id) == plan
        assert work.plans.list_all() == [plan]
        assert [t.installment_number for t in work.transactions.list_by_plan(plan.id)] == [1, 2]
        assert {t.id for t in work.transactions.list_by_statement(st1.id)} == {
            rows[0].id,
            rows[2].id,
        }
        assert len(work.transactions.list_by_account(k.id)) == 3
        assert work.transactions.list_by_account(acc.id) == []
        got = work.transactions.get(rows[1].id)
        assert got and (got.plan_id, got.installment_number, got.statement_id) == (
            plan.id,
            2,
            st2.id,
        )


def test_update_amount_and_set_statement(uow: UnitOfWork) -> None:
    inst, _, cat = populate(uow)
    k = card(inst)
    st1, st2 = statement(k, "2026-08", 1), statement(k, "2026-09", 2)
    row = tx(1, k, cat, D(2026, 8, 3), -500, statement_id=st1.id)
    with uow as work:
        work.accounts.add(k)
        work.statements.add(st1)
        work.statements.add(st2)
        work.transactions.add_many([row])
        work.commit()
    with uow as work:
        work.transactions.update_amount(row.id, -750)
        work.transactions.set_statement(row.id, st2.id)
        work.commit()
    with uow as work:
        got = work.transactions.get(row.id)
        assert got and (got.amount_cents, got.statement_id) == (-750, st2.id)


def test_plan_delete(uow: UnitOfWork) -> None:
    inst, _, cat = populate(uow)
    k = card(inst)
    plan = InstallmentPlan("p" * 32, k.id, "Fone", cat.id, 3, None)
    with uow as work:
        work.accounts.add(k)
        work.plans.add(plan)
        work.commit()
    with uow as work:
        work.plans.delete(plan.id)
        work.commit()
    with uow as work:
        assert work.plans.get(plan.id) is None


def test_competence_uses_the_statement_month_for_card_entries(uow: UnitOfWork) -> None:
    inst, acc, cat = populate(uow)
    k = card(inst)
    august = statement(k, "2026-08", 1)
    rows = [
        tx(1, acc, cat, D(2026, 7, 26), -100),  # checking: counts in July (posted_on)
        tx(2, k, cat, D(2026, 7, 26), -200, statement_id=august.id),  # card: counts in August
        tx(3, k, cat, D(2026, 8, 25), -300, statement_id=august.id),
        tx(4, acc, cat, D(2026, 8, 1), -400),
    ]
    with uow as work:
        work.accounts.add(k)
        work.statements.add(august)
        work.transactions.add_many(rows)
        work.commit()
    with uow as work:
        july = {t.id for t in work.transactions.list_for_competence(D(2026, 7, 1), D(2026, 7, 31))}
        aug = {t.id for t in work.transactions.list_for_competence(D(2026, 8, 1), D(2026, 8, 31))}
        year = work.transactions.list_for_competence(D(2026, 1, 1), D(2026, 12, 31))
    assert july == {rows[0].id}
    assert aug == {rows[1].id, rows[2].id, rows[3].id}
    assert len(year) == 4


def test_investment_account_and_gross_balance_round_trip(uow: UnitOfWork) -> None:
    inst, _, _ = populate(uow)
    fund = Account(
        id="f".ljust(32, "0"),
        kind=AccountKind.INVESTMENT,
        institution_id=inst.id,
        nickname="Tesouro",
        tracking=InvestmentTracking.ACCOUNT,
        asset_class=AssetClass.FIXED_INCOME,
        is_emergency_fund=True,
    )
    with uow as work:
        work.accounts.add(fund)
        work.anchors.upsert(
            BalanceAnchor("a" * 32, fund.id, D(2026, 7, 31), 1_000_000, "x", 1_020_000)
        )
        work.commit()
    with uow as work:
        assert work.accounts.get(fund.id) == fund
        (anchor,) = work.anchors.list_for_account(fund.id)
        assert (anchor.balance_cents, anchor.gross_balance_cents, anchor.note) == (
            1_000_000,
            1_020_000,
            "x",
        )
        work.anchors.upsert(BalanceAnchor("b" * 32, fund.id, D(2026, 7, 31), 1_010_000, None, None))
        work.commit()
    with uow as work:
        (anchor,) = work.anchors.list_for_account(fund.id)
        assert (anchor.balance_cents, anchor.gross_balance_cents) == (1_010_000, None)


# --- holdings (Phase 3b) ---


def broker(inst: Institution) -> Account:
    return Account(
        id="b".ljust(32, "0"),
        kind=AccountKind.INVESTMENT,
        institution_id=inst.id,
        nickname="Corretora",
        tracking=InvestmentTracking.HOLDINGS,
        asset_class=AssetClass.FIXED_INCOME,
    )


def holding(acc: Account, inst: Institution, n: int = 1, **kw: object) -> InvestmentHolding:
    base: dict[str, object] = {
        "id": f"h{n}".ljust(32, "0"),
        "account_id": acc.id,
        "name": f"CDB {n}",
        "instrument_type": InstrumentType.CDB,
        "issuer_id": inst.id,
        "indexer": Indexer.CDI,
        "rate_mode": RateMode.PERCENT_OF_INDEX,
        "rate_bps": 11_000,
        "applied_on": D(2026, 3, 1),
        "principal_cents": 1_000_000,
        "maturity_on": D(2028, 3, 1),
        "liquidity": Liquidity.AT_MATURITY,
        "liquid_from": None,
        "fgc_covered": True,
        "is_emergency_fund": False,
        "asset_class": AssetClass.FIXED_INCOME,
        "status": HoldingStatus.ACTIVE,
    }
    base.update(kw)
    return InvestmentHolding(**base)  # type: ignore[arg-type]


def test_holding_round_trip_update_and_listing(uow: UnitOfWork) -> None:
    inst, _, _ = populate(uow)
    acc = broker(inst)
    h1, h2 = (
        holding(acc, inst, 1),
        holding(acc, inst, 2, liquidity=Liquidity.DAILY, maturity_on=None),
    )
    with uow as work:
        work.accounts.add(acc)
        work.holdings.add(h1)
        work.holdings.add(h2)
        work.commit()
    with uow as work:
        assert work.holdings.get(h1.id) == h1 and work.holdings.get("z" * 32) is None
        assert [h.name for h in work.holdings.list_all()] == ["CDB 1", "CDB 2"]
        assert work.holdings.list_for_account(acc.id) == [h1, h2]
        assert work.holdings.list_for_account("nope") == []
        work.holdings.update(
            holding(
                acc,
                inst,
                1,
                status=HoldingStatus.REDEEMED,
                fgc_covered=False,
                is_emergency_fund=True,
            )
        )
        work.commit()
    with uow as work:
        got = work.holdings.get(h1.id)
        assert got and (got.status, got.fgc_covered, got.is_emergency_fund) == (
            HoldingStatus.REDEEMED,
            False,
            True,
        )


def test_valuations_and_flows_are_kept_per_holding(uow: UnitOfWork) -> None:
    inst, _, cat = populate(uow)
    acc = broker(inst)
    h1, h2 = holding(acc, inst, 1), holding(acc, inst, 2)
    flow = tx(1, acc, cat, D(2026, 7, 10), 100_000, kind=TransactionKind.TRANSFER, holding_id=h1.id)
    other = tx(2, acc, cat, D(2026, 7, 11), 50_000, kind=TransactionKind.TRANSFER, holding_id=h2.id)
    with uow as work:
        work.accounts.add(acc)
        work.holdings.add(h1)
        work.holdings.add(h2)
        work.transactions.add_many([flow, other])
        work.anchors.upsert(
            BalanceAnchor("a" * 32, acc.id, D(2026, 7, 31), 1_000, None, None, h1.id)
        )
        work.anchors.upsert(
            BalanceAnchor("b" * 32, acc.id, D(2026, 7, 31), 2_000, None, None, h2.id)
        )
        work.anchors.upsert(BalanceAnchor("c" * 32, acc.id, D(2026, 7, 31), 9_000))  # whole account
        work.commit()
    with uow as work:
        assert work.transactions.movements_for_holding(h1.id) == [(D(2026, 7, 10), 100_000)]
        assert work.transactions.get(flow.id).holding_id == h1.id  # type: ignore[union-attr]
        assert [a.balance_cents for a in work.anchors.list_for_holding(h1.id)] == [1_000]
        assert [a.balance_cents for a in work.anchors.list_for_holding(h2.id)] == [2_000]
        assert [a.balance_cents for a in work.anchors.list_for_account(acc.id)] == [9_000]
        # the same day again replaces the valuation of that holding only
        work.anchors.upsert(
            BalanceAnchor("d" * 32, acc.id, D(2026, 7, 31), 1_500, "x", None, h1.id)
        )
        work.commit()
    with uow as work:
        assert [(a.balance_cents, a.note) for a in work.anchors.list_for_holding(h1.id)] == [
            (1_500, "x")
        ]
        assert [a.balance_cents for a in work.anchors.list_for_holding(h2.id)] == [2_000]
        assert [a.balance_cents for a in work.anchors.list_for_account(acc.id)] == [9_000]


def test_units_of_work_do_not_share_state_between_threads_or_nested_blocks(uow: UnitOfWork) -> None:
    import threading

    populate(uow)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(30):
                with uow as work:
                    assert [i.slug for i in work.institutions.list_all()] == ["bb"]
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with uow as outer:  # nesting works: the inner block has its own session
        with uow as inner:
            assert inner.institutions.get_by_slug("bb") is not None
        assert outer.institutions.get_by_slug("bb") is not None
