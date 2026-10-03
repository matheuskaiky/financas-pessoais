"""Turns validated rows into a plan, applying the business rules of CLAUDE.md 9.1 to 9.7.

Pure: the database state comes in as a ``FeedContext``. The rules are the domain's own (category
kind, transfer accounts, card cycle with the stored dates of existing statements, installment
schedule, gross >= net), so the plan and the use cases that apply it agree by construction.
"""

import datetime as dt
import difflib
from collections import defaultdict
from dataclasses import dataclass, field, replace

from financas.application.csvfeed.model import (
    AccountInfo,
    AccountTotal,
    AmountType,
    BalanceAction,
    CategoryInfo,
    EntryAction,
    FeedAction,
    FeedContext,
    FeedIssue,
    FeedKind,
    FeedPlan,
    FeedRow,
    InstallmentLine,
    PaymentAction,
    PurchaseAction,
    StatementChoice,
    StatementInfo,
    StatementTouch,
    TransferAction,
)
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind, InvestmentTracking, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.rules import (
    validate_category_kind,
    validate_gross_balance,
    validate_transfer_accounts,
)
from financas.domain.services.card_cycle import (
    AssignmentReason,
    assign_statement,
    explicit_assignment,
    last_day_in_statement,
    statement_dates,
)
from financas.domain.services.installments import build_schedule
from financas.domain.services.text import normalize_search

_TX_KIND = {
    FeedKind.EXPENSE: TransactionKind.EXPENSE,
    FeedKind.INCOME: TransactionKind.INCOME,
    FeedKind.REFUND: TransactionKind.REFUND,
}
_DEFAULT_SLUG = {
    FeedKind.EXPENSE: "uncategorized",
    FeedKind.INCOME: "other_income",
    FeedKind.REFUND: "refund",
    FeedKind.TRANSFER: "transfer",
}
_SIDES = {AccountKind.CHECKING, AccountKind.INVESTMENT}


@dataclass
class _Touch:
    account_id: str
    month: YearMonth
    closing_date: dt.date
    due_date: dt.date
    exists: bool
    entries: int = 0
    payments: int = 0
    owed_cents: int = 0
    paid_cents: int = 0
    signed_sum_cents: int = 0


@dataclass
class _State:
    ctx: FeedContext
    issues: list[FeedIssue] = field(default_factory=lambda: [])
    warnings: list[FeedIssue] = field(default_factory=lambda: [])
    totals: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    touches: dict[tuple[str, YearMonth], _Touch] = field(default_factory=lambda: {})
    reasons: dict[AssignmentReason, int] = field(default_factory=lambda: defaultdict(int))
    payments: list[tuple[FeedRow, AccountInfo, AccountInfo | None, str, int]] = field(
        default_factory=lambda: []
    )


def _suggest(key: str, names: dict[str, str]) -> str:
    close = difflib.get_close_matches(key, list(names), n=3, cutoff=0.4)
    return " | ".join(dict.fromkeys(names[k] for k in close))


