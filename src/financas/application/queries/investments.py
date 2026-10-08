"""Investments and net worth (CLAUDE.md 9.6, 10).

Definitions (each one has a test):
- Values are **net** as the institution reports them. Taxes are never computed.
- Current value (estimated) = last valuation + net flows after it, per account.
- Net contributions = contributions - withdrawals (transfers into and out of the account).
- Yield (period) = end value - start value - net contributions; simple return = yield /
  (start value + net contributions). Capitalized yield is **not** income (open decision 2).
- Distributions paid into a checking account are income in ``investment_income``: shown apart.
- An account with no valuation is left out of the totals and listed as pending ("parcial").
- Net worth = cash + investments - outstanding statements (closed and open); future installments
  are shown apart as commitments.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from financas.application.queries.cards import card_accounts, statement_views
from financas.application.queries.summary import GetSummary, Period
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    BalanceAnchor,
    CategoryGroup,
    HoldingStatus,
    Institution,
    InvestmentHolding,
    InvestmentTracking,
    StatementStatus,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.services.balances import AnchorPoint, balance_on
from financas.domain.services.holdings import (
    BucketRow,
    FgcRow,
    LadderRow,
    LiquidityBucket,
    available_from,
    bucket_for,
    emergency_coverage_months,
    fgc_exposure,
    liquidity_buckets,
    maturity_ladder,
)
from financas.domain.services.investments import (
    Allocation,
    CapitalPosition,
    HoldingCapital,
    PeriodYield,
    SnapshotPoint,
    allocate,
    capital_position,
    current_value,
    is_stale,
    period_yield,
    profit_timeline,
    valuation_age_days,
)


@dataclass(frozen=True)
class InvestmentAccountView:
    account: Account
    current_value_cents: int | None  # None: no valuation yet (pending)
    last_valuation: BalanceAnchor | None
    age_days: int | None
    stale: bool
    net_contributions_cents: int  # since the first valuation
    yield_cents: int | None
    simple_return: float | None
    share: float | None  # of the total invested
    holdings: tuple["HoldingView", ...] = ()  # holdings-level accounts only
    free_cash_cents: int = 0  # holdings-level accounts: arrived, not in any note


@dataclass(frozen=True)
class InvestmentsOverview:
    accounts: list[InvestmentAccountView]
    total_cents: int
    allocation: Allocation
    pending: list[Account]  # accounts without a valuation, left out of the totals
    pending_holdings: list[InvestmentHolding]  # active holdings without a valuation


@dataclass(frozen=True)
class HoldingView:
    holding: InvestmentHolding
    issuer: Institution
    current_value_cents: int | None  # None: no valuation yet (pending); 0 once redeemed
    last_valuation: BalanceAnchor | None
    age_days: int | None
    stale: bool
    net_contributions_cents: int
    yield_cents: int | None
    simple_return: float | None
    available_from: dt.date | None
    bucket: LiquidityBucket
    return_base_cents: int | None = None  # start value + net contributions (simple return base)


def holding_views(
    uow: Work,
    today: dt.date,
    stale_after_days: int,
    account_id: str | None = None,
    include_redeemed: bool = False,
) -> list[HoldingView]:
    institutions = {i.id: i for i in uow.institutions.list_all()}
    rows: list[HoldingView] = []
    for holding in uow.holdings.list_all():
        if account_id and holding.account_id != account_id:
            continue
        if holding.status is HoldingStatus.REDEEMED and not include_redeemed:
            continue
        anchors = uow.anchors.list_for_holding(holding.id)
        flows = uow.transactions.movements_for_holding(holding.id)
        points = _points(anchors)
        valid = [a for a in anchors if a.on_date <= today]
        last = max(valid, key=lambda a: a.on_date) if valid else None
        age = valuation_age_days(last.on_date, today) if last else None
        span = period_yield(points, flows, dt.date.min, today)
        available = available_from(holding.liquidity, holding.liquid_from, holding.maturity_on)
        rows.append(
            HoldingView(
                holding,
                institutions[holding.issuer_id],
                current_value(points, flows, today),
                last,
                age,
                age is not None
                and holding.status is HoldingStatus.ACTIVE
                and is_stale(age, stale_after_days),
                span.net_contributions_cents if span else 0,
                span.yield_cents if span else None,
                span.simple_return if span else None,
                available,
                bucket_for(available, today),
                span.start_value_cents + span.net_contributions_cents if span else None,
            )
        )
    return sorted(rows, key=lambda v: (v.holding.status.value, v.holding.name))


class ListHoldings:
    def __init__(self, uow: UnitOfWork, clock: Clock, stale_after_days: int) -> None:
        self._uow = uow
        self._clock = clock
        self._stale = stale_after_days

    def execute(
        self, account_id: str | None = None, include_redeemed: bool = False
    ) -> list[HoldingView]:
        with self._uow as uow:
            return holding_views(
                uow, self._clock.today(), self._stale, account_id, include_redeemed
            )


def account_free_cash(uow: Work, account_id: str, day: dt.date) -> int:
    """Money that reached an account tracked by notes and no note holds (never negative here).

    net inflow up to ``day`` minus the cost basis of every note (redeemed ones included); a
    negative result means notes with no recorded funding, which is reported by the capital view
    and never subtracted from the totals.
    """
    net = sum(c for d, c in uow.transactions.movements(account_id) if d <= day)
    committed = 0
    for h in uow.holdings.list_for_account(account_id):
        if h.applied_on > day:
            continue
        committed += h.principal_cents + sum(
            c for d, c in uow.transactions.movements_for_holding(h.id) if h.applied_on < d <= day
        )
    return max(0, net - committed)


def _holdings_account_view(
    account: Account, views: list[HoldingView], free_cash: int = 0
) -> InvestmentAccountView:
    """A holdings-level account is the sum of its holdings plus its free cash (9.6)."""
    active = [v for v in views if v.holding.status is HoldingStatus.ACTIVE]
    valued = [v for v in active if v.current_value_cents is not None]
    if valued:
        value: int | None = sum(v.current_value_cents or 0 for v in valued)
    elif views and not active:
        value = 0  # everything was redeemed
    else:
        value = None
    if value is not None:
        value += free_cash
    elif not views and free_cash > 0:
        value = free_cash  # money that arrived and has no note yet
    spans = [v for v in views if v.yield_cents is not None]
    yield_total = sum(v.yield_cents or 0 for v in spans) if spans else None
    # simple return: only holdings with a positive base (a redeemed one has none left)
    based = [v for v in spans if (v.return_base_cents or 0) > 0]
    base = sum(v.return_base_cents or 0 for v in based)
    based_yield = sum(v.yield_cents or 0 for v in based)
    ages = [v.age_days for v in valued if v.age_days is not None]
    last = max(
        (v.last_valuation for v in valued if v.last_valuation),
        key=lambda a: a.on_date,
        default=None,
    )
    return InvestmentAccountView(
        account,
        value,
        last,
        max(ages) if ages else None,
        any(v.stale for v in valued),
        sum(v.net_contributions_cents for v in spans),
        yield_total,
        based_yield / base if base > 0 else None,
        None,
        tuple(views),
        free_cash,
    )


def investment_accounts(uow: Work) -> list[Account]:
    return [a for a in uow.accounts.list_all() if a.kind is AccountKind.INVESTMENT]


def _points(anchors: list[BalanceAnchor]) -> list[AnchorPoint]:
    return [AnchorPoint(a.on_date, a.balance_cents) for a in anchors]


class ListInvestments:
    def __init__(self, uow: UnitOfWork, clock: Clock, stale_after_days: int) -> None:
        self._uow = uow
        self._clock = clock
        self._stale = stale_after_days

    def execute(self) -> InvestmentsOverview:
        today = self._clock.today()
        rows: list[InvestmentAccountView] = []
        with self._uow as uow:
            holding_rows = holding_views(uow, today, self._stale, include_redeemed=True)
            for account in investment_accounts(uow):
                if account.tracking is InvestmentTracking.HOLDINGS:
                    mine = [v for v in holding_rows if v.holding.account_id == account.id]
                    rows.append(
                        _holdings_account_view(
                            account, mine, account_free_cash(uow, account.id, today)
                        )
                    )
                    continue
                anchors = uow.anchors.list_for_account(account.id)
                flows = uow.transactions.movements(account.id)
                value = current_value(_points(anchors), flows, today)
                valid = [a for a in anchors if a.on_date <= today]
                last = max(valid, key=lambda a: a.on_date) if valid else None
                age = valuation_age_days(last.on_date, today) if last else None
                span = period_yield(_points(anchors), flows, dt.date.min, today)
                rows.append(
                    InvestmentAccountView(
                        account,
                        value,
                        last,
                        age,
                        age is not None and is_stale(age, self._stale),
                        span.net_contributions_cents if span else 0,
                        span.yield_cents if span else None,
                        span.simple_return if span else None,
                        None,
                    )
                )
        items: list[tuple[AssetClass, int | None]] = []
        for r in rows:
            account_class = r.account.asset_class or AssetClass.OTHER
            if r.account.tracking is InvestmentTracking.HOLDINGS and r.holdings:
                # each holding counts in its own class (a holdings-level account mixes them)
                items.extend(
                    (v.holding.asset_class, v.current_value_cents)
                    for v in r.holdings
                    if v.holding.status is HoldingStatus.ACTIVE
                )
                if r.free_cash_cents:
                    items.append((account_class, r.free_cash_cents))
            else:
                items.append((account_class, r.current_value_cents))
        allocation = allocate(items)
        total = allocation.total_cents
        rows = [
            replace(
                r,
                share=r.current_value_cents / total
                if total and r.current_value_cents is not None
                else None,
            )
            for r in rows
        ]
        pending = [r.account for r in rows if r.current_value_cents is None]
        pending_holdings = [
            v.holding
            for r in rows
            for v in r.holdings
            if v.holding.status is HoldingStatus.ACTIVE and v.current_value_cents is None
        ]
        return InvestmentsOverview(rows, total, allocation, pending, pending_holdings)


@dataclass(frozen=True)
class InvestmentPeriodTotals:
    period: Period
    net_contributions_cents: int  # all investment accounts, in the period
    capitalized_yield_cents: int | None  # None: no account has two valuations to compare
    simple_return: float | None
    distributions_cents: int  # paid into checking as ``investment_income``: this IS income
    compared_accounts: int
    pending_accounts: list[Account]  # no pair of valuations: not in the yield


class GetInvestmentPeriodTotals:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, period: Period) -> InvestmentPeriodTotals:
        with self._uow as uow:
            accounts = investment_accounts(uow)
            net = 0
            yields: list[PeriodYield] = []
            pending: list[Account] = []
            for account in accounts:
                flows = uow.transactions.movements(account.id)
                net += sum(c for d, c in flows if period.start <= d <= period.end)
                if account.tracking is InvestmentTracking.HOLDINGS:
                    spans = [
                        period_yield(
                            _points(uow.anchors.list_for_holding(h.id)),
                            uow.transactions.movements_for_holding(h.id),
                            period.start,
                            period.end,
                        )
                        for h in uow.holdings.list_for_account(account.id)
                    ]
                    found_spans = [x for x in spans if x is not None]
                    yields.extend(found_spans)
                    if not found_spans:
                        pending.append(account)
                    continue
                span = period_yield(
                    _points(uow.anchors.list_for_account(account.id)),
                    flows,
                    period.start,
                    period.end,
                )
                if span is None:
                    pending.append(account)
                else:
                    yields.append(span)
            income = uow.categories.get_by_slug("investment_income")
            distributions = 0
            if income is not None:
                distributions = sum(
                    t.amount_cents
                    for t in uow.transactions.list_for_competence(period.start, period.end)
                    if t.category_id == income.id and t.kind is TransactionKind.INCOME
                )
        total_yield = sum(y.yield_cents for y in yields) if yields else None
        base = sum(y.start_value_cents + y.net_contributions_cents for y in yields)
        return InvestmentPeriodTotals(
            period,
            net,
            total_yield,
            total_yield / base if total_yield is not None and base > 0 else None,
            distributions,
            len(yields),
            pending,
        )


@dataclass(frozen=True)
class YearEndRow:
    account: Account
    value_cents: int | None  # position on 31 Dec; None: no valuation up to that date
    yield_cents: int | None  # the year's yield
    holding: InvestmentHolding | None = None  # holdings-level accounts: one row per holding


@dataclass(frozen=True)
class YearEndPosition:
    year: int
    date: dt.date
    rows: list[YearEndRow]
    total_cents: int
    total_yield_cents: int | None
    pending: list[Account]
    pending_holdings: list[InvestmentHolding]


class GetYearEndPosition:
    """Position on 31 Dec per account, to help with the annual tax return. No tax computed."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, year: int) -> YearEndPosition:
        end = dt.date(year, 12, 31)
        rows: list[YearEndRow] = []
        with self._uow as uow:
            for account in investment_accounts(uow):
                if account.tracking is InvestmentTracking.HOLDINGS:
                    for holding in uow.holdings.list_for_account(account.id):
                        points = _points(uow.anchors.list_for_holding(holding.id))
                        flows = uow.transactions.movements_for_holding(holding.id)
                        span = period_yield(points, flows, dt.date(year, 1, 1), end)
                        rows.append(
                            YearEndRow(
                                account,
                                current_value(points, flows, end),
                                span.yield_cents if span else None,
                                holding,
                            )
                        )
                    continue
                points = _points(uow.anchors.list_for_account(account.id))
                flows = uow.transactions.movements(account.id)
                span = period_yield(points, flows, dt.date(year, 1, 1), end)
                rows.append(
                    YearEndRow(
                        account,
                        current_value(points, flows, end),
                        span.yield_cents if span else None,
                    )
                )
        yields = [r.yield_cents for r in rows if r.yield_cents is not None]
        return YearEndPosition(
            year,
            end,
            rows,
            sum(r.value_cents for r in rows if r.value_cents is not None),
            sum(yields) if yields else None,
            [r.account for r in rows if r.value_cents is None and r.holding is None],
            [r.holding for r in rows if r.value_cents is None and r.holding is not None],
        )


