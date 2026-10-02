"""Informed balances (anchors). For investment accounts the same record is a valuation."""

import datetime as dt
from dataclasses import dataclass

from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind, BalanceAnchor, InvestmentTracking
from financas.domain.ports import UnitOfWork
from financas.domain.rules import validate_gross_balance
from financas.domain.services.balances import AnchorPoint, balance_on
from financas.domain.services.text import clean_text


@dataclass(frozen=True)
class RecordBalanceCommand:
    account_id: str
    on_date: dt.date
    balance_cents: int  # signed: an overdrawn account is negative; net for an investment
    note: str | None = None
    gross_balance_cents: int | None = None  # investment accounts only


@dataclass(frozen=True)
class RecordBalanceResult:
    anchor: BalanceAnchor
    computed_cents: int | None  # what the system expected for that date, before this anchor
    difference_cents: int | None  # informed - computed (missing entries, or yield)


class RecordBalance:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RecordBalanceCommand) -> RecordBalanceResult:
        with self._uow as uow:
            account = found(uow.accounts.get(cmd.account_id), "account")
            if account.kind is AccountKind.CREDIT_CARD:
                raise DomainError("BALANCE_NOT_FOR_CARDS")
            if account.tracking is InvestmentTracking.HOLDINGS:
                raise DomainError("ACCOUNT_TRACKS_HOLDINGS")
            if cmd.gross_balance_cents is not None:
                if account.kind is not AccountKind.INVESTMENT:
                    raise DomainError("GROSS_ONLY_FOR_INVESTMENTS")
                validate_gross_balance(cmd.balance_cents, cmd.gross_balance_cents)
            others = [
                AnchorPoint(a.on_date, a.balance_cents)
                for a in uow.anchors.list_for_account(cmd.account_id)
                if a.on_date != cmd.on_date
            ]
            computed = balance_on(others, uow.transactions.movements(cmd.account_id), cmd.on_date)
            anchor = BalanceAnchor(
                id=new_id(),
                account_id=cmd.account_id,
                on_date=cmd.on_date,
                balance_cents=cmd.balance_cents,
                note=clean_text(cmd.note) if cmd.note else None,
                gross_balance_cents=cmd.gross_balance_cents,
            )
            uow.anchors.upsert(anchor)
            uow.commit()
        difference = None if computed is None else cmd.balance_cents - computed
        return RecordBalanceResult(anchor, computed, difference)
