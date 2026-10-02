"""Helpers shared by the card use cases."""

import datetime as dt

from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import Account, AccountKind, Statement
from financas.domain.money import YearMonth
from financas.domain.ports import UnitOfWork
from financas.domain.services.card_cycle import (
    StatementAssignment,
    assign_statement,
    explicit_assignment,
    statement_dates,
)


def require_card(uow: UnitOfWork, account_id: str) -> Account:
    card = found(uow.accounts.get(account_id), "account")
    if card.kind is not AccountKind.CREDIT_CARD:
        raise DomainError("CARD_REQUIRED")
    if not card.is_active:
        raise DomainError("ACCOUNT_INACTIVE")
    assert card.closing_day is not None and card.due_day is not None
    return card


def assignment_for(
    card: Account, posted_on: dt.date | None, statement_month: YearMonth | None
) -> StatementAssignment:
    """The statement of an entry: the user's choice when given, else the card cycle (9.3)."""
    assert card.closing_day is not None and card.due_day is not None
    if statement_month is not None:
        return explicit_assignment(statement_month, card.closing_day, card.due_day, posted_on)
    if posted_on is None:
        raise DomainError("PURCHASE_DATE_REQUIRED")
    return assign_statement(posted_on, card.closing_day, card.due_day)


def ensure_statement(uow: UnitOfWork, card: Account, month: YearMonth) -> Statement:
    """The card's statement for ``month``; created with the card's current dates if missing."""
    existing = uow.statements.get_by_card_month(card.id, month)
    if existing is not None:
        return existing
    assert card.closing_day is not None and card.due_day is not None
    closing, due = statement_dates(month, card.closing_day, card.due_day)
    statement = Statement(new_id(), card.id, month, closing, due)
    uow.statements.add(statement)
    return statement
