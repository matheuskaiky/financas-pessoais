"""Entering, deleting and categorizing income, expenses, refunds and transfers."""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.queries.cards import StatementView, is_locked, statement_view
from financas.application.use_cases._cards import assignment_for, ensure_statement, require_card
from financas.application.use_cases._common import UNSET, Unset, found, new_id
from financas.application.use_cases.deletion import delete_entry
from financas.application.use_cases.plan_dates import reschedule_plan
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    Category,
    InstallmentPlan,
    PaymentMethod,
    StatementStatus,
    Transaction,
    TransactionKind,
    TransactionSplit,
)
from financas.domain.money import YearMonth
from financas.domain.ports import Clock, UnitOfWork, Work
from financas.domain.rules import (
    validate_category_kind,
    validate_payment_method,
    validate_sign,
    validate_transfer_accounts,
)
from financas.domain.services.merchants import settle_description
from financas.domain.services.splits import distribute_items, validate_split_amounts
from financas.domain.services.text import clean_merchant, clean_text, normalize_search

# Phase 1: income, expense and refund are entered on checking accounts only.
# Cards (Phase 2) and investment accounts (Phase 3) get their own flows.
_ENTRY_ACCOUNT_KINDS = {AccountKind.CHECKING}
_TRANSFER_ACCOUNT_KINDS = {AccountKind.CHECKING, AccountKind.INVESTMENT}

_DEFAULT_CATEGORY_SLUG = {
    TransactionKind.EXPENSE: "uncategorized",
    TransactionKind.INCOME: "other_income",
    TransactionKind.REFUND: "refund",
    TransactionKind.TRANSFER: "transfer",
}


def _check_account(account: Account, allowed: set[AccountKind]) -> None:
    if not account.is_active:
        raise DomainError("ACCOUNT_INACTIVE")
    if account.kind not in allowed:
        raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=account.kind.value)


def _magnitude(amount_cents: int) -> int:
    if amount_cents <= 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")
    return amount_cents


@dataclass(frozen=True)
class SplitItem:
    """One item typed on the form: description, category and a positive amount."""

    description: str
    category_id: str
    amount_cents: int


@dataclass(frozen=True)
class RegisterTransactionCommand:
    """``amount_cents`` is the positive magnitude; the sign comes from ``kind``."""

    account_id: str
    posted_on: dt.date
    kind: TransactionKind
    amount_cents: int
    description: str
    category_id: str | None = None
    is_recurring: bool = False
    notes: str | None = None
    statement_month: YearMonth | None = None  # cards only; default comes from the card cycle
    merchant: str | None = None  # where the money went (optional)
    # expenses only: the items of an itemized expense; they add up to ``amount_cents`` and carry
    # the categories (the entry itself then has none, 9.10)
    splits: tuple[SplitItem, ...] = ()
    is_refunded: bool = False  # expenses only: kept on record, counted nowhere (9.10)
    # how it was paid: any bank method on a checking account; a card purchase is always
    # ``credit_card`` (whatever is given), and ``None`` on a bank account means "not informed"
    payment_method: PaymentMethod | None = None


