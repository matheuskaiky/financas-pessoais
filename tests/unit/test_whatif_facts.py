"""The read query behind E se…: facts from in-memory data."""

import datetime as dt

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.whatif import GetWhatIfFacts
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.domain.models import Account
from financas.domain.money import YearMonth

D = dt.date
CLOCK = FixedClock(D(2026, 7, 1))


def test_empty_database_has_unknown_cash_and_no_cards(uow: MemoryUnitOfWork) -> None:
    facts = GetWhatIfFacts(uow, CLOCK).execute()
    assert facts.cash_cents is None and facts.cards == () and facts.closed_statements_cents == 0
    assert dict(facts.registered_by_due_month) == {}


def test_cash_is_the_checking_balance(uow: MemoryUnitOfWork, checking: Account) -> None:
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 6, 30), 500_000))
    assert GetWhatIfFacts(uow, CLOCK).execute().cash_cents == 500_000


def test_card_facts_commitment_and_registered_dues(uow: MemoryUnitOfWork, card: Account) -> None:
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            account_id=card.id,
            description="Fone",
            purchased_on=D(2026, 7, 10),
            total_cents=30_100,
            installments=3,
        )
    )
    facts = GetWhatIfFacts(uow, CLOCK).execute()
    (c,) = facts.cards
    assert c.account_id == card.id and c.limit_cents == 1_200_000
    assert c.committed_cents == 30_100
    assert YearMonth(2026, 8) in c.known_dates and len(c.known_dates) == 3
    assert sum(facts.registered_by_due_month.values()) == 30_100
    assert YearMonth(2026, 8) in facts.registered_by_due_month
