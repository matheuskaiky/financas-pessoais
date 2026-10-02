"""Contributions and withdrawals on investment accounts (CLAUDE.md 9.1, 9.6)."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from financas.application.use_cases._common import found
from financas.application.use_cases.transactions import (
    RegisterTransferCommand,
    build_transfer_legs,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    HoldingStatus,
    InvestmentTracking,
    Transaction,
)
from financas.domain.ports import UnitOfWork


class FlowDirection(StrEnum):
    CONTRIBUTION = "contribution"  # money goes into the investment account
    WITHDRAWAL = "withdrawal"  # money comes out of it


@dataclass(frozen=True)
class RegisterInvestmentFlowCommand:
    """A contribution or withdrawal: a transfer between a checking account and an investment one.

    ``other_account_id`` is the tracked checking account on the other side, or ``None`` when the
    money comes from (or goes to) an account the system does not track: one leg is created.
    """

    investment_account_id: str
    direction: FlowDirection
    posted_on: dt.date
    amount_cents: int
    other_account_id: str | None = None
    description: str = ""
    notes: str | None = None
    holding_id: str | None = None  # required on a holdings-level account, refused otherwise


class RegisterInvestmentFlow:
    """Neither income nor expense (9.1): it only moves money between places."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RegisterInvestmentFlowCommand) -> list[Transaction]:
        with self._uow as uow:
            account = found(uow.accounts.get(cmd.investment_account_id), "account")
            if account.kind is not AccountKind.INVESTMENT:
                raise DomainError("INVESTMENT_REQUIRED")
            if account.tracking is InvestmentTracking.HOLDINGS:
                if cmd.holding_id is None:
                    raise DomainError("HOLDING_REQUIRED")
                holding = found(uow.holdings.get(cmd.holding_id), "holding")
                if holding.account_id != account.id:
                    raise DomainError("HOLDING_NOT_IN_ACCOUNT")
                if holding.status is HoldingStatus.REDEEMED:
                    raise DomainError("HOLDING_REDEEMED")
            elif cmd.holding_id is not None:
                raise DomainError("ACCOUNT_NOT_HOLDINGS_LEVEL")
            if cmd.other_account_id is not None:
                other = found(uow.accounts.get(cmd.other_account_id), "account")
                if other.kind is not AccountKind.CHECKING:
                    raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=other.kind.value)
            into = cmd.direction is FlowDirection.CONTRIBUTION
            legs = build_transfer_legs(
                uow,
                RegisterTransferCommand(
                    from_account_id=cmd.other_account_id if into else cmd.investment_account_id,
                    to_account_id=cmd.investment_account_id if into else cmd.other_account_id,
                    posted_on=cmd.posted_on,
                    amount_cents=cmd.amount_cents,
                    description=cmd.description,
                    notes=cmd.notes,
                    holding_id=cmd.holding_id,
                ),
            )
            uow.transactions.add_many(legs)
            uow.commit()
        return legs
