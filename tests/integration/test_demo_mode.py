"""Demo mode: ``financas demo`` / ``--demo`` run on ``data/demo.db`` and never touch the real data.

Every test works in a temporary ``data`` folder that holds a *real* database with a marker row; its
files are fingerprinted (SHA-256 of every file outside the demo's own names) before and after.
"""

import datetime as dt
import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from fakes import FixedClock
from financas.application.queries.balances import ListAccountBalances
from financas.application.queries.cards import ListCards
from financas.application.queries.charts import GetNetWorthSeries
from financas.application.queries.investments import GetNetWorth
from financas.application.use_cases.catalog import CreateInstitution, CreateInstitutionCommand
from financas.container import Container, build_container
from financas.domain.models import AccountKind, StatementStatus, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.rules import validate_category_kind, validate_sign
from financas.infrastructure.db.demo_seed import demo_is_empty, seed_demo, wipe_demo_files
from financas.infrastructure.settings import Settings
from financas.interfaces import cli
from financas.interfaces.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 10, 4)
HEADERS = {"host": "localhost", "origin": "http://localhost"}
runner = CliRunner()


# --- a throwaway project folder with a "real" database --------------------------------------


def fingerprint(data: Path) -> dict[str, str]:
    """SHA-256 of every file under ``data`` that is not one of the demo's own files."""
    result = {}
    for path in sorted(data.rglob("*")):
        relative = path.relative_to(data)
        if path.is_file() and not relative.parts[0].startswith("demo"):
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``tmp/data`` with a real database, backups, images and logs; the environment points there."""
    data = tmp_path / "data"
    for name in [n for n in os.environ if n.startswith("FINANCAS_")]:
        monkeypatch.delenv(name)  # a developer's own settings must not reach the test
    monkeypatch.setitem(Settings.model_config, "env_file", None)  # nor the project's .env
    monkeypatch.setenv("FINANCAS_DATA_DIR", str(data))
    monkeypatch.setenv("FINANCAS_DB_URL", f"sqlite:///{data / 'financas.db'}")
    real = build_container()
    real.migrate()
    real.seed()
    CreateInstitution(real.uow).execute(CreateInstitutionCommand(name="Banco Real Marcador"))
    real.backup()
    (data / "images").mkdir(exist_ok=True)
    (data / "images" / "logo.png").write_bytes(b"\x89PNG real")
    (data / "logs").mkdir(exist_ok=True)
    (data / "logs" / "failures.jsonl").write_text("{}\n", encoding="utf-8")
    real.close()
    return data


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A demo database seeded once on the fixed date (seeding takes seconds); tests copy it."""
    data = tmp_path_factory.mktemp("template") / "data"
    settings = Settings(
        db_url=f"sqlite:///{data / 'financas.db'}",
        data_dir=data,
        _env_file=None,  # type: ignore[call-arg]
    ).for_demo()
    c = Container(settings)
    c.__dict__["clock"] = FixedClock(TODAY)
    c.ensure_demo()
    c.close()
    return settings.demo_db_path


def demo_container(
    project: Path, today: dt.date = TODAY, template: Path | None = None
) -> Container:
    """A demo container on a fixed date, seeded (copied from ``template`` when given)."""
    if template is not None:
        shutil.copy(template, project / "demo.db")
    c = build_container(demo=True)
    c.__dict__["clock"] = FixedClock(today)  # cached_property: the fixed date wins
    c.ensure_demo()
    return c


@pytest.fixture
def demo(project: Path, seeded_template: Path) -> Iterator[Container]:
    c = demo_container(project, template=seeded_template)
    yield c
    c.close()