@dataclass(frozen=True)
class NetWorthView:
    cash_cents: int
    investments_cents: int
    closed_statements_cents: int  # unpaid, past closing
    open_statements_cents: int  # unpaid, still receiving purchases
    net_worth_cents: int
    future_installments_cents: int  # shown apart, as commitments
    pending: list[Account]  # accounts without balance or valuation: the total is partial
    pending_holdings: list[InvestmentHolding] = field(default_factory=list[InvestmentHolding])

    @property
    def is_partial(self) -> bool:
        return bool(self.pending or self.pending_holdings)


def net_worth_at(uow: Work, day: dt.date, *, historical: bool = False) -> NetWorthView:
    """Net worth on ``day`` (CLAUDE.md 10): cash + investments - outstanding statements.

    With ``historical=False`` it is the current definition, exactly (``day`` is today). With
    ``historical=True`` it looks back at a past date: only what already existed on ``day`` counts
    (entries posted up to it; installments of a plan bought by then; redeemed holdings that still
    had value; holdings applied by then), and an account whose first valuation comes later is
    simply not there yet (it is pending only when it has no valuation at all).
    """
    cash = investments = 0
    pending: list[Account] = []
    pending_holdings: list[InvestmentHolding] = []
    closed = opened = future = 0
    for account in uow.accounts.list_all():
        if account.kind is AccountKind.CREDIT_CARD:
            continue
        # a deactivated account still holds money: it counts when it has a balance, and
        # is only listed as pending while it is active
        if account.tracking is InvestmentTracking.HOLDINGS:
            views = [
                v
                for v in holding_views(uow, day, 0, include_redeemed=historical)
                if v.holding.account_id == account.id
                and not (historical and v.holding.applied_on > day)
            ]
            investments += sum(v.current_value_cents or 0 for v in views)
            investments += account_free_cash(uow, account.id, day)
            for v in views:
                if v.current_value_cents is None and not (
                    historical and uow.anchors.list_for_holding(v.holding.id)
                ):
                    pending_holdings.append(v.holding)
            continue
        anchors_all = uow.anchors.list_for_account(account.id)
        anchors = _points(anchors_all)
        flows = uow.transactions.movements(account.id)
        if account.kind is AccountKind.CHECKING:
            value = balance_on(anchors, flows, day)
        else:
            value = current_value(anchors, flows, day)
        if value is None:
            if account.is_active and not (historical and anchors_all):
                pending.append(account)
        elif account.kind is AccountKind.CHECKING:
            cash += value
        else:
            investments += value
    counts = _existed_on(uow, day) if historical else None
    for card in card_accounts(uow):
        for view in statement_views(uow, card, day, counts):
            if view.status is StatementStatus.CLOSED:
                closed += view.outstanding_cents
            elif view.status is StatementStatus.OPEN:
                opened += view.outstanding_cents
            elif view.status is StatementStatus.FUTURE:
                future += view.outstanding_cents
    return NetWorthView(
        cash,
        investments,
        closed,
        opened,
        cash + investments - closed - opened,
        future,
        pending,
        pending_holdings,
    )


