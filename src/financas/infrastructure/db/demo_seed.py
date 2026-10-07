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
    SplitItem,
    UpdateTransaction,
    UpdateTransactionCommand,
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
    itemized: int = 0  # entries split into items (the "Desdobramento" showcase)
    pending_review: int = 0  # expenses waiting in "Revisão Rápida"
    refunded: int = 0  # purchases marked as refunded ("Compra estornada")


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
        merchant: str | None = None,
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
                merchant=merchant,
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
        merchant: str | None = None,
        splits: tuple[SplitItem, ...] = (),
    ) -> None:
        if when is None:
            return
        RegisterCardPurchase(self.uow).execute(
            CardPurchaseCommand(
                card_id,
                description,
                when,
                None if splits else self.cat[slug],  # an itemized purchase has no category
                installments=installments,
                total_cents=cents,
                is_recurring=recurring,
                merchant=merchant,
                splits=splits,
            )
        )

    # -- build --------------------------------------------------------------------------------

    def build(self) -> DemoSummary:
        self._catalog()
        for ym in self.months:
            self._month(ym)
        self._plans()
        self._showcase()
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
        fixed = (  # (day, cents, description, category, merchant)
            (6, 235_000, "Aluguel", "home", "Imobiliária Central"),
            (6, 48_000, "Condomínio", "home", "Condomínio Edifício Aurora"),
            (10, 11_990, "Internet fibra", "telecom", "Vivo Fibra"),
            (10, 38_990, "Plano de saúde", "insurance", "Unimed"),
        )
        for day, cents, text, slug, place in fixed[:2]:
            self.spend(
                self.bb_checking,
                self.at(ym, day),
                cents,
                text,
                slug,
                recurring=True,
                merchant=place,
            )
        self.spend(
            self.bb_checking,
            self.at(ym, 9),
            self.money(14_000, 23_000),
            "Conta de luz",
            "home",
            merchant="Enel",
        )
        for day, cents, text, slug, place in fixed[2:]:
            self.spend(
                self.bb_checking,
                self.at(ym, day),
                cents,
                text,
                slug,
                recurring=True,
                merchant=place,
            )
        if not cancelled_gym:
            self.spend(
                self.bb_checking,
                self.at(ym, 12),
                11_990,
                "Academia",
                "health",
                recurring=True,
                merchant="Smart Fit",
            )
        for day in (3, 10, 17, 24):
            amount = self.money(17_000, 39_000)
            place = self.rng.choice(("Supermercado Pão de Açúcar", "Mercado Extra", "Atacadão"))
            self.spend(
                self.bb_checking, self.at(ym, day), amount, place, "groceries", merchant=place
            )
        for day in self.rng.sample((4, 13, 21, 27), k=2):
            amount = self.money(3_500, 16_500)
            place = self.rng.choice(("Farmácia Pague Menos", "Drogasil", "Droga Raia"))
            self.spend(self.bb_checking, self.at(ym, day), amount, place, "health", merchant=place)
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
            self.nu_checking,
            self.at(ym, 12),
            self.money(3_500, 6_500),
            "Pix Barbearia",
            "services",
            merchant="Barbearia Estilo",
        )
        self.spend(
            self.nu_checking,
            self.at(ym, 19),
            self.money(4_000, 9_000),
            "Pix Feira",
            "groceries",
            merchant="Feira Livre",
        )
        self.spend(
            self.nu_checking,
            self.at(ym, 23),
            self.money(2_500, 7_500),
            "Pix Lanche",
            "food",
            merchant="Lanchonete do Bairro",
        )
        # --- Nubank card
        for day in sorted(self.rng.sample(range(1, 28), k=5)):
            self.buy(
                self.nu_card,
                self.at(ym, day),
                self.money(2_800, 8_900),
                "iFood",
                "food",
                merchant="iFood",
            )
        for day in sorted(self.rng.sample(range(1, 28), k=5)):
            self.buy(
                self.nu_card,
                self.at(ym, day),
                self.money(1_400, 3_800),
                "Uber",
                "transport",
                merchant="Uber",
            )
        self.buy(
            self.nu_card,
            self.at(ym, 2),
            5_590 if ym < self.last_closed else 5_990,
            "Netflix",
            "subscriptions",
            recurring=True,
            merchant="Netflix",
        )
        self.buy(
            self.nu_card,
            self.at(ym, 3),
            2_390,
            "Spotify",
            "subscriptions",
            recurring=True,
            merchant="Spotify",
        )
        if ym == self.last_closed:
            self.buy(
                self.nu_card,
                self.at(ym, 4),
                3_390,
                "Disney+",
                "subscriptions",
                recurring=True,
                merchant="Disney+",
            )
        self.buy(
            self.nu_card,
            self.at(ym, 15),
            self.money(6_000, 24_000),
            "Amazon",
            "shopping",
            merchant="Amazon",
        )
        # --- BB card
        self.buy(
            self.bb_card,
            self.at(ym, 1),
            3_990,
            "Anuidade Ourocard",
            "fees",
            recurring=True,
            merchant="Banco do Brasil",
        )
        self.buy(
            self.bb_card,
            self.at(ym, 8),
            self.money(16_000, 24_000),
            "Posto Ipiranga",
            "transport",
            merchant="Posto Ipiranga",
        )
        self.buy(
            self.bb_card,
            self.at(ym, 18),
            self.money(8_000, 21_000),
            "Restaurante Outback",
            "food",
            merchant="Outback Steakhouse",
        )
        if n == 5:  # one confirmed Shopee order: the review deck learns its usual category from it
            self.buy(self.nu_card, self.at(ym, 9), 8_990, "Shopee", "shopping", merchant="Shopee")
        if n % 3 == 0:  # a few restaurant outings and marketplace orders (no random draws)
            self.buy(
                self.bb_card,
                self.at(ym, 22),
                18_500 + 150 * n,
                "Restaurante Mocotó",
                "food",
                merchant="Restaurante Mocotó",
            )
        if n % 2 == 0:
            self.buy(
                self.inter_card,
                self.at(ym, 20),
                15_900 + 210 * n,
                "Mercado Livre",
                "shopping",
                merchant="Mercado Livre",
            )
        # --- Inter card
        self.buy(
            self.inter_card,
            self.at(ym, 3),
            119_000,
            "Mensalidade pós-graduação",
            "education",
            recurring=True,
            merchant="Faculdade Aurora",
        )
        self.buy(
            self.inter_card,
            self.at(ym, 14),
            self.money(4_500, 9_000),
            "Cinemark",
            "food",
            merchant="Cinemark",
        )

    def _plans(self) -> None:
        """Three running installment plans (the remainder of cents lands on the first one)."""
        today = self.today

        def back(days: int) -> dt.date:
            return max(dt.date(2026, 1, 5), today - dt.timedelta(days=days))

        self.buy(
            self.nu_card,
            back(110),
            349_907,
            "Smartphone Galaxy 10x",
            "shopping",
            installments=10,
            merchant="Magazine Luiza",
        )
        self.buy(
            self.bb_card,
            back(75),
            329_999,
            "Sofá retrátil 6x",
            "home",
            installments=6,
            merchant="Tok&Stok",
        )
        # an itemized plan: R$ 450,00 in 3 x R$ 150,00, the items spread over the installments
        # (117,00 + 33,00 · 117,00 + 33,00 · 116,00 + 34,00): no cent lost, no installment category
        self.buy(
            self.inter_card,
            back(20),
            45_000,
            "Monitor gamer e acessórios 3x",
            "shopping",
            installments=3,
            merchant="Kabum",
            splits=(
                SplitItem("Monitor Gamer", self.cat["shopping"], 35_000),
                SplitItem("Cabo HDMI e Suporte", self.cat["home"], 10_000),
            ),
        )
        self.buy(
            self.nu_card,
            back(40),
            156_050,
            "Passagem aérea 3x",
            "other",
            installments=3,
            merchant="LATAM",
        )

    def _showcase(self) -> None:
        """One entry of each recent capability, so the demo shows it without any typing:

        - an itemized supermarket run (R$ 380,00 in three categories),
        - two card purchases of the same day and statement, ready for "Mesclar lançamentos",
        - a card purchase marked as refunded ("Compra estornada").

        Running installment plans (anticipation, plan deletion, installment editing) and the card
        face telemetry come from the rest of the seed. Dated at or before today, never in the
        future; everything goes through the use cases like real entries.
        """
        today = self.today

        def back(days: int) -> dt.date:
            return max(START, today - dt.timedelta(days=days))

        run = RegisterTransaction(self.uow).execute(
            RegisterTransactionCommand(
                self.bb_checking,
                back(3),
                _EXPENSE,
                38_000,
                "Compra do mês no Pão de Açúcar",
                self.cat["groceries"],
                merchant="Supermercado Pão de Açúcar",
            )
        )
        UpdateTransaction(self.uow, self.clock).execute(
            UpdateTransactionCommand(
                run.id,
                run.posted_on,
                38_000,
                run.description,
                splits=(
                    SplitItem("Feira e laticínios", self.cat["groceries"], 22_000),
                    SplitItem("Higiene pessoal", self.cat["health"], 9_000),
                    SplitItem("Casa e limpeza", self.cat["home"], 7_000),
                ),
            )
        )
        # two plain purchases on one card and one day: pick both and "Mesclar em um só lançamento"
        self.buy(
            self.inter_card,
            today,
            7_840,
            "Feira orgânica",
            "groceries",
            merchant="Quitanda da Vila",
        )
        self.buy(
            self.inter_card, today, 3_260, "Produtos de limpeza", "home", merchant="Casa Limpa"
        )
        # a purchase that was given back: listed, struck through, counted nowhere
        self.buy(
            self.nu_card,
            back(4),
            29_990,
            "Tênis esportivo (devolvido)",
            "shopping",
            merchant="Centauro",
        )
        # typed with " - Estabelecimento": the suffix becomes the merchant and leaves the text
        self.buy(self.inter_card, back(6), 15_990, "Mouse gamer - Kabum", "shopping")
        self.buy(self.nu_card, back(5), 8_950, "Capa de celular - amazon.com.br", "shopping")
        # raw card-statement text and nothing else: left for "Revisão Rápida" (/revisar)
        self.buy(self.nu_card, back(1), 12_790, "PAG*MERCADOLIVRE 123", "uncategorized")
        self.buy(self.nu_card, back(2), 6_450, "SHOPEE *BR", "uncategorized")
        self.buy(self.bb_card, back(3), 9_730, "COMPRA DROGASIL 0451", "uncategorized")
        with self.uow as work:
            returned = next(
                t
                for t in work.transactions.list_by_account(self.nu_card)
                if t.description == "Tênis esportivo (devolvido)"
            )
        UpdateTransaction(self.uow, self.clock).execute(
            UpdateTransactionCommand(
                returned.id,
                returned.posted_on,
                29_990,
                returned.description,
                is_refunded=True,
                acknowledge_closed=True,  # its statement may already be closed: this is the seed
            )
        )

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
            everything = work.transactions.list_between(
                dt.date.min, dt.date.max, include_refunded=True
            )
            return DemoSummary(
                itemized=len(work.transactions.splits_for(t.id for t in everything)),
                pending_review=work.transactions.count_pending_review(self.cat["uncategorized"]),
                refunded=sum(1 for t in everything if t.is_refunded),
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