class RegisterTransaction:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RegisterTransactionCommand) -> Transaction:
        if cmd.kind is TransactionKind.TRANSFER:
            raise DomainError("USE_TRANSFER_FOR_TRANSFERS")
        magnitude = _magnitude(cmd.amount_cents)
        description = clean_text(cmd.description)
        if not description:
            raise DomainError("EMPTY_DESCRIPTION")
        amount = -magnitude if cmd.kind is TransactionKind.EXPENSE else magnitude
        validate_sign(cmd.kind, amount)
        if cmd.splits and cmd.kind is not TransactionKind.EXPENSE:
            raise DomainError("SPLIT_ONLY_FOR_EXPENSES")
        if cmd.splits and cmd.category_id:  # the items carry the categories
            raise DomainError("PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS")
        if cmd.is_refunded and cmd.kind is not TransactionKind.EXPENSE:
            raise DomainError("REFUND_ONLY_FOR_EXPENSES")
        merchant = clean_merchant(cmd.merchant)
        if cmd.kind is TransactionKind.EXPENSE:  # "Mouse Gamer - Kabum" -> merchant Kabum
            description, merchant = settle_description(description, merchant)
        with self._uow as uow:
            account = found(uow.accounts.get(cmd.account_id), "account")
            statement_id: str | None = None
            if account.kind is AccountKind.CREDIT_CARD:
                # Purchases, charges and refunds on a card go to a statement (9.3, 9.5).
                if cmd.kind is TransactionKind.INCOME:
                    raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=account.kind.value)
                card = require_card(uow, account.id)
                month = assignment_for(uow, card, cmd.posted_on, cmd.statement_month).month
                statement_id = ensure_statement(uow, card, month).id
            else:
                if cmd.statement_month is not None:
                    raise DomainError("STATEMENT_ONLY_FOR_CARDS")
                _check_account(account, _ENTRY_ACCOUNT_KINDS)
            items = [
                (*checked_split_item(uow, cmd.kind, item), abs(item.amount_cents))
                for item in cmd.splits
            ]
            category_id: str | None = None
            if items:
                validate_split_amounts(magnitude, [cents for _, _, cents in items])
            else:
                if cmd.category_id is None:
                    category = found(
                        uow.categories.get_by_slug(_DEFAULT_CATEGORY_SLUG[cmd.kind]), "category"
                    )
                else:
                    category = found(uow.categories.get(cmd.category_id), "category")
                validate_category_kind(cmd.kind, category.kind)
                category_id = category.id
            transaction = Transaction(
                id=new_id(),
                account_id=cmd.account_id,
                posted_on=cmd.posted_on,
                kind=cmd.kind,
                category_id=category_id,
                amount_cents=amount,
                description=description,
                description_search=normalize_search(description),
                is_recurring=cmd.is_recurring,
                notes=clean_text(cmd.notes) if cmd.notes else None,
                statement_id=statement_id,
                merchant=merchant,
                is_refunded=cmd.is_refunded,
                payment_method=validate_payment_method(account.kind, cmd.kind, cmd.payment_method),
            )
            uow.transactions.add_many([transaction])
            if items:
                uow.transactions.set_splits(
                    transaction.id,
                    [
                        TransactionSplit(new_id(), transaction.id, text, category, cents)
                        for text, category, cents in items
                    ],
                )
            uow.commit()
        return transaction


@dataclass(frozen=True)
class RegisterTransferCommand:
    """Money moved from ``from_account_id`` to ``to_account_id``.

    Either side may be ``None`` when that account is not tracked: one leg is created.
    ``holding_id`` tags the leg that lands on (or leaves) an investment account (9.6).
    """

    from_account_id: str | None
    to_account_id: str | None
    posted_on: dt.date
    amount_cents: int
    description: str = ""
    notes: str | None = None
    holding_id: str | None = None


def build_transfer_legs(uow: Work, cmd: RegisterTransferCommand) -> list[Transaction]:
    """Validate and build the legs inside an open unit of work (the caller adds and commits)."""
    magnitude = _magnitude(cmd.amount_cents)
    validate_transfer_accounts(cmd.from_account_id, cmd.to_account_id)
    description = clean_text(cmd.description)
    transfer_id = new_id()
    category = found(uow.categories.get_by_slug("transfer"), "category")
    holding_account_id = None
    if cmd.holding_id is not None:  # the holding is tagged on the leg of its own account only
        holding_account_id = found(uow.holdings.get(cmd.holding_id), "holding").account_id
    legs: list[Transaction] = []
    for account_id, amount in (
        (cmd.from_account_id, -magnitude),
        (cmd.to_account_id, magnitude),
    ):
        if account_id is None:
            continue
        account = found(uow.accounts.get(account_id), "account")
        _check_account(account, _TRANSFER_ACCOUNT_KINDS)
        tag = holding_account_id is not None and account.id == holding_account_id
        legs.append(
            Transaction(
                id=new_id(),
                account_id=account_id,
                posted_on=cmd.posted_on,
                kind=TransactionKind.TRANSFER,
                category_id=category.id,
                amount_cents=amount,
                description=description,
                description_search=normalize_search(description),
                transfer_id=transfer_id,
                notes=clean_text(cmd.notes) if cmd.notes else None,
                holding_id=cmd.holding_id if tag else None,
            )
        )
    return legs


