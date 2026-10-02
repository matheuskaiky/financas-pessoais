"""Facts for the monthly letter (Carta do mês), collected from the existing read queries.

Nothing new is defined here: the month's summary, the previous months, the year so far, the budget
goals, the recurring view, the cards overview, the net worth and the investments overview. The only
new reads are the entries of the month filed as ``uncategorized`` and the statement differences.
"""

import datetime as dt

from financas.application.letter.facts import (
    CategoryFacts,
    ClosedStatementFact,
    LetterFacts,
    PendingAccountFact,
    ReconciliationFact,
    StaleValuationFact,
)
from financas.application.queries.cards import ListCards
from financas.application.queries.investments import GetNetWorth, ListInvestments
from financas.application.queries.planning import GetRecurring
from financas.application.queries.summary import GetSummary, Period
from financas.application.queries.whatif import GetWhatIfFacts
from financas.domain.models import AccountKind, CategoryKind, HoldingStatus, StatementStatus
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork

UNCATEGORIZED_SLUG = "uncategorized"
PREVIOUS_MONTHS = 3


class GetLetterFacts:
    def __init__(self, uow: UnitOfWork, clock: Clock, stale_after_days: int) -> None:
        self._uow = uow
        self._clock = clock
        self._stale = stale_after_days

    def execute(
        self,
        month: YearMonth,
        last_backup: dt.date | None = None,
        backup_warn_days: int = 7,
    ) -> LetterFacts:
        today = self._clock.today()
        summaries = GetSummary(self._uow)
        previous = tuple(
            summaries.execute(Period.month(month.add_months(-n)))
            for n in range(PREVIOUS_MONTHS, 0, -1)
        )
        year = summaries.months_of_year(month.year)[: month.month]
        current = year[-1] if year else summaries.execute(Period.month(month))
        with self._uow as uow:
            categories = {
                c.id: CategoryFacts(
                    c.id,
                    c.name,
                    c.monthly_budget_cents if c.kind is CategoryKind.EXPENSE else None,
                )
                for c in uow.categories.list_all()
            }
            uncategorized = next(
                (c for c in uow.categories.list_all() if c.slug == UNCATEGORIZED_SLUG), None
            )
            entries = uow.transactions.list_for_competence(month.day(1), month.last_day())
            count = (
                sum(1 for t in entries if t.category_id == uncategorized.id) if uncategorized else 0
            )
        recurring = GetRecurring(self._uow, self._clock).execute()
        recurring_count = (
            sum(1 for item in recurring.items if item.amounts.get(month))
            if month in recurring.months
            else None
        )
        cards = ListCards(self._uow, self._clock).execute()
        closed: list[ClosedStatementFact] = []
        reconciliations: list[ReconciliationFact] = []
        for card in cards.cards:
            for view in card.statements:
                if view.status is StatementStatus.CLOSED and view.outstanding_cents > 0:
                    closed.append(
                        ClosedStatementFact(
                            card.account.nickname,
                            view.statement.month,
                            view.statement.due_date,
                            view.outstanding_cents,
                        )
                    )
                difference = view.reconciliation.difference_cents
                if difference and view.status is not StatementStatus.PAID:
                    reconciliations.append(
                        ReconciliationFact(
                            view.statement.id,
                            card.account.nickname,
                            view.statement.month,
                            difference,
                        )
                    )
        closed.sort(key=lambda c: (c.due_date, c.card_name))
        net = GetNetWorth(self._uow, self._clock).execute()
        cash = GetWhatIfFacts(self._uow, self._clock).execute().cash_cents
        pending = [
            PendingAccountFact(a.id, a.nickname, a.kind is AccountKind.INVESTMENT)
            for a in net.pending
        ]
        pending += [PendingAccountFact(h.account_id, h.name, True) for h in net.pending_holdings]
        stale: list[StaleValuationFact] = []
        overview = ListInvestments(self._uow, self._clock, self._stale).execute()
        for row in overview.accounts:
            if row.holdings:
                stale += [
                    StaleValuationFact(v.holding.account_id, v.holding.name, v.age_days or 0)
                    for v in row.holdings
                    if v.stale and v.holding.status is HoldingStatus.ACTIVE
                ]
            elif row.stale:
                stale.append(
                    StaleValuationFact(row.account.id, row.account.nickname, row.age_days or 0)
                )
        return LetterFacts(
            month=month,
            today=today,
            summary=current,
            previous=previous,
            year_to_date=tuple(year),
            categories=categories,
            recurring_count=recurring_count,
            closed_statements=tuple(closed),
            cash_cents=cash,
            future_installments_cents=cards.future_installments_cents,
            uncategorized_count=count,
            uncategorized_category_id=uncategorized.id if uncategorized else None,
            reconciliations=tuple(reconciliations),
            stale_valuations=tuple(stale),
            accounts_without_balance=tuple(pending),
            backup_age_days=None if last_backup is None else (today - last_backup).days,
            backup_warn_days=backup_warn_days,
        )
