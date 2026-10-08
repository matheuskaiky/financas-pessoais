"""Editing an internal transfer: both legs move together (CLAUDE.md 9.1).

Creating and deleting live in ``transactions.py`` (``RegisterTransfer``, ``DeleteTransaction``):
both are atomic over every leg of the ``transfer_id``. Editing the amount, date, accounts or text
of one leg synchronizes the other in the same unit of work.
"""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.queries.transfer_links import TRANSFER_ACCOUNT_KINDS
from financas.application.use_cases._common import UNSET, Unset, found
from financas.domain.errors import DomainError
from financas.domain.models import (
    HoldingStatus,
    InvestmentTracking,
    Transaction,
    TransactionKind,
)
from financas.domain.ports import UnitOfWork
from financas.domain.rules import validate_transfer_accounts
from financas.domain.services.text import clean_text, normalize_search


@dataclass(frozen=True)
class UpdateTransferCommand:
    """The full desired transfer, addressed by any of its legs."""

    transaction_id: str
    from_account_id: str | None
    to_account_id: str | None
    posted_on: dt.date
    amount_cents: int  # the magnitude, positive
    description: str = ""
    notes: str | None = None
    # the note an investment leg moves money into or out of: ``UNSET`` keeps it, ``None`` clears it
    holding_id: Unset | str | None = UNSET


class UpdateTransfer:
    """Both legs take the new date, amount, accounts, description and notes, atomically.

    Which sides are tracked cannot change (a one-leg transfer stays one-leg): that would create or
    remove an entry, which is deleting and entering again. Statement payments and holding movements
    have their own edit and are refused here (``TRANSFER_NOT_EDITABLE``).
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: UpdateTransferCommand) -> list[Transaction]:
        if cmd.amount_cents <= 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        validate_transfer_accounts(cmd.from_account_id, cmd.to_account_id)
        description = clean_text(cmd.description)
        with self._uow as uow:
            entry = found(uow.transactions.get(cmd.transaction_id), "transaction")
            if entry.kind is not TransactionKind.TRANSFER or entry.transfer_id is None:
                raise DomainError("TRANSFER_NOT_EDITABLE")
            legs = uow.transactions.list_by_transfer(entry.transfer_id)
            outgoing = next((x for x in legs if x.amount_cents < 0), None)
            incoming = next((x for x in legs if x.amount_cents > 0), None)
            if any(leg.statement_id is not None for leg in legs):
                raise DomainError("TRANSFER_NOT_EDITABLE")  # a statement payment has its own edit
            if (outgoing is None) != (cmd.from_account_id is None) or (incoming is None) != (
                cmd.to_account_id is None
            ):
                raise DomainError("TRANSFER_SIDES_CHANGED")
            current = next((x.holding_id for x in legs if x.holding_id), None)
            holding_id = current if isinstance(cmd.holding_id, Unset) else cmd.holding_id
            holding_account_id = None
            if holding_id is not None:
                held = found(uow.holdings.get(holding_id), "holding")
                holding_account_id = held.account_id
                if holding_account_id not in (cmd.from_account_id, cmd.to_account_id):
                    raise DomainError("HOLDING_NOT_IN_ACCOUNT")
                if held.status is HoldingStatus.REDEEMED and holding_id != current:
                    raise DomainError("HOLDING_REDEEMED")
            for side in (cmd.from_account_id, cmd.to_account_id):
                if side is None:
                    continue
                tracked = found(uow.accounts.get(side), "account")
                if holding_account_id == tracked.id and (
                    tracked.tracking is not InvestmentTracking.HOLDINGS
                ):
                    raise DomainError("ACCOUNT_NOT_HOLDINGS_LEVEL")
            updated: list[Transaction] = []
            for leg, account_id, amount in (
                (outgoing, cmd.from_account_id, -cmd.amount_cents),
                (incoming, cmd.to_account_id, cmd.amount_cents),
            ):
                if leg is None or account_id is None:
                    continue
                account = found(uow.accounts.get(account_id), "account")
                if account.id != leg.account_id and not account.is_active:
                    raise DomainError("ACCOUNT_INACTIVE")
                if account.kind not in TRANSFER_ACCOUNT_KINDS:
                    raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=account.kind.value)
                changed = replace(
                    leg,
                    account_id=account_id,
                    posted_on=cmd.posted_on,
                    amount_cents=amount,
                    description=description,
                    description_search=normalize_search(description),
                    notes=clean_text(cmd.notes) if cmd.notes else None,
                    # the note is tagged on the leg of its own account only
                    holding_id=holding_id if account_id == holding_account_id else None,
                )
                uow.transactions.update(changed)
                updated.append(changed)
            uow.commit()
        return updated