class RegisterTransfer:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RegisterTransferCommand) -> list[Transaction]:
        with self._uow as uow:
            legs = build_transfer_legs(uow, cmd)
            uow.transactions.add_many(legs)
            uow.commit()
        return legs


class DeleteTransaction:
    """Deletes an entry; for a transfer, every leg goes together."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, transaction_id: str) -> int:
        with self._uow as uow:
            transaction = found(uow.transactions.get(transaction_id), "transaction")
            removed = delete_entry(uow, transaction, self._clock.today())
            uow.commit()
        return removed


@dataclass(frozen=True)
class PaymentInfo:
    """A statement payment seen from either of its legs (9.5)."""

    card_leg: Transaction  # the credit on the card, linked to the statement
    origin_leg: Transaction | None  # the debit on the checking account; none when untracked
    statement: StatementView


def checked_split_item(uow: Work, kind: TransactionKind, item: SplitItem) -> tuple[str, str]:
    """``(description, category id)`` of an item, or an error: it needs a description and a
    category of the entry's kind (an expense item takes an expense category)."""
    description = clean_text(item.description)
    if not description:
        raise DomainError("EMPTY_DESCRIPTION")
    category = found(uow.categories.get(item.category_id), "category")
    validate_category_kind(kind, category.kind)
    return description, category.id


def plan_scope_entries(uow: Work, entry: Transaction, today: dt.date) -> list[Transaction]:
    """The installments plan-wide items cover: the one being edited plus every other installment
    of its plan on an open or future statement (closed and paid ones are history; refunded ones
    count nowhere). In installment order."""
    assert entry.plan_id is not None
    views: dict[str, StatementView] = {}
    covered: list[Transaction] = []
    for sibling in uow.transactions.list_by_plan(entry.plan_id):
        if sibling.id != entry.id:
            if sibling.statement_id is None:
                continue
            if sibling.statement_id not in views:
                statement = found(uow.statements.get(sibling.statement_id), "statement")
                views[sibling.statement_id] = statement_view(uow, statement, today)
            view = views[sibling.statement_id]
            if view.status not in {StatementStatus.OPEN, StatementStatus.FUTURE} or is_locked(view):
                continue
        covered.append(sibling)
    if all(e.id != entry.id for e in covered):  # an entry edited with the acknowledgement
        covered.append(entry)
    return sorted(covered, key=lambda e: e.installment_number or 0)


@dataclass(frozen=True)
class PlanItems:
    """The plan-wide view of an installment's items (for the edit form)."""

    covered_total_cents: int  # what the covered installments add up to (positive)
    covered_count: int
    items: tuple[SplitItem, ...]  # their items summed by description and category, in order


@dataclass(frozen=True)
class EntryEditState:
    """What the edit form needs to know about an entry (nothing is written)."""

    entry: Transaction
    statement: StatementView | None  # card entries only
    is_transfer: bool
    is_installment: bool  # part of a plan: date and card follow the plan and cannot change
    plan: InstallmentPlan | None = None
    splits: tuple[TransactionSplit, ...] = ()
    payment: PaymentInfo | None = None  # set when the entry is a leg of a statement payment
    plan_items: PlanItems | None = None  # installments only: the plan-wide scope of the items

    @property
    def is_payment(self) -> bool:
        return self.payment is not None

    @property
    def is_closed_statement(self) -> bool:
        """Closed and unpaid: editing is allowed, but only after the user acknowledges it."""
        return self.statement is not None and self.statement.status is StatementStatus.CLOSED

    @property
    def is_locked(self) -> bool:
        return self.statement is not None and is_locked(self.statement)