class _Planner:
    def __init__(self, ctx: FeedContext) -> None:
        self.ctx = ctx
        self.s = _State(ctx)
        self.stored: dict[tuple[str, YearMonth], StatementInfo] = {
            (s.account_id, s.month): s for s in ctx.statements
        }
        self.known: dict[str, dict[YearMonth, tuple[dt.date, dt.date]]] = defaultdict(dict)
        for st in ctx.statements:
            self.known[st.account_id][st.month] = (st.closing_date, st.due_date)

    # ---- lookups ----------------------------------------------------------------------------

    def issue(self, row: FeedRow, code: str, column: str | None, **params: str | int) -> None:
        self.s.issues.append(FeedIssue(code, row.line, column, dict(params)))

    def account(self, row: FeedRow, ref: str, column: str) -> AccountInfo | None:
        key = normalize_search(ref)
        found = [a for a in self.ctx.accounts if normalize_search(a.nickname) == key]
        if len(found) > 1:
            self.issue(row, "AMBIGUOUS_ACCOUNT", column)
            return None
        if not found:
            names = {normalize_search(a.nickname): a.nickname for a in self.ctx.accounts}
            self.issue(row, "UNKNOWN_ACCOUNT", column, suggestions=_suggest(key, names))
            return None
        return found[0]

    def category(self, row: FeedRow) -> CategoryInfo | None:
        if not row.category:
            slug = _DEFAULT_SLUG[row.kind]
            found = [c for c in self.ctx.categories if c.slug == slug]
            if not found:
                self.issue(row, "UNKNOWN_CATEGORY", "category", suggestions="")
                return None
            return found[0]
        key = normalize_search(row.category)
        found = [
            c
            for c in self.ctx.categories
            if key in (normalize_search(c.name), normalize_search(c.slug))
        ]
        if len(found) > 1:
            self.issue(row, "AMBIGUOUS_CATEGORY", "category")
            return None
        if not found:
            names = {normalize_search(c.name): c.name for c in self.ctx.categories}
            names.update({normalize_search(c.slug): c.name for c in self.ctx.categories})
            self.issue(row, "UNKNOWN_CATEGORY", "category", suggestions=_suggest(key, names))
            return None
        return found[0]

    def usable(self, row: FeedRow, account: AccountInfo, column: str) -> bool:
        if not account.is_active:
            self.issue(row, "ACCOUNT_INACTIVE", column)
            return False
        return True

    # ---- statements -------------------------------------------------------------------------

    def choice(
        self, card: AccountInfo, explicit: YearMonth | None, purchase: FeedRow
    ) -> StatementChoice:
        assert card.due_day is not None and card.closing_days_before_due is not None
        known = self.known[card.id]
        due_day, days = card.due_day, card.closing_days_before_due
        cycle = assign_statement(purchase.date, due_day, days, known)
        if explicit is not None:
            a = explicit_assignment(explicit, due_day, days, purchase.date, known)
            comparable = purchase.installment_number == 1
            cycle_month = cycle.month if comparable else None
        else:
            a, cycle_month = cycle, cycle.month
        return StatementChoice(
            a.month,
            a.closing_date,
            a.due_date,
            a.reason,
            cycle_month,
            (card.id, a.month) in self.stored,
        )

    def dates(self, card: AccountInfo, month: YearMonth) -> tuple[dt.date, dt.date]:
        """Stored dates of an existing statement, else the ones the card's settings give."""
        stored = self.stored.get((card.id, month))
        if stored is not None:
            return stored.closing_date, stored.due_date
        assert card.due_day is not None and card.closing_days_before_due is not None
        return statement_dates(month, card.due_day, card.closing_days_before_due)

    def touch(self, card: AccountInfo, month: YearMonth) -> _Touch:
        key = (card.id, month)
        if key not in self.s.touches:
            closing, due = self.dates(card, month)
            self.s.touches[key] = _Touch(card.id, month, closing, due, key in self.stored)
        return self.s.touches[key]

    def locked(self, row: FeedRow, card: AccountInfo, month: YearMonth, column: str) -> bool:
        stored = self.stored.get((card.id, month))
        if stored is not None and stored.is_locked:
            self.issue(row, "STATEMENT_ALREADY_PAID", column)
            return True
        return False

    def note_choice(self, row: FeedRow, choice: StatementChoice) -> None:
        self.s.reasons[choice.reason] += 1
        if choice.cycle_month is not None and choice.cycle_month != choice.month:
            self.s.warnings.append(
                FeedIssue(
                    "STATEMENT_OVERRIDES_CYCLE",
                    row.line,
                    "statement",
                    {"statement": str(choice.month), "cycle": str(choice.cycle_month)},
                )
            )

    def add_total(self, account_id: str, amount: int) -> None:
        self.s.totals[account_id][0] += 1
        self.s.totals[account_id][1] += amount

    # ---- rows -------------------------------------------------------------------------------

    def entry(self, row: FeedRow) -> FeedAction | None:
        before = len(self.s.issues)
        account = self.account(row, row.account, "account")
        category = self.category(row)
        if category is not None:
            try:
                validate_category_kind(_TX_KIND[row.kind], category.kind)
            except DomainError as error:
                self.issue(row, error.code, "category", **error.params)
        if account is None or not self.usable(row, account, "account"):
            return None
        card = account.kind is AccountKind.CREDIT_CARD
        if account.kind is AccountKind.INVESTMENT or (card and row.kind is FeedKind.INCOME):
            self.issue(row, "ACCOUNT_KIND_NOT_ALLOWED", "account", account_kind=account.kind.value)
            return None
        if not card:
            if row.statement is not None:
                self.issue(row, "STATEMENT_ONLY_FOR_CARDS", "statement")
            if row.installments is not None and row.installments >= 2:
                self.issue(row, "INSTALLMENTS_ONLY_ON_CARDS", "installments")
        if len(self.s.issues) > before or category is None:
            return None
        notes = row.notes or None
        if not card:
            self.add_total(
                account.id,
                row.amount_cents if row.kind is not FeedKind.EXPENSE else -row.amount_cents,
            )
            return EntryAction(
                row.line,
                account.id,
                _TX_KIND[row.kind],
                row.date,
                row.amount_cents,
                row.description,
                category.id,
                row.recurring,
                notes,
                None,
            )
        if row.installments is not None and row.installments >= 2:
            return self.purchase(row, account, category, notes)
        choice = self.choice(account, row.statement, row)
        if self.locked(row, account, choice.month, "statement" if row.statement else "date"):
            return None
        self.note_choice(row, choice)
        signed = -row.amount_cents if row.kind is FeedKind.EXPENSE else row.amount_cents
        touch = self.touch(account, choice.month)
        touch.entries += 1
        touch.owed_cents -= signed
        touch.signed_sum_cents += signed
        self.add_total(account.id, signed)
        return EntryAction(
            row.line,
            account.id,
            _TX_KIND[row.kind],
            row.date,
            row.amount_cents,
            row.description,
            category.id,
            row.recurring,
            notes,
            choice,
        )

    def purchase(
        self, row: FeedRow, card: AccountInfo, category: CategoryInfo, notes: str | None
    ) -> FeedAction | None:
        assert row.installments is not None and row.amount_type is not None
        if row.installment_number > 1 and row.statement is None:
            self.issue(row, "STATEMENT_REQUIRED", "statement")
            return None
        choice = self.choice(card, row.statement, row)
        total = row.amount_cents if row.amount_type is AmountType.TOTAL else None
        each = row.amount_cents if row.amount_type is AmountType.INSTALLMENT else None
        try:
            schedule = build_schedule(
                count=row.installments,
                first_number=row.installment_number,
                first_statement=choice.month,
                total_cents=total,
                installment_cents=each,
            )
        except DomainError as error:
            self.issue(row, error.code, "amount", **error.params)
            return None
        lines: list[InstallmentLine] = []
        bad = False
        for item in schedule:
            stored = self.stored.get((card.id, item.statement_month))
            closing, due = self.dates(card, item.statement_month)
            posted = row.date if item.number == 1 else last_day_in_statement(closing)
            lines.append(
                InstallmentLine(
                    item.number, item.statement_month, closing, due, item.amount_cents, posted
                )
            )
            if stored is not None and stored.is_locked:
                self.issue(row, "STATEMENT_ALREADY_PAID", "statement" if row.statement else "date")
                bad = True
        if bad:
            return None
        for item in lines:
            touch = self.touch(card, item.month)
            touch.entries += 1
            touch.owed_cents += item.amount_cents
            touch.signed_sum_cents -= item.amount_cents
        self.add_total_many(card.id, len(lines), -sum(ln.amount_cents for ln in lines))
        self.note_choice(row, choice)
        return PurchaseAction(
            row.line,
            card.id,
            row.date,
            row.description,
            category.id,
            notes,
            row.installments,
            row.installment_number,
            row.amount_type,
            total,
            each,
            choice,
            row.statement,
            tuple(lines),
        )

    def add_total_many(self, account_id: str, count: int, amount: int) -> None:
        self.s.totals[account_id][0] += count
        self.s.totals[account_id][1] += amount

    def transfer(self, row: FeedRow) -> FeedAction | None:
        before = len(self.s.issues)
        origin = self.account(row, row.account, "account") if row.account else None
        target = self.account(row, row.to_account, "to_account") if row.to_account else None
        if not row.account and not row.to_account:
            self.issue(row, "TRANSFER_NEEDS_ACCOUNT", "account")
        category = self.category(row) if row.category else None
        if category is not None:
            try:
                validate_category_kind(TransactionKind.TRANSFER, category.kind)
                if category.slug != "transfer":
                    raise DomainError("TRANSFER_CATEGORY_FIXED")
            except DomainError as error:
                self.issue(row, error.code, "category", **error.params)
        if len(self.s.issues) > before:
            return None
        if origin is not None and target is not None:
            try:
                validate_transfer_accounts(origin.id, target.id)
            except DomainError as error:
                self.issue(row, error.code, "to_account")
                return None
        payment = target is not None and target.kind is AccountKind.CREDIT_CARD
        for side, column in ((origin, "account"), (target, "to_account")):
            if side is None or not self.usable(row, side, column):
                continue
            if side.tracking is InvestmentTracking.HOLDINGS:
                self.issue(row, "HOLDING_REQUIRED", column)
            if payment and side is origin:  # a statement is paid from a checking account
                allowed = side.kind is AccountKind.CHECKING
            else:
                allowed = side.kind in _SIDES or (payment and side is target)
            if not allowed:
                self.issue(row, "ACCOUNT_KIND_NOT_ALLOWED", column, account_kind=side.kind.value)
        if payment:
            if row.statement is None:
                self.issue(row, "STATEMENT_REQUIRED", "statement")
            if row.notes:
                self.issue(row, "PAYMENT_NOTES_NOT_SUPPORTED", "notes")
        elif row.statement is not None:
            self.issue(row, "STATEMENT_ONLY_FOR_CARDS", "statement")
        if len(self.s.issues) > before:
            return None
        if payment:
            assert target is not None and row.statement is not None
            self.s.payments.append((row, target, origin, row.description, row.amount_cents))
            return None
        if origin is not None:
            self.add_total(origin.id, -row.amount_cents)
        if target is not None:
            self.add_total(target.id, row.amount_cents)
        return TransferAction(
            row.line,
            origin.id if origin else None,
            target.id if target else None,
            row.date,
            row.amount_cents,
            row.description,
            row.notes or None,
        )

    def resolve_payments(self) -> list[FeedAction]:
        actions: list[FeedAction] = []
        for row, card, origin, description, amount in self.s.payments:
            assert row.statement is not None
            key = (card.id, row.statement)
            if key not in self.stored and key not in self.s.touches:
                self.issue(row, "STATEMENT_NOT_FOUND", "statement", statement=str(row.statement))
                continue
            touch = self.touch(card, row.statement)
            touch.payments += 1
            touch.paid_cents += amount
            touch.signed_sum_cents += amount
            if origin is not None:
                self.add_total(origin.id, -amount)
            self.add_total(card.id, amount)
            actions.append(
                PaymentAction(
                    row.line,
                    card.id,
                    origin.id if origin else None,
                    row.statement,
                    row.date,
                    amount,
                    description,
                )
            )
        return actions

    def balances(self, rows: list[FeedRow]) -> list[FeedAction]:
        actions: list[FeedAction] = []
        first: dict[tuple[str, dt.date], int] = {}
        for row in rows:
            before = len(self.s.issues)
            account = self.account(row, row.account, "account")
            if account is None or not self.usable(row, account, "account"):
                continue
            if account.kind is AccountKind.CREDIT_CARD:
                self.issue(row, "BALANCE_NOT_FOR_CARDS", "account")
            if account.tracking is InvestmentTracking.HOLDINGS:
                self.issue(row, "ACCOUNT_TRACKS_HOLDINGS", "account")
            if row.gross_cents is not None:
                if account.kind is not AccountKind.INVESTMENT:
                    self.issue(row, "GROSS_ONLY_FOR_INVESTMENTS", "gross_amount")
                else:
                    try:
                        validate_gross_balance(row.amount_cents, row.gross_cents)
                    except DomainError as error:
                        self.issue(row, error.code, "gross_amount")
            key = (account.id, row.date)
            if key in first:
                self.issue(row, "DUPLICATE_BALANCE", "date", first_line=first[key])
            else:
                first[key] = row.line
            if len(self.s.issues) > before:
                continue
            if key in self.ctx.anchors:
                self.s.warnings.append(FeedIssue("BALANCE_REPLACES_EXISTING", row.line, "date"))
            note = " · ".join(t for t in (row.description, row.notes) if t) or None
            actions.append(
                BalanceAction(
                    row.line, account.id, row.date, row.amount_cents, row.gross_cents, note
                )
            )
        return actions


