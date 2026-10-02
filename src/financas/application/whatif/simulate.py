"""The deterministic purchase simulator (CLAUDE.md 9.3, 9.4, 9.5; docs/V3_PLAN.md "E se…").

Pure functions over facts that a read query collects. Definitions (each one has a test):

- Schedule: the statement of installment 1 comes from ``assign_statement`` using the card's stored
  statement dates (settings only for months with no statement yet); installment k goes to that
  statement + (k - 1) months (``build_schedule``); each line carries its closing and due dates.
- Installment amounts: from the price (first installment absorbs the remainder) or, when the user
  gave the installment value, that value n times. The total is their sum.
- Limit: committed (everything not yet paid on the card) + the whole total, against the limit
  (``limit_usage``). Without a limit the answer is unknown, never "fits".
- Free cash = cash in checking accounts - closed statements still to pay. A cash purchase fits when
  the price is at most the free cash; a card purchase leaves it unchanged.
- Implied interest: the monthly rate at which the present value of the installments equals the
  price (days / 30 to each due date). Value today at the user's assumed rate: the same present
  value at that rate; ``difference = value today - price`` (positive: cash is cheaper).
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from financas.domain.models import Account, StatementStatus
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import (
    KnownDates,
    StatementAssignment,
    assign_statement,
    statement_dates,
)
from financas.domain.services.installments import build_schedule
from financas.domain.services.present_value import (
    implied_monthly_rate,
    present_value,
)
from financas.domain.services.statements import LimitUsage, limit_usage


@dataclass(frozen=True)
class Purchase:
    description: str
    installments: int
    purchased_on: dt.date
    price_cents: int | None  # the cash price; None when only the installment value is known
    installment_cents: int | None = None

    @property
    def total_cents(self) -> int | None:
        if self.installment_cents is not None:
            return self.installment_cents * self.installments
        return self.price_cents


@dataclass(frozen=True)
class CardFacts:
    account_id: str
    name: str
    due_day: int
    closing_days_before_due: int
    limit_cents: int | None
    committed_cents: int  # everything on the card not yet paid, future installments included
    known_dates: KnownDates  # stored (closing, due) of the card's existing statements


@dataclass(frozen=True)
class WhatIfFacts:
    today: dt.date
    cash_cents: int | None  # None: no checking account has an informed balance
    closed_statements_cents: int
    cards: tuple[CardFacts, ...]
    registered_by_due_month: Mapping[YearMonth, int]  # open and future statements, by due month


@dataclass(frozen=True)
class ScheduleLine:
    number: int
    month: YearMonth  # statement (closing month)
    closing: dt.date
    due: dt.date
    amount_cents: int
    days: int  # from today to the due date, never negative


@dataclass(frozen=True)
class CardScenario:
    card: CardFacts
    assignment: StatementAssignment
    lines: tuple[ScheduleLine, ...]
    total_cents: int
    usage_before: LimitUsage
    usage_after: LimitUsage
    fits_limit: bool | None  # None: the card has no limit informed
    implied_rate: Decimal | None  # monthly; None without a cash price to compare
    value_today_cents: int  # present value of the installments at the user's rate
    difference_cents: int | None  # value today - price (positive: paying cash is cheaper)


@dataclass(frozen=True)
class CashScenario:
    price_cents: int | None
    free_before_cents: int | None
    free_after_cents: int | None
    fits: bool | None  # None: no cash price, or no balance to compare with


@dataclass(frozen=True)
class MonthDue:
    month: YearMonth
    registered_cents: int
    new_cents: int

    @property
    def total_cents(self) -> int:
        return self.registered_cents + self.new_cents


@dataclass(frozen=True)
class Simulation:
    purchase: Purchase
    monthly_rate: Decimal
    free_cash_cents: int | None
    cash: CashScenario
    cards: tuple[CardScenario, ...]


def card_facts(
    account: Account,
    committed_cents: int,
    statements: list[tuple[YearMonth, dt.date, dt.date]],
) -> CardFacts | None:
    """Facts of a card from its settings and statements; ``None`` for an unconfigured card."""
    if account.due_day is None or account.closing_days_before_due is None:
        return None
    return CardFacts(
        account.id,
        account.nickname,
        account.due_day,
        account.closing_days_before_due,
        account.credit_limit_cents,
        committed_cents,
        {month: (closing, due) for month, closing, due in statements},
    )


def card_scenario(
    purchase: Purchase, card: CardFacts, facts: WhatIfFacts, monthly_rate: Decimal
) -> CardScenario:
    assignment = assign_statement(
        purchase.purchased_on, card.due_day, card.closing_days_before_due, card.known_dates
    )
    plan = build_schedule(
        count=purchase.installments,
        first_number=1,
        first_statement=assignment.month,
        total_cents=purchase.price_cents if purchase.installment_cents is None else None,
        installment_cents=purchase.installment_cents,
    )
    lines: list[ScheduleLine] = []
    for item in plan:
        known = card.known_dates.get(item.statement_month)
        closing, due = known or statement_dates(
            item.statement_month, card.due_day, card.closing_days_before_due
        )
        days = max((due - facts.today).days, 0)
        lines.append(
            ScheduleLine(item.number, item.statement_month, closing, due, item.amount_cents, days)
        )
    total = sum(line.amount_cents for line in lines)
    payments = [(line.days, line.amount_cents) for line in lines]
    before = limit_usage(card.committed_cents, card.limit_cents)
    after = limit_usage(card.committed_cents + total, card.limit_cents)
    price = purchase.price_cents
    rate = implied_monthly_rate(price, payments) if price else None
    value_today = present_value(payments, monthly_rate)
    return CardScenario(
        card,
        assignment,
        tuple(lines),
        total,
        before,
        after,
        None if after.available_cents is None else after.available_cents >= 0,
        rate,
        value_today,
        None if not price else value_today - price,
    )


def simulate(purchase: Purchase, facts: WhatIfFacts, monthly_rate: Decimal) -> Simulation:
    """Every scenario for the purchase: cash, and the installments on each configured card."""
    free = None if facts.cash_cents is None else facts.cash_cents - facts.closed_statements_cents
    price = purchase.price_cents
    cash = CashScenario(
        price,
        free,
        None if free is None or price is None else free - price,
        None if free is None or price is None else free >= price,
    )
    cards = tuple(card_scenario(purchase, c, facts, monthly_rate) for c in facts.cards)
    return Simulation(purchase, monthly_rate, free, cash, cards)


def monthly_dues(
    facts: WhatIfFacts, scenario: CardScenario | None, min_months: int = 11, max_months: int = 24
) -> list[MonthDue]:
    """Due months from this month on: what is already registered, and what the purchase adds."""
    first = YearMonth.from_date(facts.today)
    added: dict[YearMonth, int] = {}
    if scenario is not None:
        for line in scenario.lines:
            due_month = YearMonth.from_date(line.due)
            added[due_month] = added.get(due_month, 0) + line.amount_cents
    last_used = max([*facts.registered_by_due_month, *added, first])
    span = min(max(first.months_until(last_used) + 1, min_months), max_months)
    return [
        MonthDue(
            month,
            facts.registered_by_due_month.get(month, 0),
            added.get(month, 0),
        )
        for month in (first.add_months(n) for n in range(span))
    ]


def tightest(dues: list[MonthDue]) -> MonthDue | None:
    """The month with the biggest total (the first one on a tie); ``None`` when all are empty."""
    best = max(dues, key=lambda d: d.total_cents, default=None)
    return best if best is not None and best.total_cents > 0 else None


def statement_is_open_or_future(status: StatementStatus) -> bool:
    """The statements whose totals count as "already registered" by due month."""
    return status in (StatementStatus.OPEN, StatementStatus.FUTURE)