class GetEntryEditState:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, transaction_id: str) -> EntryEditState:
        with self._uow as uow:
            entry = found(uow.transactions.get(transaction_id), "transaction")
            plan = uow.plans.get(entry.plan_id) if entry.plan_id else None
            splits = uow.transactions.splits_for([entry.id]).get(entry.id, [])
            view = None
            payment = None
            plan_items = None
            if entry.plan_id and entry.kind is TransactionKind.EXPENSE:
                covered = plan_scope_entries(uow, entry, self._clock.today())
                saved = uow.transactions.splits_for([e.id for e in covered])
                totals: dict[tuple[str, str], int] = {}
                for e in covered:
                    for part in saved.get(e.id, []):
                        key = (part.description, part.category_id)
                        totals[key] = totals.get(key, 0) + part.amount_cents
                plan_items = PlanItems(
                    sum(abs(e.amount_cents) for e in covered),
                    len(covered),
                    tuple(
                        SplitItem(text, category, cents)
                        for (text, category), cents in totals.items()
                    ),
                )
            if entry.statement_id and entry.kind is not TransactionKind.TRANSFER:
                statement = found(uow.statements.get(entry.statement_id), "statement")
                view = statement_view(uow, statement, self._clock.today())
            elif entry.kind is TransactionKind.TRANSFER and entry.transfer_id:
                legs = uow.transactions.list_by_transfer(entry.transfer_id)
                card_leg = next((leg for leg in legs if leg.statement_id), None)
                if card_leg is not None:  # a transfer that credits a statement: a payment
                    statement = found(uow.statements.get(card_leg.statement_id or ""), "statement")
                    payment = PaymentInfo(
                        card_leg,
                        next((leg for leg in legs if leg.id != card_leg.id), None),
                        statement_view(uow, statement, self._clock.today()),
                    )
        return EntryEditState(
            entry,
            view,
            is_transfer=entry.kind is TransactionKind.TRANSFER,
            is_installment=entry.plan_id is not None,
            plan=plan,
            splits=tuple(splits),
            payment=payment,
            plan_items=plan_items,
        )


@dataclass(frozen=True)
class UpdateTransactionCommand:
    """The full desired state of an entry. ``amount_cents`` is the positive magnitude.

    ``category_id`` ``None`` keeps the current one. ``account_id`` ``None`` keeps the account; a
    change must stay within the same account kind. ``acknowledge_closed`` is the user's explicit
    "I know the statement is already closed" (9.5).

    On an installment only ``description``, ``category_id``, ``amount_cents`` and ``notes`` may
    change (``posted_on`` must be the entry's own date; the account stays). With
    ``propagate_plan_metadata`` a new description and/or category also goes to the other
    installments of the plan (and to the plan itself).

    ``is_refunded`` marks an expense as given back: it stays listed but counts nowhere (``None``
    keeps the current value). On an installment ``refund_pending_installments`` applies the new
    value to the plan's other installments on open or future statements. Changing the flag is an
    edit like any other: a paid statement refuses it, a closed one needs the acknowledgement.

    ``splits`` itemizes an expense (``None`` keeps the items it has, an empty tuple removes
    them). The items must add up to the amount; so an amount change on an itemized entry needs
    new items. Installments are not itemized.
    """

    transaction_id: str
    posted_on: dt.date
    amount_cents: int
    description: str
    category_id: str | None = None
    account_id: str | None = None
    notes: str | None = None
    acknowledge_closed: bool = False
    propagate_plan_metadata: bool = True
    is_refunded: bool | None = None
    refund_pending_installments: bool = False
    splits: tuple[SplitItem, ...] | None = None
    merchant: Unset | str | None = UNSET  # ``UNSET`` keeps it, ``None`` or "" clears it
    payment_method: Unset | PaymentMethod | None = UNSET  # ``UNSET`` keeps it, ``None`` clears it
    # installments only: a new purchase date for the whole plan (see ``plan_dates``), in the same
    # unit of work as the rest of the edit
    purchase_date: dt.date | None = None