def _existed_on(uow: Work, day: dt.date) -> Callable[[Transaction], bool]:
    """Which card entries existed on a past ``day``: posted by then, or an installment of a plan
    already bought (its later installments are committed even though they post at closing)."""
    plans = {p.id: p for p in uow.plans.list_all()}

    def counts(entry: Transaction) -> bool:
        if entry.posted_on <= day:
            return True
        plan = plans.get(entry.plan_id) if entry.plan_id else None
        return plan is not None and (plan.purchased_on is None or plan.purchased_on <= day)

    return counts


class GetNetWorth:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, on: dt.date | None = None) -> NetWorthView:
        """Net worth today, or looking back at ``on`` (a past date)."""
        today = self._clock.today()
        day = on if on is not None and on < today else today
        with self._uow as uow:
            return net_worth_at(uow, day, historical=day < today)


@dataclass(frozen=True)
class EmergencyFund:
    value_cents: int
    average_essential_cents: int  # average monthly essential spending, last 3 closed months
    months: float | None  # None without essential spending to divide by
    items: list[str]  # names of the marked accounts and holdings


@dataclass(frozen=True)
class FixedIncomeOverview:
    """Brazilian views of the holdings (9.6). Account-level accounts have no contract data, so
    they are not on the ladder, in the liquidity ranges or in the FGC exposure."""

    holdings: list[HoldingView]  # active and valued
    pending: list[InvestmentHolding]  # active without a valuation: left out
    ladder: list[LadderRow]
    liquidity: list[BucketRow]
    fgc: list[FgcRow]
    fgc_limit_cents: int
    emergency: EmergencyFund


