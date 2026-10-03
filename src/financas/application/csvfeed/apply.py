"""Runs a plan through the existing use cases and checks the result (CLAUDE.md 13.3).

Meant for a *working copy* of the database: the adapter swaps the copy in only when ``verify_feed``
finds nothing wrong. Order: entries, transfers and card purchases in file order, then statement
payments (they need the statements), then balances (they compare with the entries). Nothing here
writes Portuguese: the stored text of a payment without a description comes from the adapter.
"""

import datetime as dt
from dataclasses import dataclass, field

from financas.application.csvfeed.model import (
    BalanceAction,
    EntryAction,
    FeedPlan,
    PaymentAction,
    PurchaseAction,
    TransferAction,
)
from financas.application.imports.apply import Verification
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind
from financas.domain.ports import Clock, UnitOfWork


@dataclass(frozen=True)
class BalanceOutcome:
    account_id: str
    on_date: dt.date
    informed_cents: int
    computed_cents: int | None
    difference_cents: int | None


@dataclass(frozen=True)
class FeedApplyResult:
    entries: int
    purchases: int
    transfers: int
    payments: int
    balances: list[BalanceOutcome] = field(default_factory=lambda: [])


class ApplyFeed:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, plan: FeedPlan, payment_description: str) -> FeedApplyResult:
        entries = purchases = transfers = payments = 0
        outcomes: list[BalanceOutcome] = []
        for action in plan.actions:
            if isinstance(action, EntryAction):
                self._entry(action)
                entries += 1
            elif isinstance(action, PurchaseAction):
                self._purchase(action)
                purchases += 1
            elif isinstance(action, TransferAction):
                RegisterTransfer(self._uow).execute(
                    RegisterTransferCommand(
                        from_account_id=action.from_id,
                        to_account_id=action.to_id,
                        posted_on=action.posted_on,
                        amount_cents=action.amount_cents,
                        description=action.description,
                        notes=action.notes,
                    )
                )
                transfers += 1
            elif isinstance(action, PaymentAction):
                self._payment(action, payment_description)
                payments += 1
            else:
                outcomes.append(self._balance(action))
        return FeedApplyResult(entries, purchases, transfers, payments, outcomes)

    def _entry(self, action: EntryAction) -> None:
        RegisterTransaction(self._uow).execute(
            RegisterTransactionCommand(
                account_id=action.account_id,
                posted_on=action.posted_on,
                kind=action.kind,
                amount_cents=action.amount_cents,
                description=action.description,
                category_id=action.category_id,
                is_recurring=action.recurring,
                notes=action.notes,
                statement_month=action.statement.month if action.statement else None,
            )
        )

    def _purchase(self, action: PurchaseAction) -> None:
        result = RegisterCardPurchase(self._uow).execute(
            CardPurchaseCommand(
                account_id=action.account_id,
                description=action.description,
                purchased_on=action.purchased_on,
                category_id=action.category_id,
                installments=action.installments,
                current_installment=action.first_number,
                total_cents=action.total_cents,
                installment_cents=action.installment_cents,
                statement_month=action.statement.month,
                is_recurring=False,
                notes=action.notes,
            )
        )
        made = [(ln.number, ln.statement_month, ln.amount_cents) for ln in result.preview.lines]
        planned = [(ln.number, ln.month, ln.amount_cents) for ln in action.lines]
        if made != planned:
            raise DomainError("FEED_PLAN_MISMATCH", line=action.line)

    def _payment(self, action: PaymentAction, description: str) -> None:
        with self._uow as work:
            statement = work.statements.get_by_card_month(action.card_id, action.month)
        if statement is None:
            raise DomainError("STATEMENT_NOT_FOUND", statement=str(action.month))
        PayStatement(self._uow, self._clock).execute(
            PayStatementCommand(
                statement_id=statement.id,
                from_account_id=action.from_id,
                paid_on=action.paid_on,
                amount_cents=action.amount_cents,
                description=action.description or description,
            )
        )

    def _balance(self, action: BalanceAction) -> BalanceOutcome:
        result = RecordBalance(self._uow).execute(
            RecordBalanceCommand(
                account_id=action.account_id,
                on_date=action.on_date,
                balance_cents=action.balance_cents,
                note=action.note,
                gross_balance_cents=action.gross_cents,
            )
        )
        return BalanceOutcome(
            action.account_id,
            action.on_date,
            action.balance_cents,
            result.computed_cents,
            result.difference_cents,
        )


