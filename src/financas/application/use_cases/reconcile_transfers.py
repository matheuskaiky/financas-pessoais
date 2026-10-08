"""Scan the ledger for the two halves of an internal transfer typed as separate entries.

``ScanTransfers`` only reads. ``ApplyTransferPairs`` turns each clear pair into one transfer
(``kind=transfer``, one shared ``transfer_id``, the neutral ``transfer`` category), all or nothing:
the plain income or expense stops counting as income or expense (CLAUDE.md 9.1), which is why the
user reviews the dry run first and applies explicitly.
"""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.queries.transfer_links import TRANSFER_ACCOUNT_KINDS
from financas.application.services.reconcile_transfers import (
    Candidate,
    PairProposal,
    Reconciliation,
    find_pairs,
)
from financas.application.use_cases._common import found, new_id
from financas.domain.models import Transaction, TransactionKind
from financas.domain.ports import UnitOfWork, Work


@dataclass(frozen=True)
class PairView:
    """A proposal with what a report needs to show about each half."""

    proposal: PairProposal
    outgoing_account: str
    incoming_account: str
    outgoing_description: str
    incoming_description: str

    @property
    def posted_on(self) -> dt.date:
        return self.proposal.outgoing.posted_on

    @property
    def amount_cents(self) -> int:
        return self.proposal.incoming.amount_cents


@dataclass(frozen=True)
class ScanReport:
    pairs: tuple[PairView, ...]
    ambiguous: int
    considered: int  # entries that were eligible to be matched

    @property
    def applicable(self) -> tuple[PairView, ...]:
        return tuple(p for p in self.pairs if p.proposal.applicable)


@dataclass(frozen=True)
class ApplyResult:
    linked: int  # pairs turned into one transfer each


def _eligible(uow: Work, entry: Transaction, lone_legs: set[str]) -> bool:
    """Only plain entries on checking/investment accounts, or a lone transfer leg."""
    if entry.is_refunded or entry.plan_id or entry.statement_id or entry.holding_id:
        return False
    if entry.kind is TransactionKind.TRANSFER:
        return entry.id in lone_legs
    if entry.kind not in (TransactionKind.EXPENSE, TransactionKind.INCOME):
        return False
    return entry.transfer_id is None


def _candidates(uow: Work) -> tuple[list[Candidate], dict[str, Transaction], dict[str, str]]:
    accounts = {a.id: a for a in uow.accounts.list_all() if a.kind in TRANSFER_ACCOUNT_KINDS}
    entries = [e for a in accounts for e in uow.transactions.list_by_account(a, False)]
    # a transfer with a single leg: the other side is untracked, or an old import lost it
    by_transfer: dict[str, list[Transaction]] = {}
    for e in entries:
        if e.kind is TransactionKind.TRANSFER and e.transfer_id:
            by_transfer.setdefault(e.transfer_id, []).append(e)
    lone = {
        legs[0].id
        for transfer_id, legs in by_transfer.items()
        # the other leg may sit on a card (a statement payment): that is a complete transfer
        if len(legs) == 1 and len(uow.transactions.list_by_transfer(transfer_id)) == 1
    }
    plain = [e for e in entries if _eligible(uow, e, lone)]
    with_items = set(uow.transactions.splits_for([e.id for e in plain]))
    chosen = [e for e in plain if e.id not in with_items]
    candidates = [
        Candidate(
            e.id,
            e.account_id,
            e.posted_on,
            e.amount_cents,
            e.description_search,
            already_transfer=e.kind is TransactionKind.TRANSFER,
        )
        for e in chosen
    ]
    return (
        candidates,
        {e.id: e for e in chosen},
        {a.id: a.nickname for a in accounts.values()},
    )


class ScanTransfers:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self) -> ScanReport:
        with self._uow as uow:
            candidates, by_id, names = _candidates(uow)
        result: Reconciliation = find_pairs(candidates)
        views = tuple(
            PairView(
                p,
                names[p.outgoing.account_id],
                names[p.incoming.account_id],
                by_id[p.outgoing.id].description,
                by_id[p.incoming.id].description,
            )
            for p in result.pairs
        )
        return ScanReport(views, len(result.ambiguous), len(candidates))


class ApplyTransferPairs:
    """Link the applicable pairs found now, all in one unit of work."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self) -> ApplyResult:
        linked = 0
        with self._uow as uow:  # scanned and written in the same block: no check-then-act gap
            candidates, by_id, _ = _candidates(uow)
            category = found(uow.categories.get_by_slug("transfer"), "category")
            for pair in find_pairs(candidates).applicable:
                out, inc = by_id[pair.outgoing.id], by_id[pair.incoming.id]
                transfer_id = out.transfer_id or inc.transfer_id or new_id()
                for leg in (out, inc):
                    if leg.kind is TransactionKind.TRANSFER and leg.transfer_id == transfer_id:
                        continue  # a lone leg that already owns the id
                    uow.transactions.update(_as_transfer_leg(leg, transfer_id, category.id))
                linked += 1
            uow.commit()
        return ApplyResult(linked)


def _as_transfer_leg(leg: Transaction, transfer_id: str, category_id: str) -> Transaction:
    if leg.kind is TransactionKind.TRANSFER:  # a lone leg only joins the other's transfer id
        return replace(leg, transfer_id=transfer_id)
    return replace(
        leg,
        kind=TransactionKind.TRANSFER,
        category_id=category_id,
        transfer_id=transfer_id,
        payment_method=None,  # a transfer carries no method (9.1)
        is_recurring=False,
        merchant=None,
    )
