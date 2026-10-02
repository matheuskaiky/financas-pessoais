"""Facts for the E se… simulator, from the user's data (read only).

- ``cash_cents``: the sum of the checking accounts' balances today; ``None`` when none of them has
  an informed balance (unknown, never zero).
- ``closed_statements_cents``: unpaid part of the statements that are closed.
- ``registered_by_due_month``: unpaid part of the open and future statements, by due month.
- Each configured card: its limit, what is committed (everything not yet paid, future installments
  included) and the stored dates of its statements.
"""

from financas.application.queries.cards import card_accounts, statement_views
from financas.application.whatif.simulate import (
    CardFacts,
    WhatIfFacts,
    card_facts,
    statement_is_open_or_future,
)
from financas.domain.models import AccountKind, StatementStatus
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.services.balances import AnchorPoint, balance_on


class GetWhatIfFacts:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self) -> WhatIfFacts:
        today = self._clock.today()
        cash: int | None = None
        closed = 0
        registered: dict[YearMonth, int] = {}
        cards: list[CardFacts] = []
        with self._uow as uow:
            for account in uow.accounts.list_all():
                if account.kind is not AccountKind.CHECKING or not account.is_active:
                    continue
                anchors = [
                    AnchorPoint(a.on_date, a.balance_cents)
                    for a in uow.anchors.list_for_account(account.id)
                ]
                value = balance_on(anchors, uow.transactions.movements(account.id), today)
                if value is not None:
                    cash = (cash or 0) + value
            for account in card_accounts(uow):
                views = statement_views(uow, account, today)
                for view in views:
                    if view.status is StatementStatus.CLOSED:
                        closed += view.outstanding_cents
                    elif statement_is_open_or_future(view.status) and account.is_active:
                        month = YearMonth.from_date(view.statement.due_date)
                        registered[month] = registered.get(month, 0) + view.outstanding_cents
                if not account.is_active:
                    continue
                committed = -sum(
                    t.amount_cents for t in uow.transactions.list_by_account(account.id)
                )
                facts = card_facts(
                    account,
                    max(committed, 0),
                    [
                        (v.statement.month, v.statement.closing_date, v.statement.due_date)
                        for v in views
                    ],
                )
                if facts is not None:
                    cards.append(facts)
        return WhatIfFacts(today, cash, closed, tuple(cards), registered)