class UpdateTransaction:
    """Edit an income, expense or refund entry, or a card purchase (single or one installment).

    Transfers (two legs) are refused. An installment keeps its date and card (they follow the
    statement of its plan); its description and category can spread to the plan's other
    installments, but only to those on open or future statements: closed and paid ones are
    history and stay as they are, and the amount and notes are never propagated. An
    entry on a paid statement is history and never changes. On a closed, unpaid statement the
    edit needs the user's acknowledgement. On a card, a new date or card re-runs the statement
    assignment (9.3); any other change keeps the statement stored on the entry.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: UpdateTransactionCommand) -> Transaction:
        magnitude = _magnitude(cmd.amount_cents)
        description = clean_text(cmd.description)
        if not description:
            raise DomainError("EMPTY_DESCRIPTION")
        with self._uow as uow:
            entry = found(uow.transactions.get(cmd.transaction_id), "transaction")
            if entry.kind is TransactionKind.TRANSFER:
                raise DomainError("TRANSFER_NOT_EDITABLE")
            if entry.plan_id is not None and (
                cmd.posted_on != entry.posted_on
                or (cmd.account_id is not None and cmd.account_id != entry.account_id)
            ):
                raise DomainError("INSTALLMENT_FIELD_LOCKED")
            if entry.plan_id is not None and cmd.purchase_date is not None:
                reschedule_plan(uow, entry.plan_id, cmd.purchase_date, self._clock.today())
                entry = found(uow.transactions.get(entry.id), "transaction")  # it may have moved
                cmd = replace(cmd, posted_on=entry.posted_on)
            old_account = found(uow.accounts.get(entry.account_id), "account")
            account_id = cmd.account_id or entry.account_id
            account = found(uow.accounts.get(account_id), "account")
            if account.kind is not old_account.kind:
                raise DomainError("ACCOUNT_KIND_CHANGE_NOT_ALLOWED")
            moved = account.id != old_account.id
            statement_id = entry.statement_id
            if account.kind is AccountKind.CREDIT_CARD:
                statement_id = self._card_statement(uow, cmd, entry, account, moved)
            elif moved:
                _check_account(account, _ENTRY_ACCOUNT_KINDS)
            existing_items = uow.transactions.splits_for([entry.id]).get(entry.id, [])
            itemized = bool(cmd.splits) if cmd.splits is not None else bool(existing_items)
            category_id: str | None
            if itemized:
                # the items carry the categories: the entry itself has none (CLAUDE.md 9.10)
                if entry.kind is not TransactionKind.EXPENSE:
                    raise DomainError("SPLIT_ONLY_FOR_EXPENSES")
                if cmd.category_id:
                    raise DomainError("PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS")
                category_id = None
            else:
                wanted = cmd.category_id or entry.category_id
                if wanted is None:  # the items were just removed and no category was chosen
                    wanted = found(uow.categories.get_by_slug("uncategorized"), "category").id
                category = found(uow.categories.get(wanted), "category")
                validate_category_kind(entry.kind, category.kind)
                category_id = category.id
            amount = -magnitude if entry.kind is TransactionKind.EXPENSE else magnitude
            validate_sign(entry.kind, amount)
            merchant = (
                entry.merchant if isinstance(cmd.merchant, Unset) else clean_merchant(cmd.merchant)
            )
            final_description = description
            if entry.kind is TransactionKind.EXPENSE and not merchant:  # " - Merchant" suffix
                final_description, merchant = settle_description(description, merchant)
            method = validate_payment_method(
                account.kind,
                entry.kind,
                entry.payment_method
                if isinstance(cmd.payment_method, Unset)
                else cmd.payment_method,
            )
            refunded = entry.is_refunded if cmd.is_refunded is None else cmd.is_refunded
            if refunded and entry.kind is not TransactionKind.EXPENSE:
                raise DomainError("REFUND_ONLY_FOR_EXPENSES")
            updated = replace(
                entry,
                account_id=account.id,
                posted_on=cmd.posted_on,
                category_id=category_id,
                amount_cents=amount,
                description=final_description,
                description_search=normalize_search(final_description),
                notes=clean_text(cmd.notes) if cmd.notes else None,
                statement_id=statement_id,
                is_refunded=refunded,
                merchant=merchant,
                payment_method=method,
            )
            uow.transactions.update(updated)
            self._save_splits(uow, entry, updated, magnitude, cmd)
            if entry.plan_id:
                self._propagate(uow, entry, updated, cmd)
            uow.commit()
        return updated

    def _save_splits(
        self,
        uow: Work,
        entry: Transaction,
        updated: Transaction,
        magnitude: int,
        cmd: UpdateTransactionCommand,
    ) -> None:
        existing = uow.transactions.splits_for([entry.id]).get(entry.id, [])
        plan_wide = entry.plan_id is not None and cmd.propagate_plan_metadata
        if cmd.splits is None:
            if existing:  # keeps the items: they must still add up
                validate_split_amounts(magnitude, [s.amount_cents for s in existing])
            return
        if not cmd.splits:
            if existing:
                uow.transactions.set_splits(entry.id, [])
            if plan_wide:  # the other installments that could still change lose their items too
                self._clear_plan_items(uow, entry, updated)
            return
        if entry.kind is not TransactionKind.EXPENSE:
            raise DomainError("SPLIT_ONLY_FOR_EXPENSES")
        if plan_wide:
            self._itemize_plan(uow, entry, updated, magnitude, cmd)
            return
        # signs never count: the entry's magnitude must equal the sum of the items' magnitudes
        items = [
            TransactionSplit(
                new_id(),
                entry.id,
                *checked_split_item(uow, entry.kind, item),
                abs(item.amount_cents),
            )
            for item in cmd.splits
        ]
        validate_split_amounts(abs(magnitude), [i.amount_cents for i in items])
        uow.transactions.set_splits(entry.id, items)

    def _itemize_plan(
        self,
        uow: Work,
        entry: Transaction,
        updated: Transaction,
        magnitude: int,
        cmd: UpdateTransactionCommand,
    ) -> None:
        """Plan-wide items: their totals cover every installment that can still change (the
        edited one and those on open or future statements). Each of them gets its share, so every
        installment keeps adding up and every item adds up across the plan (``distribute_items``);
        installments on closed or paid statements are history and are left alone."""
        assert cmd.splits is not None and entry.plan_id is not None
        covered = plan_scope_entries(uow, entry, self._clock.today())
        amounts = [magnitude if e.id == entry.id else abs(e.amount_cents) for e in covered]
        items = [
            (*checked_split_item(uow, entry.kind, item), abs(item.amount_cents))
            for item in cmd.splits
        ]
        cents = [c for _, _, c in items]
        validate_split_amounts(sum(amounts), cents)
        matrix = distribute_items(amounts, cents)
        for e, row in zip(covered, matrix, strict=True):
            if e.id != entry.id:  # the edited one was written already (and has no category)
                uow.transactions.update(replace(e, category_id=None))
            uow.transactions.set_splits(
                e.id,
                [
                    TransactionSplit(new_id(), e.id, text, category, share)
                    for (text, category, _), share in zip(items, row, strict=True)
                    if share > 0
                ],
            )
        plan = found(uow.plans.get(entry.plan_id), "plan")
        uow.plans.update(replace(plan, category_id=max(items, key=lambda i: i[2])[1]))

    def _clear_plan_items(self, uow: Work, entry: Transaction, updated: Transaction) -> None:
        for e in plan_scope_entries(uow, entry, self._clock.today()):
            if e.id != entry.id and uow.transactions.splits_for([e.id]):
                uow.transactions.set_splits(e.id, [])
                uow.transactions.update(replace(e, category_id=updated.category_id))

    def _propagate(
        self, uow: Work, before: Transaction, after: Transaction, cmd: UpdateTransactionCommand
    ) -> None:
        """Spread a changed description and/or category (and, on request, the refunded flag) to
        the plan's other installments on open or future statements; the plan record follows the
        description and category shown in "Parcelas ativas"."""
        renamed = cmd.propagate_plan_metadata and after.description != before.description
        recategorized = (
            cmd.propagate_plan_metadata
            and after.category_id is not None  # an itemized entry has none to give
            and after.category_id != before.category_id
        )
        moved = cmd.propagate_plan_metadata and after.merchant != before.merchant
        spread_refund = cmd.refund_pending_installments
        if not (renamed or recategorized or moved or spread_refund):
            return
        assert before.plan_id is not None
        today = self._clock.today()
        views: dict[str, StatementView] = {}
        siblings = uow.transactions.list_by_plan(before.plan_id, include_refunded=True)
        itemized_siblings = set(uow.transactions.splits_for([s.id for s in siblings]))
        for sibling in siblings:
            if sibling.id == before.id or sibling.statement_id is None:
                continue
            if sibling.statement_id not in views:
                statement = found(uow.statements.get(sibling.statement_id), "statement")
                views[sibling.statement_id] = statement_view(uow, statement, today)
            view = views[sibling.statement_id]
            if view.status not in {StatementStatus.OPEN, StatementStatus.FUTURE} or is_locked(view):
                continue
            uow.transactions.update(
                replace(
                    sibling,
                    description=after.description if renamed else sibling.description,
                    description_search=(
                        after.description_search if renamed else sibling.description_search
                    ),
                    category_id=(
                        after.category_id
                        if recategorized and sibling.id not in itemized_siblings
                        else sibling.category_id
                    ),
                    merchant=after.merchant if moved else sibling.merchant,
                    is_refunded=after.is_refunded if spread_refund else sibling.is_refunded,
                )
            )
        if renamed or recategorized:
            plan = found(uow.plans.get(before.plan_id), "plan")
            uow.plans.update(
                replace(
                    plan,
                    description=after.description if renamed else plan.description,
                    category_id=after.category_id if recategorized else plan.category_id,
                )
            )

    def _card_statement(
        self,
        uow: Work,
        cmd: UpdateTransactionCommand,
        entry: Transaction,
        card: Account,
        moved: bool,
    ) -> str:
        assert entry.statement_id is not None
        today = self._clock.today()
        current = found(uow.statements.get(entry.statement_id), "statement")
        guard = [statement_view(uow, current, today)]
        statement_id = entry.statement_id
        if moved or cmd.posted_on != entry.posted_on:
            card = require_card(uow, card.id)
            month = assignment_for(uow, card, cmd.posted_on, None).month
            target = ensure_statement(uow, card, month)
            statement_id = target.id
            if target.id != current.id:
                guard.append(statement_view(uow, target, today))
        if any(is_locked(view) for view in guard):
            raise DomainError("STATEMENT_ALREADY_PAID")
        closed = any(view.status is StatementStatus.CLOSED for view in guard)
        if closed and not cmd.acknowledge_closed:
            raise DomainError("STATEMENT_CLOSED_NEEDS_ACK")
        return statement_id


class SuggestCategory:
    """The last category used for the same normalized description and kind (9.2)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, description: str, kind: TransactionKind) -> Category | None:
        key = normalize_search(description)
        if not key:
            return None
        with self._uow as uow:
            category_id = uow.transactions.last_category_id(key, kind)
            return uow.categories.get(category_id) if category_id else None
