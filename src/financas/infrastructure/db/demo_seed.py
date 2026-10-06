"""Synthetic data for the demo mode (``financas demo``): a made-up Brazilian household, 2026.

Nothing here is real. The persona is a CLT software developer renting a flat, with two checking
accounts, three credit cards, one fixed-income investment account (Tesouro Selic and a CDB at 100%
of the CDI), three running installment plans and budget goals. Every row goes through the
application use cases, so every domain invariant (sign x kind, category kind x transaction kind,
statement x account, hex colors, the closing and installment rules) is enforced by the same code as
in real use. The data is deterministic (a fixed random seed) and relative to ``clock.today()``: the
timeline runs from January 2026 to today, and the installment plans are placed a few months back so
they are still running.

Descriptions and names are pt-BR *data values* (like the seeded category names); no logic here
speaks Portuguese.
"""

import datetime as dt
import random
import shutil
from dataclasses import dataclass

from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudgets
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    InformStatementTotal,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
    SetInvestmentSettings,
)
from financas.application.use_cases.holdings import (
    RecordHoldingValuation,
    RecordHoldingValuationCommand,
    RegisterHolding,
    RegisterHoldingCommand,
)
from financas.application.use_cases.investments import (
    FlowDirection,
    RegisterInvestmentFlow,
    RegisterInvestmentFlowCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.models import (
    AccountKind,
    AssetClass,
    Indexer,
    InstrumentType,
    InvestmentTracking,
    Liquidity,
    RateMode,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings

START = dt.date(2026, 1, 1)
SEED = 2026
_EXPENSE = TransactionKind.EXPENSE
_INCOME = TransactionKind.INCOME


@dataclass(frozen=True)
class DemoSummary:
    """What the demo database holds after seeding (counts only)."""

    institutions: int
    accounts: int
    transactions: int
    statements: int
    plans: int
    holdings: int
    anchors: int


def demo_is_empty(uow: UnitOfWork) -> bool:
    """True when no institution or account exists yet (the initial categories do not count)."""
    with uow as work:
        return not work.accounts.list_all() and not work.institutions.list_all()


def seed_demo(uow: UnitOfWork, clock: Clock) -> DemoSummary:
    """Fill an empty database with the demo household. Refuses a database that has accounts."""
    if not demo_is_empty(uow):
        raise ValueError("DEMO_DB_NOT_EMPTY")
    if clock.today() < START:
        raise ValueError("DEMO_NEEDS_A_DATE_IN_2026_OR_LATER")
    return _Builder(uow, clock).build()


def wipe_demo_files(settings: Settings) -> None:
    """Delete the demo database (and its WAL/journal files) and the demo images, nothing else.

    Refuses anything that is not the demo: the settings must be demo settings and the database
    must be exactly ``<data_dir>/demo.db``. The real ``financas.db``, ``images/`` and ``backups/``
    are never named here.
    """
    db = settings.db_path
    if not settings.demo or db is None or db != settings.demo_db_path or db.name != "demo.db":
        raise ValueError("NOT_THE_DEMO_DATABASE")
    for suffix in ("", "-wal", "-shm", "-journal"):
        db.with_name(db.name + suffix).unlink(missing_ok=True)
    images = settings.images_dir
    if images.name == "demo_images" and images.parent == settings.data_dir:
        shutil.rmtree(images, ignore_errors=True)


def _last_day(year: int, month: int) -> int:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - dt.timedelta(days=1)).day


class _Builder:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self.uow = uow
        self.clock = clock
        self.today = clock.today()
        self.rng = random.Random(SEED)
        seed_categories(uow)
        with uow as work:
            self.cat = {c.slug: c.id for c in work.categories.list_all()}
        self.months: list[YearMonth] = []
        ym = YearMonth.from_date(START)
        while ym <= YearMonth.from_date(self.today):
            self.months.append(ym)
            ym = ym.add_months(1)
        self.last_closed = YearMonth.from_date(self.today).add_months(-1)

    # -- helpers ------------------------------------------------------------------------------

    def at(self, ym: YearMonth, day: int) -> dt.date | None:
        """The date in ``ym`` (clamped to the month), or ``None`` when it is still in the future."""
        when = dt.date(ym.year, ym.month, min(day, _last_day(ym.year, ym.month)))
        return when if when <= self.today else None

    def money(self, low: int, high: int) -> int:
        """A random amount in cents, rounded to 10 cents."""
        return self.rng.randint(low // 10, high // 10) * 10

    def spend(
        self,
        account_id: str,
        when: dt.date | None,
        cents: int,
        description: str,
        slug: str,
        *,
        recurring: bool = False,
    ) -> None:
        if when is None:
            return
        RegisterTransaction(self.uow).execute(
            RegisterTransactionCommand(
                account_id,
                when,
                _EXPENSE,
                cents,
                description,
                self.cat[slug],
                is_recurring=recurring,
            )
        )

    def earn(self, account_id: str, when: dt.date | None, cents: int, text: str, slug: str) -> None:
        if when is None:
            return
        RegisterTransaction(self.uow).execute(
            RegisterTransactionCommand(
                account_id,
                when,
                _INCOME,
                cents,
                text,
                self.cat[slug],
                is_recurring=slug == "salary",
            )
        )

    def buy(
        self,
        card_id: str,
        when: dt.date | None,
        cents: int,
        description: str,
        slug: str,
        *,
        installments: int = 1,
        recurring: bool = False,
    ) -> None:
        if when is None:
            return
        RegisterCardPurchase(self.uow).execute(
            CardPurchaseCommand(
                card_id,
                description,
                when,
                self.cat[slug],
                installments=installments,
                total_cents=cents,
                is_recurring=recurring,
            )
        )

    # -- build --------------------------------------------------------------------------------

    def build(self) -> DemoSummary:
        self._catalog()
        for ym in self.months:
            self._month(ym)
        self._plans()
        self._statements()
        self._valuations()
        self._anchors()
        SetCategoryBudgets(self.uow).execute(
            {
                self.cat["groceries"]: 130_000,
                self.cat["food"]: 60_000,
                self.cat["transport"]: 40_000,
                self.cat["shopping"]: 50_000,
                self.cat["subscriptions"]: 15_000,
                self.cat["health"]: 40_000,
            }
        )
        return self._summary()

    def _catalog(self) -> None:
        def institution(name: str, group: str, color: str) -> str:
            return (
                CreateInstitution(self.uow)
                .execute(CreateInstitutionCommand(name=name, group_slug=group, color=color))
                .id
            )

        bb = institution("Banco do Brasil", "bb", "#F7D117")
        nu = institution("Nubank", "nubank", "#820AD1")
        inter = institution("Banco Inter", "inter", "#FF7A00")
        self.treasury = institution("Tesouro Nacional", "tesouro", "#1F6F5C")
        self.inter_id = inter

        def account(kind: AccountKind, inst: str, name: str, **extra: object) -> str:
            return (
                CreateAccount(self.uow)
                .execute(CreateAccountCommand(kind, inst, name, **extra))  # type: ignore[arg-type]
                .id
            )

        opening = dict(opening_balance_on=dt.date(2025, 12, 31))
        self.bb_checking = account(
            AccountKind.CHECKING, bb, "Conta BB", opening_balance_cents=520_000, **opening
        )
        self.nu_checking = account(
            AccountKind.CHECKING, nu, "Nubank Conta", opening_balance_cents=80_000, **opening
        )
        self.bb_card = account(
            AccountKind.CREDIT_CARD,
            bb,
            "Ourocard BB",
            closing_days_before_due=11,
            due_day=5,
            credit_limit_cents=800_000,
        )
        self.nu_card = account(
            AccountKind.CREDIT_CARD,
            nu,
            "Nubank Roxinho",
            closing_days_before_due=7,
            due_day=26,
            credit_limit_cents=380_000,
        )
        self.inter_card = account(
            AccountKind.CREDIT_CARD,
            inter,
            "Inter Black",
            closing_days_before_due=6,
            due_day=20,
            credit_limit_cents=600_000,
        )
        self.invest = account(
            AccountKind.INVESTMENT,
            inter,
            "Renda fixa",
            asset_class=AssetClass.FIXED_INCOME,
        )
        SetInvestmentSettings(self.uow).execute(
            self.invest, AssetClass.FIXED_INCOME, True, InvestmentTracking.HOLDINGS
        )
        selic = RegisterHolding(self.uow).execute(
            RegisterHoldingCommand(
                account_id=self.invest,
                name="Tesouro Selic 2029",
                instrument_type=InstrumentType.TREASURY_SELIC,
                issuer_id=self.treasury,
                applied_on=dt.date(2025, 10, 15),
                principal_cents=800_000,
                liquidity=Liquidity.DAILY,
                indexer=Indexer.SELIC,
                rate_mode=RateMode.SPREAD_OVER_INDEX,
                rate_bps=10,
                maturity_on=dt.date(2029, 3, 1),
                is_emergency_fund=True,
            )
        )
        cdb = RegisterHolding(self.uow).execute(
            RegisterHoldingCommand(
                account_id=self.invest,
                name="CDB Banco Inter 100% CDI",
                instrument_type=InstrumentType.CDB,
                issuer_id=inter,
                applied_on=dt.date(2025, 12, 10),
                principal_cents=500_000,
                liquidity=Liquidity.AT_MATURITY,
                indexer=Indexer.CDI,
                rate_mode=RateMode.PERCENT_OF_INDEX,
                rate_bps=10_000,
                maturity_on=dt.date(2027, 12, 10),
            )
        )
        self.selic, self.cdb = selic.id, cdb.id
        self.holding_value = {selic.id: 831_000, cdb.id: 504_500}  # at 2025-12-31
        for holding_id, cents in self.holding_value.items():
            RecordHoldingValuation(self.uow).execute(
                RecordHoldingValuationCommand(holding_id, dt.date(2025, 12, 31), cents)
            )

    def _month(self, ym: YearMonth) -> None:
        n = ym.month
        cancelled_gym = ym >= self.last_closed
        # --- income and fixed costs on the Banco do Brasil account
        self.earn(self.bb_checking, self.at(ym, 1), 890_000, "Salário CLT", "salary")
        if n == 3:
            self.earn(
                self.bb_checking,
                self.at(ym, 16),
                520_000,
                "Participação nos lucros",
                "other_income",
            )
        self.spend(self.bb_checking, self.at(ym, 6), 235_000, "Aluguel", "home", recurring=True)
        self.spend(self.bb_checking, self.at(ym, 6), 48_000, "Condomínio", "home", recurring=True)
        self.spend(
            self.bb_checking, self.at(ym, 9), self.money(14_000, 23_000), "Conta de luz", "home"
        )
        self.spend(
            self.bb_checking, self.at(ym, 10), 11_990, "Internet fibra", "telecom", recurring=True
        )
        self.spend(
            self.bb_checking, self.at(ym, 10), 38_990, "Plano de saúde", "insurance", recurring=True
        )
        if not cancelled_gym:
            self.spend(
                self.bb_checking, self.at(ym, 12), 11_990, "Academia", "health", recurring=True
            )
        for day in (3, 10, 17, 24):
            self.spend(
                self.bb_checking,
                self.at(ym, day),
                self.money(17_000, 39_000),
                self.rng.choice(("Supermercado Pão de Açúcar", "Mercado Extra", "Atacadão")),
                "groceries",
            )
        for day in self.rng.sample((4, 13, 21, 27), k=2):
            self.spend(
                self.bb_checking,
                self.at(ym, day),
                self.money(3_500, 16_500),
                self.rng.choice(("Farmácia Pague Menos", "Drogasil", "Droga Raia")),
                "health",
            )
        # --- own transfers and investments
        when = self.at(ym, 3)
        if when:
            RegisterTransfer(self.uow).execute(
                RegisterTransferCommand(
                    self.bb_checking,
                    self.nu_checking,
                    when,
                    125_000,
                    "Transferência para Nubank",
                )
            )
        for holding_id, cents, every in ((self.cdb, 70_000, 1), (self.selic, 40_000, 2)):
            when = self.at(ym, 2)
            if when and n % every == 0:
                RegisterInvestmentFlow(self.uow).execute(
                    RegisterInvestmentFlowCommand(
                        self.invest,
                        FlowDirection.CONTRIBUTION,
                        when,
                        cents,
                        other_account_id=self.bb_checking,
                        description="Aporte mensal",
                        holding_id=holding_id,
                    )
                )
        # --- Nubank checking (Pix)
        self.spend(
            self.nu_checking, self.at(ym, 12), self.money(3_500, 6_500), "Pix Barbearia", "services"
        )
        self.spend(
            self.nu_checking, self.at(ym, 19), self.money(4_000, 9_000), "Pix Feira", "groceries"
        )
        self.spend(
            self.nu_checking, self.at(ym, 23), self.money(2_500, 7_500), "Pix Lanche", "food"
        )
        # --- Nubank card
        for day in sorted(self.rng.sample(range(1, 28), k=5)):
            self.buy(self.nu_card, self.at(ym, day), self.money(2_800, 8_900), "iFood", "food")
        for day in sorted(self.rng.sample(range(1, 28), k=5)):
            self.buy(self.nu_card, self.at(ym, day), self.money(1_400, 3_800), "Uber", "transport")
        self.buy(
            self.nu_card,
            self.at(ym, 2),
            5_590 if ym < self.last_closed else 5_990,
            "Netflix",
            "subscriptions",
            recurring=True,
        )
        self.buy(self.nu_card, self.at(ym, 3), 2_390, "Spotify", "subscriptions", recurring=True)
        if ym == self.last_closed:
            self.buy(
                self.nu_card, self.at(ym, 4), 3_390, "Disney+", "subscriptions", recurring=True
            )
        self.buy(self.nu_card, self.at(ym, 15), self.money(6_000, 24_000), "Amazon", "shopping")
        # --- BB card
        self.buy(self.bb_card, self.at(ym, 1), 3_990, "Anuidade Ourocard", "fees", recurring=True)
        self.buy(
            self.bb_card, self.at(ym, 8), self.money(16_000, 24_000), "Posto Ipiranga", "transport"
        )
        self.buy(
            self.bb_card, self.at(ym, 18), self.money(8_000, 21_000), "Restaurante Outback", "food"
        )
        # --- Inter card
        self.buy(
            self.inter_card,
            self.at(ym, 3),
            119_000,
            "Mensalidade pós-graduação",
            "education",
            recurring=True,
        )
        self.buy(self.inter_card, self.at(ym, 14), self.money(4_500, 9_000), "Cinemark", "food")

    def _plans(self) -> None:
        """Three running installment plans (the remainder of cents lands on the first one)."""
        today = self.today

        def back(days: int) -> dt.date:
            return max(dt.date(2026, 1, 5), today - dt.timedelta(days=days))

        self.buy(
            self.nu_card, back(110), 349_907, "Smartphone Galaxy 10x", "shopping", installments=10
        )
        self.buy(self.bb_card, back(75), 329_999, "Sofá retrátil 6x", "home", installments=6)
        self.buy(self.nu_card, back(40), 156_050, "Passagem aérea 3x", "other", installments=3)

    def _statements(self) -> None:
        """Pay every statement that is due; inform the total of the first closed, unpaid one."""
        pay_from = {
            self.bb_card: self.bb_checking,
            self.inter_card: self.bb_checking,
            self.nu_card: self.nu_checking,
        }
        informed = False
        for card_id, origin in pay_from.items():
            with self.uow as work:
                statements = sorted(work.statements.list_for_card(card_id), key=lambda s: s.month)
            for statement in statements:
                if statement.due_date <= self.today:
                    self._pay(statement.id, origin, statement.due_date)
                elif statement.closing_date <= self.today and not informed:
                    # closed and unpaid; the bank's total is a bit higher (reconciliation)
                    total = self._statement_total(statement.id)
                    InformStatementTotal(self.uow).execute(statement.id, total + 2_790)
                    informed = True

    def _statement_total(self, statement_id: str) -> int:
        from financas.application.queries.cards import statement_view

        with self.uow as work:
            statement = work.statements.get(statement_id)
            assert statement is not None
            return statement_view(work, statement, self.today).total_cents

    def _pay(self, statement_id: str, origin: str, paid_on: dt.date) -> None:
        PayStatement(self.uow, self.clock).execute(
            PayStatementCommand(statement_id, origin, paid_on, description="Pagamento da fatura")
        )

    def _valuations(self) -> None:
        """A valuation at every month end (and today): yield plus the contributions of the month."""
        rates = {self.selic: 0.0104, self.cdb: 0.0105}
        value = dict(self.holding_value)
        for ym in self.months:
            end = dt.date(ym.year, ym.month, _last_day(ym.year, ym.month))
            when = min(end, self.today)
            for holding_id, rate in rates.items():
                gain = round(value[holding_id] * rate)
                contributed = 0
                if holding_id == self.cdb:
                    contributed = 70_000 if self.at(ym, 2) else 0
                elif ym.month % 2 == 0:
                    contributed = 40_000 if self.at(ym, 2) else 0
                value[holding_id] += gain + contributed
                net = value[holding_id]
                RecordHoldingValuation(self.uow).execute(
                    RecordHoldingValuationCommand(
                        holding_id, when, net, gross_balance_cents=round(net * 1.011)
                    )
                )

    def _anchors(self) -> None:
        """Informed balances of the checking accounts at every month end (and today)."""
        for account_id in (self.bb_checking, self.nu_checking):
            for ym in self.months:
                end = dt.date(ym.year, ym.month, _last_day(ym.year, ym.month))
                when = min(end, self.today)
                probe = RecordBalance(self.uow).execute(RecordBalanceCommand(account_id, when, 0))
                computed = probe.computed_cents or 0
                RecordBalance(self.uow).execute(RecordBalanceCommand(account_id, when, computed))

    def _summary(self) -> DemoSummary:
        with self.uow as work:
            return DemoSummary(
                institutions=len(work.institutions.list_all()),
                accounts=len(work.accounts.list_all()),
                transactions=len(work.transactions.list_between(dt.date.min, dt.date.max)),
                statements=len(work.statements.list_all()),
                plans=len(work.plans.list_all()),
                holdings=len(work.holdings.list_all()),
                anchors=sum(
                    len(work.anchors.list_for_account(a.id)) for a in work.accounts.list_all()
                )
                + sum(len(work.anchors.list_for_holding(h.id)) for h in work.holdings.list_all()),
            )


__all__ = ["DemoSummary", "demo_is_empty", "seed_demo", "wipe_demo_files"]