# ---- verification ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """Transaction ids per account and per statement, taken before applying."""

    by_account: dict[str, frozenset[str]]
    transfers: frozenset[str]


def _involved(plan: FeedPlan) -> set[str]:
    ids = {t.account_id for t in plan.account_totals}
    ids |= {s.account_id for s in plan.statements}
    ids |= {a.account_id for a in plan.actions if isinstance(a, BalanceAction)}
    return ids


def snapshot(uow: UnitOfWork, plan: FeedPlan) -> Snapshot:
    by_account: dict[str, frozenset[str]] = {}
    transfers: set[str] = set()
    with uow as work:
        for account_id in _involved(plan):
            rows = work.transactions.list_by_account(account_id)
            by_account[account_id] = frozenset(t.id for t in rows)
            transfers |= {t.transfer_id for t in rows if t.transfer_id}
    return Snapshot(by_account, frozenset(transfers))


def verify_feed(uow: UnitOfWork, plan: FeedPlan, before: Snapshot) -> Verification:
    """What the database gained must equal what the plan promised, account by account and
    statement by statement; transfers have at most two opposite legs; card entries have statements.
    """
    result = Verification()
    with uow as work:
        new: dict[str, list[tuple[str, int, str | None, str | None]]] = {}
        legs: dict[str, list[int]] = {}
        cards = {a.id for a in work.accounts.list_all() if a.kind is AccountKind.CREDIT_CARD}
        statement_ids = {s.id for s in work.statements.list_all()}
        for account_id, known in before.by_account.items():
            rows = [t for t in work.transactions.list_by_account(account_id) if t.id not in known]
            new[account_id] = [(t.id, t.amount_cents, t.statement_id, t.transfer_id) for t in rows]
            for t in rows:
                if t.transfer_id and t.transfer_id not in before.transfers:
                    legs.setdefault(t.transfer_id, []).append(t.amount_cents)
            if account_id in cards:
                result.add(
                    "CARD_ENTRY_STATEMENT",
                    all(t.statement_id in statement_ids for t in rows if not t.transfer_id),
                    account_id,
                )
        expected = {t.account_id: (t.count, t.sum_cents) for t in plan.account_totals}
        for account_id in before.by_account:
            rows = new[account_id]
            count, total = expected.get(account_id, (0, 0))
            result.add("COUNT_BY_ACCOUNT", len(rows) == count, account_id)
            result.add("SUM_BY_ACCOUNT", sum(r[1] for r in rows) == total, account_id)
        for touch in plan.statements:
            statement = work.statements.get_by_card_month(touch.account_id, touch.month)
            linked = (
                [
                    t
                    for t in work.transactions.list_by_statement(statement.id)
                    if t.id not in before.by_account[touch.account_id]
                ]
                if statement
                else []
            )
            ok = (
                statement is not None
                and len(linked) == touch.entries + touch.payments
                and sum(t.amount_cents for t in linked) == touch.signed_sum_cents
            )
            result.add("STATEMENT_TOTALS", ok, f"{touch.account_id} {touch.month}")
        broken = [i for i, a in legs.items() if len(a) > 2 or (len(a) == 2 and sum(a) != 0)]
        result.add("TRANSFER_LEGS", not broken, str(len(broken)))
        for action in plan.actions:
            if not isinstance(action, BalanceAction):
                continue
            found = [
                a
                for a in work.anchors.list_for_account(action.account_id)
                if a.on_date == action.on_date
            ]
            result.add(
                "BALANCE_RECORDED",
                len(found) == 1
                and found[0].balance_cents == action.balance_cents
                and found[0].gross_balance_cents == action.gross_cents,
                action.account_id,
            )
    return result
