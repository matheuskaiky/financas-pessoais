"""Internal transfers seen from either leg: the reciprocal link for the feed and the edit form.

A transfer is the set of entries sharing ``transfer_id`` (two legs between tracked accounts, or one
when the other side is not tracked). The "pair" of a leg is simply the other entry of that set.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from financas.application.use_cases._common import found
from financas.domain.models import AccountKind, Transaction, TransactionKind
from financas.domain.ports import UnitOfWork

TRANSFER_ACCOUNT_KINDS = frozenset({AccountKind.CHECKING, AccountKind.INVESTMENT})


@dataclass(frozen=True)
class TransferLink:
    """The reciprocal leg of an entry."""

    other_id: str
    other_account_id: str
    other_posted_on: dt.date
    outgoing: bool  # this leg is the outflow ("Para ..."); otherwise the inflow ("De ...")
    other_holding_id: str | None = None  # the note the other leg moves money into or out of


@dataclass(frozen=True)
class TransferState:
    """Both legs of a transfer, and whether the transfer form may edit it."""

    outgoing: Transaction | None
    incoming: Transaction | None
    editable: bool

    @property
    def holding_id(self) -> str | None:
        return next((leg.holding_id for leg in self.legs if leg.holding_id), None)

    @property
    def legs(self) -> list[Transaction]:
        return [leg for leg in (self.outgoing, self.incoming) if leg is not None]


class ListTransferLinks:
    """``{entry id: TransferLink}`` for the transfer legs among ``entries`` that have a pair."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, entries: Sequence[Transaction]) -> dict[str, TransferLink]:
        links: dict[str, TransferLink] = {}
        with self._uow as uow:
            legs_of: dict[str, list[Transaction]] = {}
            for entry in entries:
                if entry.kind is not TransactionKind.TRANSFER or entry.transfer_id is None:
                    continue
                if entry.transfer_id not in legs_of:
                    legs_of[entry.transfer_id] = uow.transactions.list_by_transfer(
                        entry.transfer_id
                    )
                other = next((x for x in legs_of[entry.transfer_id] if x.id != entry.id), None)
                if other is not None:
                    links[entry.id] = TransferLink(
                        other.id,
                        other.account_id,
                        other.posted_on,
                        entry.amount_cents < 0,
                        other.holding_id,
                    )
        return links


class GetTransfer:
    """The legs of the transfer an entry belongs to (nothing is written)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, transaction_id: str) -> TransferState:
        with self._uow as uow:
            entry = found(uow.transactions.get(transaction_id), "transaction")
            legs = (
                uow.transactions.list_by_transfer(entry.transfer_id)
                if entry.transfer_id
                else [entry]
            )
            accounts = [found(uow.accounts.get(leg.account_id), "account") for leg in legs]
        # a statement payment (a card leg) or an investment holding movement has its own screen
        editable = (
            entry.kind is TransactionKind.TRANSFER
            and entry.transfer_id is not None
            and all(a.kind in TRANSFER_ACCOUNT_KINDS for a in accounts)
            and all(leg.statement_id is None for leg in legs)
        )
        return TransferState(
            outgoing=next((x for x in legs if x.amount_cents < 0), None),
            incoming=next((x for x in legs if x.amount_cents > 0), None),
            editable=editable,
        )