def captured_app(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``uvicorn.run`` so a server command returns at once, keeping the app it got."""
    seen: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        seen["app"], seen["kwargs"] = app, kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    return seen


# --- isolation of paths ------------------------------------------------------------------------


def test_demo_settings_isolate_every_path(project: Path) -> None:
    real = Settings()
    demo = real.for_demo()
    assert demo.demo and not real.demo
    assert real.db_path == project / "financas.db" and demo.db_path == project / "demo.db"
    pairs = [
        (real.images_dir, demo.images_dir, "demo_images"),
        (real.backups_dir, demo.backups_dir, "demo_backups"),
        (real.logs_dir, demo.logs_dir, "demo_logs"),
        (real.import_dir, demo.import_dir, "demo_import"),
    ]
    for production, isolated, name in pairs:
        assert isolated == project / name and isolated != production
    assert demo.for_demo() is demo


def test_demo_settings_refuse_a_foreign_or_real_database(project: Path) -> None:
    with pytest.raises(ValueError, match="DEMO_DB_PATH_NOT_ISOLATED"):
        Settings(demo=True, db_url=f"sqlite:///{project / 'financas.db'}")
    with pytest.raises(ValueError, match="DEMO_DB_IS_THE_REAL_DB"):
        Settings(db_url=f"sqlite:///{project / 'demo.db'}").for_demo()


def test_wipe_refuses_anything_that_is_not_the_demo(project: Path) -> None:
    before = fingerprint(project)
    with pytest.raises(ValueError, match="NOT_THE_DEMO_DATABASE"):
        wipe_demo_files(Settings())  # the real settings
    assert fingerprint(project) == before and (project / "financas.db").is_file()


def test_gitignore_keeps_the_real_data_and_the_demo_side_files_out_of_git() -> None:
    paths = [
        "data/financas.db",
        "data/financas.db-wal",
        "data/financas.db-shm",
        "data/backups/financas-20261004-120000/financas.db",
        "data/images/logo.png",
        "data/demo_images/logo.png",
        "data/demo_backups/x/financas.db",
    ]
    result = subprocess.run(
        ["git", "check-ignore", *paths], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.stdout.split() == paths, result.stderr


# --- commands ------------------------------------------------------------------------------------


def test_seed_demo_command_creates_and_recreates_only_the_demo(project: Path) -> None:
    before = fingerprint(project)
    created = runner.invoke(cli.app, ["seed-demo"])
    assert created.exit_code == 0, created.output
    assert "Banco de demonstração criado" in created.output and (project / "demo.db").is_file()
    again = runner.invoke(cli.app, ["seed-demo"])
    assert again.exit_code == 0 and "já tem dados" in again.output
    stamp = (project / "demo.db").stat().st_mtime_ns
    forced = runner.invoke(cli.app, ["seed-demo", "--force"])
    assert forced.exit_code == 0 and "recriado" in forced.output, forced.output
    assert re.search(r"6 contas, \d+ lançamentos, \d+ faturas, 4 compras parceladas", forced.output)
    assert (project / "demo.db").stat().st_mtime_ns != stamp
    assert fingerprint(project) == before


def test_demo_command_serves_the_demo_database_and_serve_the_real_one(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = fingerprint(project)
    seen = captured_app(monkeypatch)
    result = runner.invoke(cli.app, ["demo", "--port", "8123"])
    assert result.exit_code == 0, result.output
    app, kwargs = seen["app"], seen["kwargs"]
    assert kwargs["port"] == 8123 and kwargs["host"] == "127.0.0.1"
    settings = app.state.container.settings
    assert settings.demo and settings.db_path == project / "demo.db"
    page = TestClient(app, base_url="http://localhost", headers=HEADERS).get("/")
    assert "Modo demonstração" in page.text and "Dados fictícios de demonstração" in page.text
    assert "Banco Real Marcador" not in page.text
    assert fingerprint(project) == before

    seen.clear()
    assert runner.invoke(cli.app, ["serve"]).exit_code == 0
    real = seen["app"].state.container.settings
    assert not real.demo and real.db_path == project / "financas.db"
    home = TestClient(seen["app"], base_url="http://localhost", headers=HEADERS).get("/")
    assert (
        "Modo demonstração" not in home.text and "Dados guardados só neste computador" in home.text
    )

    seen.clear()
    assert runner.invoke(cli.app, ["serve", "--demo"]).exit_code == 0
    assert seen["app"].state.container.settings.db_path == project / "demo.db"


def test_demo_flag_is_on_the_read_commands_and_routes_to_the_demo(project: Path) -> None:
    before = fingerprint(project)
    runner.invoke(cli.app, ["seed-demo"])
    for args in (
        ["summary", "--demo", "--month", "2026-09"],
        ["networth", "--demo"],
        ["list", "--demo", "--month", "2026-09"],
        ["statement", "list", "--demo"],
        ["card", "list", "--demo"],
        ["balance", "list", "--demo"],
        ["invest", "list", "--demo"],
        ["budget", "show", "--demo"],
    ):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0, (args, result.output)
        assert "Banco Real Marcador" not in result.output
    summary = runner.invoke(cli.app, ["summary", "--demo", "--month", "2026-09"])
    assert "Salário" not in summary.output and "Receitas:  R$ 8.900,00" in summary.output
    assert (
        "R$ 0,00" in runner.invoke(cli.app, ["summary", "--month", "2026-09"]).output
    )  # real: empty
    backup = runner.invoke(cli.app, ["backup", "--demo"])
    assert backup.exit_code == 0 and "demo_backups" in "".join(backup.output.split())
    assert len(list((project / "demo_backups").glob("financas-*"))) == 1
    assert fingerprint(project) == before


def test_a_demo_flag_on_a_fresh_checkout_builds_the_demo_by_itself(project: Path) -> None:
    assert not (project / "demo.db").exists()
    result = runner.invoke(cli.app, ["networth", "--demo"])
    assert result.exit_code == 0 and (project / "demo.db").is_file(), result.output
    assert "Patrimônio" in result.output or "patrimônio" in result.output


def test_the_import_commands_have_no_demo_flag() -> None:
    result = runner.invoke(cli.app, ["import", "plan", "--help"])
    assert "--demo" not in result.output and "--year" in result.output
    assert "--demo" in runner.invoke(cli.app, ["summary", "--help"]).output


# --- the synthetic data -------------------------------------------------------------------------


def test_demo_data_has_the_planned_shape(demo: Container) -> None:
    with demo.uow as work:
        accounts = {a.nickname: a for a in work.accounts.list_all()}
        institutions = {i.name: i for i in work.institutions.list_all()}
        plans = work.plans.list_all()
        holdings = work.holdings.list_all()
        entries = work.transactions.list_between(dt.date.min, dt.date.max)
    assert set(institutions) == {"Banco do Brasil", "Nubank", "Banco Inter", "Tesouro Nacional"}
    kinds = sorted(a.kind.value for a in accounts.values())
    assert kinds == ["checking"] * 2 + ["credit_card"] * 3 + ["investment"]
    cards = {a.institution_id: a for a in accounts.values() if a.kind is AccountKind.CREDIT_CARD}
    bb, nu, inter = (institutions[n].id for n in ("Banco do Brasil", "Nubank", "Banco Inter"))
    assert (cards[bb].due_day, cards[bb].closing_days_before_due) == (5, 11)
    assert (cards[nu].due_day, cards[nu].closing_days_before_due) == (26, 7)
    assert (cards[inter].due_day, cards[inter].closing_days_before_due) == (20, 6)
    assert {h.name for h in holdings} == {"Tesouro Selic 2029", "CDB Banco Inter 100% CDI"}
    assert len(plans) == 4  # three plain plans and the itemized one
    assert min(t.posted_on for t in entries) >= dt.date(2026, 1, 1)
    descriptions = {t.description for t in entries}
    for expected in ("Salário CLT", "Aluguel", "Condomínio", "iFood", "Uber", "Netflix", "Spotify"):
        assert expected in descriptions
    assert any(d.startswith("Farmácia") or d in {"Drogasil", "Droga Raia"} for d in descriptions)


def test_demo_showcases_itemizing_merging_and_refunds(demo: Container) -> None:
    """Every capability the user can reach from the screens has data in the demo (CLAUDE.md 14)."""
    from financas.application.queries.cards import statement_view
    from financas.domain.services.splits import validate_split_amounts

    with demo.uow as work:
        everything = work.transactions.list_between(dt.date.min, dt.date.max, include_refunded=True)
        splits = work.transactions.splits_for(t.id for t in everything)
        by_id = {t.id: t for t in everything}
        assert splits, "an itemized entry is seeded"
        for parent_id, items in splits.items():  # the sum invariant holds for every one
            parent = by_id[parent_id]
            validate_split_amounts(-parent.amount_cents, [i.amount_cents for i in items])
        run = next(t for t in everything if t.description == "Compra do mês no Pão de Açúcar")
        assert [i.amount_cents for i in splits[run.id]] == [22_000, 9_000, 7_000]
        assert len({i.category_id for i in splits[run.id]}) == 3
        assert run.amount_cents == -38_000  # the parent keeps the whole amount

        # two plain purchases of one card, one day and one statement: the "Mesclar" demo
        pair = [t for t in everything if t.description in {"Feira orgânica", "Produtos de limpeza"}]
        assert len(pair) == 2
        assert len({(t.account_id, t.statement_id, t.posted_on) for t in pair}) == 1
        assert all(t.plan_id is None and not t.is_refunded and t.id not in splits for t in pair)

        # a refunded purchase: listed, flagged, and out of its statement's total
        returned = next(t for t in everything if t.is_refunded)
        assert returned.kind is TransactionKind.EXPENSE and returned.statement_id
        statement = work.statements.get(returned.statement_id)
        assert statement
        view = statement_view(work, statement, TODAY)
        counted = [t for t in work.transactions.list_by_statement(statement.id)]
        assert returned.id not in {t.id for t in counted}
        assert view.total_cents == -sum(t.amount_cents for t in counted)


def test_demo_summary_counts_the_showcase(project: Path, seeded_template: Path) -> None:
    c = demo_container(project, template=seeded_template)
    try:
        with c.uow as work:
            everything = work.transactions.list_between(
                dt.date.min, dt.date.max, include_refunded=True
            )
            assert (
                len(work.transactions.splits_for(t.id for t in everything)) == 5
            )  # the supermarket run, the PIX split and the three installments of the itemized plan
            assert sum(1 for t in everything if t.is_refunded) == 1
    finally:
        c.close()


def test_installment_plans_put_the_remainder_on_the_first_installment(demo: Container) -> None:
    expected = {"Smartphone Galaxy 10x": (349_907, 10), "Sofá retrátil 6x": (329_999, 6)}
    with demo.uow as work:
        plans = {p.description: p for p in work.plans.list_all()}
        assert set(plans) >= set(expected) and len(plans) == 4
        for description, plan in plans.items():
            parts = sorted(
                work.transactions.list_by_plan(plan.id), key=lambda t: t.installment_number or 0
            )
            amounts = [-t.amount_cents for t in parts]
            assert len(parts) == plan.installment_total
            assert [t.installment_number for t in parts] == list(range(1, len(parts) + 1))
            assert amounts[0] >= amounts[1] and amounts[0] - amounts[1] <= len(parts) - 1
            assert len(set(amounts[1:])) == 1  # the others are equal
            if description in expected:
                total, n = expected[description]
                assert sum(amounts) == total and amounts[1] == total // n
                assert amounts[0] == total // n + total % n
        statements = {
            t.statement_id for p in plans.values() for t in work.transactions.list_by_plan(p.id)
        }
        assert len(statements) >= 6  # installments spread over many statements


def test_demo_has_an_itemized_installment_plan_that_reconciles_to_the_cent(demo: Container) -> None:
    from financas.domain.services.splits import allocations

    with demo.uow as work:
        plan = next(p for p in work.plans.list_all() if p.description.startswith("Monitor gamer"))
        parts = work.transactions.list_by_plan(plan.id)
        splits = work.transactions.splits_for([t.id for t in parts])
        shopping = work.categories.get_by_slug("shopping")
        home = work.categories.get_by_slug("home")
    assert shopping and home and len(parts) == 3
    assert [-t.amount_cents for t in parts] == [15_000] * 3  # R$ 450,00 in 3 x R$ 150,00
    assert all(t.category_id is None for t in parts)  # no installment has a category of its own
    rows = [[(i.description, i.amount_cents) for i in splits[t.id]] for t in parts]
    assert rows == [  # 117,00 + 33,00 · 117,00 + 33,00 · 116,00 + 34,00
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],
        [("Monitor Gamer", 11_666), ("Cabo HDMI e Suporte", 3_334)],
    ]
    for t in parts:  # every installment adds up, and so does what the categories see
        assert sum(c for _, c in allocations(t, splits)) == t.amount_cents
    assert sum(i.amount_cents for t in parts for i in splits[t.id]) == 45_000
    assert plan.category_id == shopping.id


def test_demo_entries_list_consolidates_every_installment_plan_into_one_purchase(
    demo: Container,
) -> None:
    from financas.application.queries.plan_purchases import ListPlanPurchases

    purchases = ListPlanPurchases(demo.uow).execute()
    with demo.uow as work:
        plans = work.plans.list_all()
    assert len(purchases) == len(plans) >= 4  # one row per plan, none lost
    monitor = next(p for p in purchases if p.plan.description.startswith("Monitor gamer"))
    assert (monitor.total_cents, monitor.count, monitor.installment_cents) == (45_000, 3, 15_000)
    assert [(i.description, i.total_cents) for i in monitor.items] == [
        ("Monitor Gamer", 35_000),
        ("Cabo HDMI e Suporte", 10_000),
    ]
    assert monitor.items[0].breakdown == ((2, 11_667), (1, 11_666))  # 350,00 over three statements


def test_cards_show_every_status_and_both_limit_badges(demo: Container) -> None:
    overview = ListCards(demo.uow, demo.clock).execute()
    alerts = {c.account.nickname: c.usage.alert.value for c in overview.cards}
    percents = {c.account.nickname: c.usage.percent or 0.0 for c in overview.cards}
    assert alerts["Nubank Roxinho"] == "warning" and 80 <= percents["Nubank Roxinho"] < 100
    assert percents["Ourocard BB"] < 80 and percents["Inter Black"] < 80
    statuses = {s.status for c in overview.cards for s in c.statements}
    assert statuses == set(StatementStatus)
    bb = next(c for c in overview.cards if c.account.nickname == "Ourocard BB")
    reconciled = [s for s in bb.statements if s.reconciliation.difference_cents]
    assert reconciled and reconciled[0].status is StatementStatus.CLOSED


def test_every_entry_honors_the_domain_rules(demo: Container) -> None:
    with demo.uow as work:
        categories = {c.id: c for c in work.categories.list_all()}
        accounts = {a.id: a for a in work.accounts.list_all()}
        entries = work.transactions.list_between(dt.date.min, dt.date.max)
        institutions = work.institutions.list_all()
        statements = {s.id: s for s in work.statements.list_all()}
    assert len(entries) > 300
    for t in entries:
        validate_sign(t.kind, t.amount_cents)
        if t.category_id is not None:  # only an itemized expense has none: its items do
            validate_category_kind(t.kind, categories[t.category_id].kind)
        else:
            assert t.kind is TransactionKind.EXPENSE
        if t.statement_id is not None:  # statement x account
            assert accounts[t.account_id].kind is AccountKind.CREDIT_CARD
            assert statements[t.statement_id].account_id == t.account_id
    transfers: dict[str, list[int]] = {}
    for t in entries:
        if t.kind is TransactionKind.TRANSFER and t.transfer_id:
            transfers.setdefault(t.transfer_id, []).append(t.amount_cents)
    assert all(sum(legs) == 0 for legs in transfers.values())  # both legs, opposite amounts
    assert all(
        c is None or re.fullmatch(r"#[0-9A-F]{6}", c) for c in (i.color for i in institutions)
    )


def test_no_checking_account_is_ever_overdrawn(demo: Container) -> None:
    with demo.uow as work:
        for account in work.accounts.list_all():
            if account.kind is AccountKind.CHECKING:
                anchors = work.anchors.list_for_account(account.id)
                assert len(anchors) >= 10 and all(a.balance_cents > 0 for a in anchors)
    with demo.uow as work:
        checking = {a.id for a in work.accounts.list_all() if a.kind is AccountKind.CHECKING}
    day = dt.date(2026, 1, 1)
    while day <= TODAY:  # every third day, not only the informed month ends
        rows = ListAccountBalances(demo.uow).execute(day)
        assert all(
            r.balance_cents is not None and r.balance_cents > 0
            for r in rows
            if r.account_id in checking
        ), day
        day += dt.timedelta(days=3)


def test_net_worth_series_is_rich_enough_for_the_chart(demo: Container) -> None:
    series = GetNetWorthSeries(demo.uow, demo.clock).execute()
    assert len(series.points) >= 8 and not series.partial and series.pending == []
    assert series.points[0].on == dt.date(2025, 12, 31) and series.points[-1].on == TODAY
    assert series.points[-1].cents > series.points[0].cents  # the household saves
    view = GetNetWorth(demo.uow, demo.clock).execute()
    assert not view.is_partial and view.net_worth_cents == series.points[-1].cents


def test_seeding_is_deterministic_and_only_on_an_empty_database(project: Path) -> None:
    first = demo_container(project)
    with first.uow as work:
        total = sum(
            t.amount_cents for t in work.transactions.list_between(dt.date.min, dt.date.max)
        )
    summary = first.reset_demo()
    again = Container(first.settings)
    again.__dict__["clock"] = FixedClock(TODAY)
    with again.uow as work:
        assert (
            sum(t.amount_cents for t in work.transactions.list_between(dt.date.min, dt.date.max))
            == total
        )
    assert summary.transactions > 300 and summary.anchors >= 40
    assert not demo_is_empty(again.uow)
    with pytest.raises(ValueError, match="DEMO_DB_NOT_EMPTY"):
        seed_demo(again.uow, again.clock)
    assert again.ensure_demo() is False  # idempotent: nothing is added twice


@pytest.mark.parametrize(
    "today", [dt.date(2026, 2, 10), dt.date(2026, 12, 31), dt.date(2028, 5, 3)]
)
def test_the_seed_works_on_other_dates(project: Path, today: dt.date) -> None:
    c = demo_container(project, today)
    with c.uow as work:
        entries = work.transactions.list_between(dt.date.min, dt.date.max)
    assert entries and max(t.posted_on for t in entries if t.statement_id is None) <= today
    assert len(GetNetWorthSeries(c.uow, c.clock).execute().points) >= 3
    c.close()


def test_a_date_before_2026_is_refused(project: Path) -> None:
    c = build_container(demo=True)
    c.__dict__["clock"] = FixedClock(dt.date(2025, 6, 1))
    c.migrate()
    c.seed()
    with pytest.raises(ValueError, match="DEMO_NEEDS_A_DATE"):
        seed_demo(c.uow, c.clock)


# --- the pages and charts on the demo data -----------------------------------------------------


@pytest.fixture
def client(demo: Container) -> TestClient:
    return TestClient(create_app(demo), base_url="http://localhost", headers=HEADERS)


def test_every_page_renders_on_the_demo(client: TestClient) -> None:
    for path in (
        "/",
        "/analises",
        "/entries",
        "/accounts",
        "/cards",
        "/cards/purchase",
        "/investments",
        "/networth",
        "/budget",
        "/recurring",
        "/carta",
        "/ese",
        "/categories",
        "/more",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Modo demonstração" in response.text, path


def test_demo_merchants_feed_the_ranking_and_the_suggestions(demo: Container) -> None:
    from financas.application.queries.merchants import GetTopMerchants, ListMerchants
    from financas.application.queries.summary import Period

    names = ListMerchants(demo.uow).execute()
    for expected in (
        "Amazon",
        "Uber",
        "iFood",
        "Mercado Livre",
        "Droga Raia",
        "Posto Ipiranga",
        "Restaurante Mocotó",
        "Supermercado Pão de Açúcar",
    ):
        assert expected in names, expected
    ranking = GetTopMerchants(demo.uow).execute(Period.month(YearMonth(2026, 9)))
    assert len(ranking.rows) >= 5 and ranking.unidentified is None  # a closed month is filled in
    assert ranking.rows[0].total_spent_cents >= ranking.rows[-1].total_spent_cents
    assert all(r.transaction_count >= 1 and r.average_ticket_cents > 0 for r in ranking.rows)
    # the three entries waiting in "Revisão Rápida" are the unidentified ones of the open month
    october = GetTopMerchants(demo.uow).execute(Period.month(YearMonth(2026, 10)))
    assert october.unidentified is not None and october.unidentified.transaction_count == 3


def test_merchant_analytics_render_with_the_synthetic_merchants(client: TestClient) -> None:
    page = client.get("/analises?month=2026-09")
    assert page.status_code == 200
    section = page.text.split('id="h-mer"')[1]
    for merchant in ("Imobiliária Central", "Supermercado Pão de Açúcar"):
        assert merchant in section, merchant
    assert 'class="mer-share"' in section and "% da despesa" in section
    october = client.get("/analises?month=2026-10").text.split('id="h-mer"')[1]
    assert "Outros / Sem identificação" in october  # the entries waiting for review
    entries = client.get("/entries?month=2026-09").text
    assert 'class="c-merchant"' in entries and '<datalist id="merchants-list">' in entries


def forbidden_word() -> str:
    return "tin" + "der"  # never in the product's copy (spelled apart so this file never says it)


def test_demo_has_three_expenses_waiting_in_the_review_deck(demo: Container) -> None:
    from financas.application.queries.review import CountPendingReview, GetReviewDeck

    assert CountPendingReview(demo.uow).execute() == 3
    with demo.uow as work:
        everything = work.transactions.list_between(dt.date.min, dt.date.max, include_refunded=True)
    waiting = sorted(
        t.description for t in everything if t.kind.value == "expense" and not t.merchant
    )
    assert waiting == ["COMPRA DROGASIL 0451", "PAG*MERCADOLIVRE 123", "SHOPEE *BR"]
    # what the deck suggests for each: aliases and what the demo's own entries say
    suggestions: dict[str, str | None] = {}
    seen: list[str] = []
    for _ in range(3):
        deck = GetReviewDeck(demo.uow).execute(seen)
        assert deck.card is not None
        suggestions[deck.card.entry.description] = deck.card.suggested_merchant
        seen.append(deck.card.entry.id)
    assert suggestions == {
        "PAG*MERCADOLIVRE 123": "Mercado Livre",
        "SHOPEE *BR": "Shopee",
        "COMPRA DROGASIL 0451": "Drogasil",
    }
    assert GetReviewDeck(demo.uow).execute(seen).card is None


def test_demo_suffix_entries_were_parsed_when_they_were_typed(demo: Container) -> None:
    with demo.uow as work:
        entries = {
            t.description: t for t in work.transactions.list_between(dt.date.min, dt.date.max)
        }
    assert entries["Mouse gamer"].merchant == "Kabum"
    assert entries["Capa de celular"].merchant == "Amazon"  # the alias, not "amazon.com.br"
    assert not any(" - " in description for description in entries)


def test_review_deck_renders_on_the_demo_without_the_forbidden_name(client: TestClient) -> None:
    page = client.get("/revisar")
    assert page.status_code == 200 and "Revisão Rápida" in page.text
    assert "para revisar" in page.text and "Salvar e avançar" in page.text
    assert forbidden_word() not in page.text.lower()
    assert '<span class="nav-count">(3)</span>' in client.get("/").text  # the sidebar counter


def test_chart_endpoints_answer_with_real_shapes(client: TestClient) -> None:
    nw = client.get("/api/charts/net-worth").json()
    assert len(nw["points"]) >= 8 and nw["partial"] is False and nw["first_date"] == "2025-12-31"
    pace = client.get("/api/charts/pace", params={"month": "2026-09"}).json()
    assert pace["total_cents"] > 0 and pace["average_cents"] and pace["ceiling_cents"] == 335_000
    assert pace["elapsed_days"] == 30
    flow = client.get("/api/charts/cash-flow", params={"year": "2026"}).json()
    assert len(flow["months"]) == 10 and all(m["income_cents"] > 0 for m in flow["months"][:9])
    cats = client.get("/api/charts/categories", params={"month": "2026-09"}).json()
    assert cats["total_cents"] > 0 and len(cats["slices"]) >= 5 and cats["rest"] is not None
    comp = client.get("/api/charts/net-worth-composition").json()
    assert comp["partial"] is False and comp["investments_cents"] > 0 and comp["cash_cents"] > 0


def test_painel_chart_island_gets_the_series_and_privacy_marks(client: TestClient) -> None:
    page = client.get("/").text
    island = page[page.index("<fp-patrimonio") : page.index("</fp-patrimonio>")]
    assert island.count('["20') >= 8 and 'private="networth"' in island
    assert 'badge="parcial"' not in island  # nothing is pending: the figure is complete
    assert 'data-private="cards"' in client.get("/cards").text
    assert 'data-private="investments"' in client.get("/investments").text
    assert page.count('data-private="transactions"') >= 3


def test_carta_do_mes_has_the_last_closed_month(client: TestClient) -> None:
    page = client.get("/carta").text
    assert "Carta de setembro" in page and "Salário" not in page.split("Carta de setembro")[0]
    assert 'class="num-v"' in page  # every number carries its margin note


def test_e_se_simulates_a_purchase_on_the_demo_cards(client: TestClient) -> None:
    page = client.get("/ese", params={"q": "geladeira de 4.200 em 10x no Nubank"}).text
    assert "geladeira" in page.lower() and "Nubank" in page and "R$" in page
    result = client.get("/ese/result", params={"q": "notebook 5000 em 12x no BB"})
    assert result.status_code == 200 and ("BB" in result.text or "Ourocard" in result.text)


def test_recurring_alerts_and_budget_use_the_story_of_the_demo(client: TestClient) -> None:
    recurring = client.get("/recurring").text
    assert "Netflix" in recurring and "Academia" in recurring and "Disney+" in recurring
    budget = client.get("/budget").text
    assert "Supermercado" in budget and "Alimentação" in budget


def test_the_real_database_is_untouched_by_a_whole_demo_session(
    project: Path, demo: Container
) -> None:
    before = fingerprint(project)
    client = TestClient(create_app(demo), base_url="http://localhost", headers=HEADERS)
    for path in ("/", "/cards", "/analises", "/carta", "/api/charts/net-worth"):
        assert client.get(path).status_code == 200
    posted = client.post("/backup", follow_redirects=False)
    assert posted.status_code in {200, 303}
    demo.reset_demo()
    runner.invoke(cli.app, ["seed-demo", "--force"])
    runner.invoke(cli.app, ["summary", "--demo"])
    assert fingerprint(project) == before
    assert not list((project / "backups").glob(".tmp-*"))
    assert len(list((project / "backups").glob("financas-*"))) == 1  # the one made by the fixture
    assert list((project / "demo_backups").glob("financas-*"))  # the demo's own backup


# --- git safety: the demo database is the only database that may be tracked -----------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=60
    )


needs_git = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="not a git checkout"
)


@needs_git
@pytest.mark.parametrize(
    "path",
    [
        "data/financas.db",
        "data/financas.db-wal",
        "data/financas.db-shm",
        "data/backups/financas-2026-10-04/financas.db",
        "data/import/csv_applied.jsonl",
        "data/images/logo.png",
        "data/logs/failures.jsonl",
        "data/demo.db-wal",
        "data/demo.db-shm",
        "data/demo_backups/x.db",
        "docs-dev/PROJECT_HISTORY.md",
    ],
)
def test_real_data_and_sqlite_side_files_are_git_ignored(path: str) -> None:
    assert _git("check-ignore", "-q", path).returncode == 0, path


@needs_git
def test_only_the_demo_database_can_be_tracked() -> None:
    assert _git("check-ignore", "-q", "data/demo.db").returncode == 1  # the one exception
    tracked = _git("ls-files", "data").stdout.split()
    assert set(tracked) <= {"data/demo.db"}, tracked
    sqlite_files = [
        name
        for name in _git("ls-files").stdout.splitlines()
        if name.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"))
    ]
    assert set(sqlite_files) <= {"data/demo.db"}, sqlite_files


def test_the_committed_demo_database_is_synthetic_and_healthy() -> None:
    import sqlite3

    path = ROOT / "data" / "demo.db"
    if not path.exists():
        pytest.skip("data/demo.db is generated on first use")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        nicknames = {row[0] for row in db.execute("SELECT nickname FROM accounts")}
        institutions = {row[0] for row in db.execute("SELECT name FROM institutions")}
    assert nicknames == {
        "Conta BB",
        "Nubank Conta",
        "Ourocard BB",
        "Nubank Roxinho",
        "Inter Black",
        "Renda fixa",
    }
    assert institutions == {"Banco do Brasil", "Nubank", "Banco Inter", "Tesouro Nacional"}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        # the committed file is at the newest schema and shows the newest features (CLAUDE.md 14)
        from financas.infrastructure.db.migrate import head_revision

        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (
            head_revision(f"sqlite:///{path}"),
        )
        assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)  # not WAL: it is tracked
        assert db.execute("SELECT count(*) FROM transaction_splits").fetchone()[0] >= 3
        assert db.execute("SELECT count(DISTINCT merchant) FROM transactions").fetchone()[0] >= 8
        # an itemized entry has no category of its own: its items carry them (CLAUDE.md 9.10)
        assert (
            db.execute(
                "SELECT count(*) FROM transactions t WHERE t.category_id IS NOT NULL AND EXISTS"
                " (SELECT 1 FROM transaction_splits s WHERE s.transaction_id = t.id)"
            ).fetchone()[0]
            == 0
        )
        # exactly three expenses wait in the review deck (no merchant), as in the seed
        assert (
            db.execute(
                "SELECT count(*) FROM transactions WHERE kind = 'expense' AND is_refunded = 0"
                " AND transfer_id IS NULL AND (merchant IS NULL OR merchant = '')"
            ).fetchone()[0]
            == 3
        )
        assert (
            db.execute("SELECT count(*) FROM transactions WHERE is_refunded = 1").fetchone()[0] >= 1
        )
    assert (
        not (ROOT / "data" / "demo.db-wal").exists()
        or (ROOT / "data" / "demo.db-wal").stat().st_size == 0
    )


def test_the_demo_shows_every_payment_method_and_the_filters_find_them(demo: Container) -> None:
    """A PIX split in two items, a boleto, a debit purchase and card entries: the chips of
    ``/entries`` give meaningful results in demo mode."""
    with demo.uow as work:
        everything = work.transactions.list_between(dt.date.min, dt.date.max, include_refunded=True)
        by_description = {t.description: t for t in everything}
        pix = by_description["Feira de sábado"]
        items = work.transactions.splits_for([pix.id])[pix.id]
    assert pix.payment_method is not None and pix.payment_method.value == "pix"
    assert (pix.amount_cents, pix.category_id) == (-18_000, None)
    assert sorted(i.amount_cents for i in items) == [7_000, 11_000]
    boleto = by_description["Condomínio Edifício Solar"]
    assert (boleto.amount_cents, str(boleto.payment_method)) == (-65_000, "boleto")
    debit = by_description["Padaria Pão Quente"]
    assert (debit.amount_cents, str(debit.payment_method)) == (-3_250, "debito")
    methods = {str(t.payment_method) for t in everything}
    assert {"pix", "debito", "boleto", "ted", "transferencia", "cartao_credito"} <= methods

    client = TestClient(
        create_app(demo), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )
    month = f"{TODAY:%Y-%m}"
    for method, expected in (
        ("pix", "Feira de sábado"),
        ("debito", "Padaria Pão Quente"),
        ("boleto", "Condomínio Edifício Solar"),
        ("cartao_credito", "Feira orgânica"),
    ):
        page = client.get(f"/entries?month={month}&method={method}")
        listed = page.text.split('id="lista"')[1].split("</section>")[0]
        assert page.status_code == 200 and expected in listed, method
    cards = (
        client.get(f"/entries?month={month}&method=cartao_credito")
        .text.split('id="lista"')[1]
        .split("</section>")[0]
    )
    assert "Condomínio Edifício Solar" not in cards  # a bank entry is not a card entry