class GetFixedIncomeOverview:
    def __init__(
        self, uow: UnitOfWork, clock: Clock, stale_after_days: int, fgc_limit_cents: int
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._stale = stale_after_days
        self._fgc_limit = fgc_limit_cents

    def execute(self) -> FixedIncomeOverview:
        today = self._clock.today()
        with self._uow as uow:
            views = holding_views(uow, today, self._stale)
            institutions = {i.id: i for i in uow.institutions.list_all()}
            valued = [v for v in views if v.current_value_cents is not None]
            pending = [v.holding for v in views if v.current_value_cents is None]
            ladder = maturity_ladder(
                [(v.current_value_cents or 0, v.holding.maturity_on) for v in valued]
            )
            buckets = liquidity_buckets(
                [(v.current_value_cents or 0, v.available_from) for v in valued], today
            )

            def group(inst: Institution) -> str:
                return inst.group_slug or inst.slug

            covered = [
                (group(v.issuer), v.current_value_cents or 0)
                for v in valued
                if v.holding.fgc_covered
            ]
            checking: list[tuple[str, int]] = []
            emergency_value = 0
            marked: list[str] = []
            for account in uow.accounts.list_all():
                points = _points(uow.anchors.list_for_account(account.id))
                flows = uow.transactions.movements(account.id)
                if account.kind is AccountKind.CHECKING:
                    balance = balance_on(points, flows, today)
                    if balance is not None:
                        checking.append((group(institutions[account.institution_id]), balance))
                elif (
                    account.kind is AccountKind.INVESTMENT
                    and account.tracking is InvestmentTracking.ACCOUNT
                    and account.is_emergency_fund
                ):
                    amount = current_value(points, flows, today)
                    if amount is not None:
                        emergency_value += amount
                        marked.append(account.nickname)
            for v in valued:
                if v.holding.is_emergency_fund:
                    emergency_value += v.current_value_cents or 0
                    marked.append(v.holding.name)
        average = self._average_essential(today)
        return FixedIncomeOverview(
            valued,
            pending,
            ladder,
            buckets,
            fgc_exposure(covered, checking, self._fgc_limit),
            self._fgc_limit,
            EmergencyFund(
                emergency_value,
                average,
                emergency_coverage_months(emergency_value, average),
                marked,
            ),
        )

    def _average_essential(self, today: dt.date) -> int:
        """Average monthly ``essential`` spending over the last 3 closed months.

        Months before the first essential spending are not counted, so a new user's coverage is
        not overstated by empty months.
        """
        queries = GetSummary(self._uow)
        current = YearMonth.from_date(today)
        spent: list[int] = []
        for back in (3, 2, 1):  # oldest first
            summary = queries.execute(Period.month(current.add_months(-back)))
            spent.append(
                sum(g.total_cents for g in summary.by_group if g.group is CategoryGroup.ESSENTIAL)
            )
        first = next((i for i, v in enumerate(spent) if v > 0), None)
        if first is None:
            return 0
        window = spent[first:]
        return sum(window) // len(window)


# --- profit over time (snapshots) --------------------------------------------------------------


@dataclass(frozen=True)
class TimelineRow:
    """One snapshot of a series: the ``SnapshotPoint`` figures and the anchor to edit or delete."""

    anchor_id: str
    point: SnapshotPoint
    note: str | None


@dataclass(frozen=True)
class ProfitSeries:
    """The snapshots of one account (account-level tracking) or one holding, with the profit."""

    label: str
    account: Account
    holding: InvestmentHolding | None
    rows: tuple[TimelineRow, ...]  # oldest first
    current_value_cents: int | None  # last snapshot + net flows after it; none without a snapshot
    cost_cents: int | None  # net contributions (or the cost basis of a holding) up to today
    profit_cents: int | None
    return_rate: float | None


@dataclass(frozen=True)
class InvestmentProfit:
    series: tuple[ProfitSeries, ...]
    value_cents: int  # of the series that have a snapshot
    cost_cents: int
    profit_cents: int
    return_rate: float | None
    pending: int  # series with no snapshot yet, left out of the totals


def snapshot_value(anchor: BalanceAnchor) -> int:
    """The figure a profit is measured on: the gross position when informed, else the net one."""
    return (
        anchor.gross_balance_cents
        if anchor.gross_balance_cents is not None
        else (anchor.balance_cents)
    )


def _series(
    label: str,
    account: Account,
    holding: InvestmentHolding | None,
    anchors: list[BalanceAnchor],
    flows: list[tuple[dt.date, int]],
    today: dt.date,
) -> ProfitSeries:
    ordered = sorted(anchors, key=lambda a: a.on_date)
    points = profit_timeline(
        [AnchorPoint(a.on_date, snapshot_value(a)) for a in ordered],
        flows,
        base_cents=holding.principal_cents if holding else 0,
        base_date=holding.applied_on if holding else None,
    )
    rows = tuple(TimelineRow(a.id, p, a.note) for a, p in zip(ordered, points, strict=True))
    known = [p for p in points if p.on_date <= today]
    if not known:
        return ProfitSeries(label, account, holding, rows, None, None, None, None)
    last = known[-1]
    after = sum(
        c
        for d, c in flows
        if last.on_date < d <= today and (holding is None or d > holding.applied_on)
    )
    value = last.balance_cents + after
    cost = last.net_contributions_cents + after
    profit = value - cost
    return ProfitSeries(
        label, account, holding, rows, value, cost, profit, profit / cost if cost > 0 else None
    )


class GetInvestmentProfit:
    """Profit since the beginning for every investment account and holding that has snapshots.

    ``profit = value - net contributions`` (CLAUDE.md 9.6); contributions are the flows registered
    here, so a position that existed before the first entry shows as profit until its contribution
    is registered.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self) -> InvestmentProfit:
        today = self._clock.today()
        out: list[ProfitSeries] = []
        with self._uow as uow:
            for account in investment_accounts(uow):
                if account.tracking is InvestmentTracking.HOLDINGS:
                    for holding in uow.holdings.list_for_account(account.id):
                        out.append(
                            _series(
                                holding.name,
                                account,
                                holding,
                                uow.anchors.list_for_holding(holding.id),
                                uow.transactions.movements_for_holding(holding.id),
                                today,
                            )
                        )
                else:
                    out.append(
                        _series(
                            account.nickname,
                            account,
                            None,
                            uow.anchors.list_for_account(account.id),
                            uow.transactions.movements(account.id),
                            today,
                        )
                    )
        measured = [s for s in out if s.current_value_cents is not None]
        value = sum(s.current_value_cents or 0 for s in measured)
        cost = sum(s.cost_cents or 0 for s in measured)
        profit = value - cost
        return InvestmentProfit(
            tuple(out),
            value,
            cost,
            profit,
            profit / cost if cost > 0 else None,
            len(out) - len(measured),
        )


# --- capital: what arrived, what sits free, what is applied -------------------------------------


@dataclass(frozen=True)
class CapitalMovement:
    """A transfer leg into (contribution) or out of (redemption) an investment account."""

    leg_id: str
    posted_on: dt.date
    account_id: str
    other_account_id: str | None  # ``None``: the other side is not tracked here
    amount_cents: int  # signed on the investment account: > 0 contribution, < 0 redemption
    holding_id: str | None
    description: str


@dataclass(frozen=True)
class AccountCapital:
    account: Account
    position: CapitalPosition | None  # accounts tracked by notes; ``None`` for a whole-account one
    movements: tuple[CapitalMovement, ...]  # newest first
    net_inflow_cents: int
    active_holdings: int
    valuations: int  # whole-account valuations stored (history when tracked by notes)


class GetCapitalOverview:
    """Per investment account: the capital that arrived and, for notes, where it sits."""

    def __init__(self, uow: UnitOfWork, clock: Clock, stale_after_days: int) -> None:
        self._uow = uow
        self._clock = clock
        self._stale = stale_after_days

    def execute(self) -> list[AccountCapital]:
        today = self._clock.today()
        out: list[AccountCapital] = []
        with self._uow as uow:
            for account in investment_accounts(uow):
                legs = [
                    t
                    for t in uow.transactions.list_by_account(account.id)
                    if t.kind is TransactionKind.TRANSFER
                ]
                movements = tuple(
                    sorted(
                        (self._movement(uow, t) for t in legs),
                        key=lambda m: (m.posted_on, m.leg_id),
                        reverse=True,
                    )
                )
                net = sum(m.amount_cents for m in movements)
                position = None
                active = 0
                if account.tracking is InvestmentTracking.HOLDINGS:
                    views = holding_views(
                        uow, today, self._stale, account_id=account.id, include_redeemed=True
                    )
                    capitals: list[HoldingCapital] = []
                    for v in views:
                        h = v.holding
                        later = sum(
                            c
                            for d, c in uow.transactions.movements_for_holding(h.id)
                            if d > h.applied_on
                        )
                        is_active = h.status is HoldingStatus.ACTIVE
                        active += is_active
                        capitals.append(
                            HoldingCapital(
                                h.principal_cents + later,
                                v.current_value_cents if is_active else None,
                                is_active,
                            )
                        )
                    position = capital_position([m.amount_cents for m in movements], capitals)
                out.append(
                    AccountCapital(
                        account,
                        position,
                        movements,
                        net,
                        active,
                        len(uow.anchors.list_for_account(account.id)),
                    )
                )
        return out

    @staticmethod
    def _movement(uow: Work, leg: Transaction) -> CapitalMovement:
        other = next(
            (
                x.account_id
                for x in (
                    uow.transactions.list_by_transfer(leg.transfer_id) if leg.transfer_id else []
                )
                if x.id != leg.id
            ),
            None,
        )
        return CapitalMovement(
            leg.id,
            leg.posted_on,
            leg.account_id,
            other,
            leg.amount_cents,
            leg.holding_id,
            leg.description,
        )