def build_plan(rows: list[FeedRow], ctx: FeedContext) -> tuple[FeedPlan | None, list[FeedIssue]]:
    """The plan, or the list of issues (never both). Order of the actions: entries, transfers and
    card purchases in file order, then statement payments, then balances."""
    planner = _Planner(ctx)
    ordered: list[FeedAction] = []
    for row in rows:
        if row.kind in (FeedKind.EXPENSE, FeedKind.INCOME, FeedKind.REFUND):
            action = planner.entry(row)
        elif row.kind is FeedKind.TRANSFER:
            action = planner.transfer(row)
        else:
            continue
        if action is not None:
            ordered.append(action)
    ordered.extend(planner.resolve_payments())
    ordered.extend(planner.balances([r for r in rows if r.kind is FeedKind.BALANCE]))
    state = planner.s
    if state.issues:
        return None, sorted(state.issues, key=lambda i: i.line or 0)

    seen: set[FeedRow] = set()
    duplicates = 0
    for row in rows:
        key = replace(row, line=0)
        if row.kind is not FeedKind.BALANCE and key in seen:
            duplicates += 1
        seen.add(key)
    if duplicates:
        state.warnings.append(FeedIssue("DUPLICATE_ROWS", None, None, {"count": duplicates}))
    names = {a.id: a.nickname for a in ctx.accounts}
    for (card_id, month), touch in state.touches.items():
        stored = planner.stored.get((card_id, month))
        if touch.paid_cents:
            after = (stored.total_cents if stored else 0) + touch.owed_cents
            after -= (stored.paid_cents if stored else 0) + touch.paid_cents
            if after < 0:
                state.warnings.append(
                    FeedIssue(
                        "PAYMENT_ABOVE_OUTSTANDING",
                        None,
                        None,
                        {"account": names[card_id], "statement": str(month)},
                    )
                )
    by_kind: dict[FeedKind, int] = defaultdict(int)
    for row in rows:
        by_kind[row.kind] += 1
    plan = FeedPlan(
        actions=tuple(ordered),
        warnings=tuple(state.warnings),
        rows_by_kind=dict(by_kind),
        account_totals=tuple(AccountTotal(i, c, s) for i, (c, s) in state.totals.items()),
        statements=tuple(
            StatementTouch(
                t.account_id,
                t.month,
                t.closing_date,
                t.due_date,
                t.exists,
                t.entries,
                t.payments,
                t.owed_cents,
                t.paid_cents,
                t.signed_sum_cents,
            )
            for t in sorted(state.touches.values(), key=lambda t: (t.account_id, t.month))
        ),
        reasons=dict(state.reasons),
        duplicate_rows=duplicates,
    )
    return plan, []
