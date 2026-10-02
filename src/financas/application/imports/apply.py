"""Runs an import plan through the existing use cases and checks the result (CLAUDE.md 13.1).

Meant for a *working copy* of the database: the adapter swaps the copy in only when ``verify``
finds nothing wrong. Nothing here writes Portuguese: the texts stored as data (notes, the payment
description) come from callbacks supplied by the adapter.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from financas.application.imports.model import (
    AccountSpec,
    EntryAction,
    ImportPlan,
    LegacyStatementTotal,
    PaymentAction,
    PlanAction,
    TransferAction,
)
from financas.application.imports.planner import account_totals, expected_counts
from financas.application.use_cases.cards import (
    AdjustInstallment,
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind, TransactionKind
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.rules import validate_category_kind
from financas.domain.services.text import normalize_search


@dataclass(frozen=True)
class ApplyResult:
    account_ids: dict[str, str]  # plan key -> account id
    entries: int
    transfers: int
    plans: int
    payments: int


class ApplyImport:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(
        self,
        plan: ImportPlan,
        entry_notes: Callable[[EntryAction | PlanAction], str | None],
        payment_description: str,
    ) -> ApplyResult:
        if plan.errors:
            raise DomainError("IMPORT_PLAN_HAS_ERRORS")
        with self._uow as work:
            if any(work.transactions.list_by_account(a.id) for a in work.accounts.list_all()):
                raise DomainError("IMPORT_DATABASE_NOT_EMPTY")
        account_ids = self._ensure_accounts(plan.accounts)
        with self._uow as work:
            category_ids = {c.slug: c.id for c in work.categories.list_all()}
        entries = transfers = plans = 0
        payments: list[PaymentAction] = []
        for action in plan.actions:
            if isinstance(action, EntryAction):
                self._entry(action, account_ids, category_ids, entry_notes(action))
                entries += 1
            elif isinstance(action, TransferAction):
                self._transfer(action, account_ids)
                transfers += 1
            elif isinstance(action, PlanAction):
                self._plan(action, account_ids, category_ids, entry_notes(action))
                plans += 1
            else:
                payments.append(action)  # statements must exist first
        for payment in payments:
            self._payment(payment, account_ids, payment_description)
        return ApplyResult(account_ids, entries, transfers, plans, len(payments))

    # ---- accounts ----------------------------------------------------------------------------

    def _ensure_accounts(self, specs: tuple[AccountSpec, ...]) -> dict[str, str]:
        ids: dict[str, str] = {}
        for spec in specs:
            with self._uow as work:
                institution = next(
                    (
                        i
                        for i in work.institutions.list_all()
                        if normalize_search(i.name) == normalize_search(spec.institution)
                    ),
                    None,
                )
                existing = next(
                    (
                        a
                        for a in work.accounts.list_all()
                        if normalize_search(a.nickname) == normalize_search(spec.nickname)
                    ),
                    None,
                )
            if existing is not None:
                if existing.kind is not spec.kind:
                    raise DomainError("IMPORT_ACCOUNT_KIND_CONFLICT", account=spec.key)
                ids[spec.key] = existing.id
                continue
            if institution is None:
                institution = CreateInstitution(self._uow).execute(
                    CreateInstitutionCommand(
                        name=spec.institution, group_slug=spec.institution_group
                    )
                )
            card = spec.kind is AccountKind.CREDIT_CARD
            account = CreateAccount(self._uow).execute(
                CreateAccountCommand(
                    spec.kind,
                    institution.id,
                    spec.nickname,
                    closing_days_before_due=spec.closes_before_due if card else None,
                    due_day=spec.due_day if card else None,
                    credit_limit_cents=spec.limit_cents if card else None,
                    asset_class=spec.asset_class,
                    opening_balance_cents=spec.opening_cents,
                    opening_balance_on=spec.opening_on,
                )
            )
            ids[spec.key] = account.id
        return ids

    # ---- actions -----------------------------------------------------------------------------

    def _entry(
        self,
        action: EntryAction,
        accounts: dict[str, str],
        categories: dict[str, str],
        notes: str | None,
    ) -> None:
        RegisterTransaction(self._uow).execute(
            RegisterTransactionCommand(
                account_id=accounts[action.account_key],
                posted_on=action.date,
                kind=TransactionKind(action.kind.value),
                amount_cents=action.amount_cents,
                description=action.description or action.category_slug,
                category_id=categories[action.category_slug],
                is_recurring=action.recurring,
                notes=notes,
                statement_month=action.statement,
            )
        )

    def _transfer(self, action: TransferAction, accounts: dict[str, str]) -> None:
        RegisterTransfer(self._uow).execute(
            RegisterTransferCommand(
                from_account_id=accounts[action.from_key] if action.from_key else None,
                to_account_id=accounts[action.to_key] if action.to_key else None,
                posted_on=action.date,
                amount_cents=action.amount_cents,
                description=action.description,
            )
        )

    def _plan(
        self,
        action: PlanAction,
        accounts: dict[str, str],
        categories: dict[str, str],
        notes: str | None,
    ) -> None:
        result = RegisterCardPurchase(self._uow).execute(
            CardPurchaseCommand(
                account_id=accounts[action.account_key],
                description=action.description,
                purchased_on=action.purchased_on,
                category_id=categories[action.category_slug],
                installments=action.total_installments,
                current_installment=action.first_number,
                installment_cents=action.installment_cents,
                statement_month=action.first_statement,
                is_recurring=action.recurring,
                notes=notes,
            )
        )
        by_number = {t.installment_number: t for t in result.transactions}
        for installment in action.installments:  # the sheet's own amounts win
            entry = by_number[installment.number]
            if -entry.amount_cents != installment.amount_cents:
                AdjustInstallment(self._uow, self._clock).execute(
                    entry.id, installment.amount_cents
                )

    def _payment(self, action: PaymentAction, accounts: dict[str, str], description: str) -> None:
        card_id = accounts[action.card_key]
        if action.statement is None:
            raise DomainError("IMPORT_PAYMENT_WITHOUT_STATEMENT")
        with self._uow as work:
            statement = work.statements.get_by_card_month(card_id, action.statement)
        if statement is None:
            raise DomainError("IMPORT_PAYMENT_WITHOUT_STATEMENT")
        PayStatement(self._uow, self._clock).execute(
            PayStatementCommand(
                statement_id=statement.id,
                from_account_id=accounts[action.from_key] if action.from_key else None,
                paid_on=action.date,
                amount_cents=action.amount_cents,
                description=description,
            )
        )


# ---- verification ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    code: str  # the interface translates it
    ok: bool
    detail: str = ""


@dataclass
class Verification:
    checks: list[Check] = field(default_factory=lambda: [])

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, code: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(code, ok, detail))


def verify_import(uow: UnitOfWork, plan: ImportPlan, account_ids: dict[str, str]) -> Verification:
    """Compares what is in the database with what the plan promised, account by account."""
    result = Verification()
    expected_n = expected_counts(plan)
    expected_sum = account_totals(plan)
    card_keys = {a.key for a in plan.accounts if a.kind is AccountKind.CREDIT_CARD}
    with uow as work:
        categories = {c.id: c for c in work.categories.list_all()}
        statement_ids = {s.id for s in work.statements.list_all()}
        legs: dict[str, list[int]] = defaultdict(list)
        total_rows = 0
        for key, account_id in account_ids.items():
            rows = work.transactions.list_by_account(account_id)
            total_rows += len(rows)
            result.add("COUNT_BY_ACCOUNT", len(rows) == expected_n.get(key, 0), key)
            result.add(
                "SUM_BY_ACCOUNT",
                sum(t.amount_cents for t in rows) == expected_sum.get(key, 0),
                key,
            )
            for t in rows:
                if t.transfer_id:
                    legs[t.transfer_id].append(t.amount_cents)
                else:
                    validate_category_kind(t.kind, categories[t.category_id].kind)
                if key in card_keys and t.kind is not TransactionKind.TRANSFER:
                    result.add("CARD_ENTRY_STATEMENT", t.statement_id in statement_ids, key)
        broken = [
            i
            for i, amounts in legs.items()
            if len(amounts) > 2 or (sum(amounts) != 0 and len(amounts) == 2)
        ]
        result.add("TRANSFER_LEGS", not broken, str(len(broken)))
        result.add("HAS_ENTRIES", total_rows > 0, str(total_rows))
        for spec in plan.accounts:
            if spec.opening_cents is None:
                continue
            anchors = work.anchors.list_for_account(account_ids[spec.key])
            found_anchor = any(
                a.balance_cents == spec.opening_cents and a.on_date == spec.opening_on
                for a in anchors
            )
            result.add("OPENING_BALANCE", found_anchor, spec.key)
    return result


def compare_statement_totals(
    uow: UnitOfWork,
    plan: ImportPlan,
    account_ids: dict[str, str],
    sheet_totals: list[LegacyStatementTotal],
) -> tuple[int, list[str]]:
    """``(equal count, ["<card key> <month>", ...] that differ)`` against the derived sheet.

    The sheet's total is gross (refunds apart), the system's is net, so the sheet total minus its
    refunds is what is compared. Informational: a difference is listed, never fixed.
    """
    cards = {
        normalize_search(a.legacy_institution): a.key
        for a in plan.accounts
        if a.kind is AccountKind.CREDIT_CARD and a.legacy_institution
    }
    equal = 0
    different: list[str] = []
    with uow as work:
        for line in sheet_totals:
            key = cards.get(normalize_search(line.institution))
            if key is None or line.statement.year != plan.year:
                continue
            statement = work.statements.get_by_card_month(account_ids[key], line.statement)
            net = (
                -sum(
                    t.amount_cents
                    for t in work.transactions.list_by_statement(statement.id)
                    if t.kind is not TransactionKind.TRANSFER
                )
                if statement
                else None
            )
            if net == line.total_cents - line.refunds_cents:
                equal += 1
            else:
                different.append(f"{key} {line.statement}")
    return equal, different
