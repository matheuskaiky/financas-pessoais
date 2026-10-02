"""Turns the rows of the old workbook into a reviewable plan (CLAUDE.md 13.1). Pure functions.

Nothing is written anywhere: the plan lists what *would* be created, with a flag on everything
the user should look at. The rules (decided with the user, ``docs/IMPORT_PLAN_2026.md``):

* scope is the year by competence: checking rows by their date, card rows by their statement month;
* the kind decides the category when they disagree; outgoing Pix are classified by counterparty;
* statement payments and own-account transfers are matched in pairs (matching only confirms);
* installments are grouped by (card, base description, total, first statement), never by amount.
"""

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import replace
from itertools import pairwise

from financas.application.imports.model import (
    AccountSpec,
    CounterpartyDecision,
    CounterpartyLine,
    EntryAction,
    Flag,
    ImportConfig,
    ImportPlan,
    Issue,
    IssueLevel,
    LegacyRow,
    Origin,
    PaymentAction,
    PlanAction,
    PlanInstallment,
    PlannedAction,
    RowKind,
    TransferAction,
    TransferHint,
)
from financas.domain.models import AccountKind
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import assign_statement, statement_dates
from financas.domain.services.text import normalize_search

PAYMENT_WINDOW_DAYS = 5  # a card payment and the checking debit may be dated a few days apart
DUE_WINDOW_DAYS = 12  # a payment belongs to the statement due closest to it, within this many days
OWN_TRANSFER_WINDOW_DAYS = 3
_INSTALLMENT_SUFFIX = re.compile(r"\s*[-–]?\s*parcela\s+\d+\s*/\s*\d+\s*$", re.IGNORECASE)
_EXPECTED_CATEGORY_KIND = {
    RowKind.EXPENSE: "expense",
    RowKind.INCOME: "income",
    RowKind.REFUND: "neutral",
}
_MISMATCH_FALLBACK = {
    RowKind.EXPENSE: "uncategorized",
    RowKind.INCOME: "other_income",
    RowKind.REFUND: "refund",
}


def base_description(description: str) -> str:
    """The description without the trailing ``- Parcela n/N`` the issuer prints."""
    return _INSTALLMENT_SUFFIX.sub("", description).strip()


class _Planner:
    def __init__(self, rows: Sequence[LegacyRow], config: ImportConfig) -> None:
        self.rows = rows
        self.config = config
        self.issues: list[Issue] = []
        self.specs = {a.key: a for a in config.accounts}
        self.by_legacy: dict[tuple[str, Origin], AccountSpec] = {}
        for spec in config.accounts:
            if spec.legacy_institution is None:
                continue
            origin = Origin.CARD if spec.kind is AccountKind.CREDIT_CARD else Origin.CHECKING
            if spec.kind is not AccountKind.INVESTMENT:
                self.by_legacy[(normalize_search(spec.legacy_institution), origin)] = spec
        self.category_map = {normalize_search(k): v for k, v in config.category_map.items()}
        self.aliases = tuple(a for a in config.holder_aliases if a)
        self.categories_used: Counter[str] = Counter()
        self.counterparty_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        self.counterparty_decision: dict[str, str] = {}

    # ---- helpers -----------------------------------------------------------------------------

    def issue(
        self, level: IssueLevel, code: str, sheet_id: str | None = None, detail: str = ""
    ) -> None:
        self.issues.append(Issue(level, code, sheet_id, detail))

    def account_for(self, row: LegacyRow) -> AccountSpec | None:
        spec = self.by_legacy.get((normalize_search(row.institution), row.origin))
        if spec is None:
            self.issue(
                IssueLevel.ERROR, "UNKNOWN_ACCOUNT", row.sheet_id, f"{row.institution}/{row.origin}"
            )
        return spec

    def in_scope(self, row: LegacyRow) -> bool:
        if row.origin is Origin.CARD:
            if row.statement is None:
                self.issue(IssueLevel.WARNING, "NO_STATEMENT", row.sheet_id)
                return row.date.year == self.config.year
            return row.statement.year == self.config.year
        return row.date.year == self.config.year

    def resolve_category(self, row: LegacyRow) -> tuple[str, str | None, list[Flag]]:
        """``(slug, original category when replaced, flags)``; the kind decides on a mismatch."""
        override = self.config.category_overrides.get(row.sheet_id)
        slug = override or self.category_map.get(normalize_search(row.category))
        if slug is None or slug not in self.config.category_kinds:
            self.issue(IssueLevel.ERROR, "UNKNOWN_CATEGORY", row.sheet_id, row.category)
            return _MISMATCH_FALLBACK.get(row.kind, "uncategorized"), None, []
        if self.config.category_kinds[slug] != _EXPECTED_CATEGORY_KIND[row.kind]:
            return (
                _MISMATCH_FALLBACK[row.kind],
                row.category,
                [Flag.KIND_CATEGORY_MISMATCH],
            )
        return slug, None, []

    def statement_flags(self, row: LegacyRow, spec: AccountSpec) -> list[Flag]:
        """Does the sheet's statement differ from what the owner's closing rule gives?"""
        if (
            row.statement is None
            or spec.due_day is None
            or spec.closes_before_due is None
            or row.installment_total not in (None, 1)
        ):
            return []
        rule = assign_statement(row.date, spec.due_day, spec.closes_before_due).month
        return [Flag.STATEMENT_DISAGREES] if rule != row.statement else []

    # ---- main --------------------------------------------------------------------------------

    def run(self) -> ImportPlan:
        ordered = sorted(self.rows, key=lambda r: (r.date, r.row_no))
        scoped = [r for r in ordered if r.sheet_id not in self.config.skip_ids and self.in_scope(r)]
        skipped = len(ordered) - len(scoped)
        for row in scoped:
            self.categories_used[row.category] += 1

        actions: list[PlannedAction] = []
        installment_rows: list[LegacyRow] = []
        payment_rows: list[LegacyRow] = []
        own_rows: list[LegacyRow] = []
        for row in scoped:
            if row.kind is RowKind.TRANSFER:
                if row.origin is Origin.CARD or row.transfer_hint is TransferHint.STATEMENT_PAYMENT:
                    payment_rows.append(row)
                elif row.transfer_hint is TransferHint.SWEEP:
                    self.sweep(row, actions)
                else:
                    self.classify_pix(row, actions, own_rows)
            elif (
                row.origin is Origin.CARD
                and row.kind is RowKind.EXPENSE
                and (row.installment_total or 1) > 1
                and row.installment_number is not None
                and 1 <= row.installment_number <= (row.installment_total or 1)
            ):
                installment_rows.append(row)
            else:
                self.simple_entry(row, actions)
        actions.extend(self.pair_own_transfers(own_rows))
        actions.extend(self.build_plans(installment_rows))
        actions = self.resolve_payments(payment_rows, actions)
        actions.sort(key=lambda a: _action_date(a))
        return ImportPlan(
            year=self.config.year,
            accounts=self.config.accounts,
            actions=tuple(actions),
            counterparties=self.counterparty_lines(),
            categories_used=dict(self.categories_used),
            issues=tuple(self.issues),
            scope_rows=len(scoped),
            skipped_rows=skipped,
        )

    # ---- simple entries ----------------------------------------------------------------------

    def simple_entry(self, row: LegacyRow, actions: list[PlannedAction]) -> None:
        spec = self.account_for(row)
        if spec is None:
            return
        slug, original, flags = self.resolve_category(row)
        if row.origin is Origin.CARD:
            flags += self.statement_flags(row, spec)
        actions.append(
            EntryAction(
                sheet_id=row.sheet_id,
                date=row.date,
                account_key=spec.key,
                kind=row.kind,
                amount_cents=abs(row.amount_cents),
                description=row.description,
                category_slug=slug,
                recurring=row.recurring,
                place=row.place,
                statement=row.statement if row.origin is Origin.CARD else None,
                original_category=original,
                flags=tuple(flags),
            )
        )

    # ---- Pix and other transfers on checking accounts ------------------------------------------

    def suggestion_for(self, key: str) -> str:
        if key and any(alias in key for alias in self.aliases):
            return CounterpartyDecision.OWN.value
        return CounterpartyDecision.THIRD_PARTY.value

    def decision_for(self, key: str) -> str:
        return self.config.counterparty_decisions.get(key) or self.suggestion_for(key)

    def classify_pix(
        self, row: LegacyRow, actions: list[PlannedAction], own_rows: list[LegacyRow]
    ) -> None:
        spec = self.account_for(row)
        if spec is None:
            return
        key = row.counterparty
        decision = self.decision_for(key)
        self.counterparty_decision[key] = decision
        stats = self.counterparty_stats[key]
        stats[0] += 1
        stats[1 if row.amount_cents < 0 else 2] += abs(row.amount_cents)
        if decision == CounterpartyDecision.OWN.value:
            own_rows.append(row)
            return
        positive = row.amount_cents > 0
        kind = RowKind.INCOME if positive else RowKind.EXPENSE
        flags: list[Flag] = []
        if decision == CounterpartyDecision.THIRD_PARTY.value:
            slug = "other_income" if positive else "uncategorized"
            flags.append(Flag.PROVISIONAL)
        else:
            slug = decision.removeprefix("category:")
            expected = "income" if positive else "expense"
            if self.config.category_kinds.get(slug) != expected:
                self.issue(IssueLevel.ERROR, "BAD_DECISION", row.sheet_id, decision)
                return
        actions.append(
            EntryAction(
                sheet_id=row.sheet_id,
                date=row.date,
                account_key=spec.key,
                kind=kind,
                amount_cents=abs(row.amount_cents),
                description=row.description,
                category_slug=slug,
                recurring=row.recurring,
                place=row.place,
                flags=tuple(flags),
            )
        )

    def pair_own_transfers(self, own_rows: list[LegacyRow]) -> list[PlannedAction]:
        """Own-account Pix: an outgoing and an incoming row of the same amount are one transfer."""
        result: list[PlannedAction] = []
        outgoing = [r for r in own_rows if r.amount_cents < 0]
        incoming = [r for r in own_rows if r.amount_cents > 0]
        used: set[str] = set()
        for out in outgoing:
            out_spec = self.account_for(out)
            match = next(
                (
                    i
                    for i in incoming
                    if i.sheet_id not in used
                    and i.amount_cents == -out.amount_cents
                    and abs((i.date - out.date).days) <= OWN_TRANSFER_WINDOW_DAYS
                    and (spec := self.account_for(i)) is not None
                    and out_spec is not None
                    and spec.key != out_spec.key
                ),
                None,
            )
            if out_spec is None:
                continue
            if match is not None:
                used.add(match.sheet_id)
                to_spec = self.account_for(match)
                result.append(
                    TransferAction(
                        (out.sheet_id, match.sheet_id),
                        out.date,
                        out_spec.key,
                        to_spec.key if to_spec else None,
                        abs(out.amount_cents),
                        out.description,
                    )
                )
            else:
                result.append(
                    TransferAction(
                        (out.sheet_id,),
                        out.date,
                        out_spec.key,
                        None,
                        abs(out.amount_cents),
                        out.description,
                        (Flag.UNMATCHED_OWN_TRANSFER,),
                    )
                )
        for inc in incoming:
            if inc.sheet_id in used:
                continue
            spec = self.account_for(inc)
            if spec is None:
                continue
            result.append(
                TransferAction(
                    (inc.sheet_id,),
                    inc.date,
                    None,
                    spec.key,
                    inc.amount_cents,
                    inc.description,
                    (Flag.UNMATCHED_OWN_TRANSFER,),
                )
            )
        return result

    def sweep(self, row: LegacyRow, actions: list[PlannedAction]) -> None:
        spec = self.account_for(row)
        target = self.config.sweep_account_key
        if target is None or target not in self.specs:
            self.issue(IssueLevel.ERROR, "NO_SWEEP_ACCOUNT", row.sheet_id)
            return
        if spec is None:
            return
        outgoing = row.amount_cents < 0
        actions.append(
            TransferAction(
                (row.sheet_id,),
                row.date,
                spec.key if outgoing else target,
                target if outgoing else spec.key,
                abs(row.amount_cents),
                row.description,
            )
        )

    # ---- installment plans -------------------------------------------------------------------

    def build_plans(self, rows: list[LegacyRow]) -> list[PlannedAction]:
        groups: dict[tuple[str, str, int, YearMonth], list[LegacyRow]] = defaultdict(list)
        for row in rows:
            spec = self.account_for(row)
            if spec is None or row.statement is None or row.installment_number is None:
                continue
            first = row.statement.add_months(-(row.installment_number - 1))
            key = (
                spec.key,
                normalize_search(base_description(row.description)),
                row.installment_total or 1,
                first,
            )
            groups[key].append(row)
        plans: list[PlannedAction] = []
        for (account_key, _, total, first), members in groups.items():
            members.sort(key=lambda r: (r.installment_number or 0, r.date, r.row_no))
            layers: list[dict[int, LegacyRow]] = []
            for row in members:  # two identical purchases: the i-th copy of each number
                number = row.installment_number or 0
                for layer in layers:
                    if number not in layer:
                        layer[number] = row
                        break
                else:
                    layers.append({number: row})
            for layer in layers:
                plans.append(self.plan_from_layer(account_key, total, first, layer))
        return self.flag_overlaps(plans)

    def flag_overlaps(self, plans: list[PlannedAction]) -> list[PlannedAction]:
        """Plans of the same item that would generate the same statements: a double count risk."""
        by_item: dict[tuple[str, str, int], list[int]] = defaultdict(list)
        for index, plan in enumerate(plans):
            assert isinstance(plan, PlanAction)
            key = (plan.account_key, normalize_search(plan.description), plan.total_installments)
            by_item[key].append(index)
        flagged: set[int] = set()
        for indexes in by_item.values():
            for i, a in enumerate(indexes):
                for b in indexes[i + 1 :]:
                    if _statements_covered(plans[a]) & _statements_covered(plans[b]):  # type: ignore[arg-type]
                        flagged.update((a, b))
        result: list[PlannedAction] = []
        for index, plan in enumerate(plans):
            assert isinstance(plan, PlanAction)
            if index in flagged:
                plan = replace(plan, flags=tuple(dict.fromkeys((*plan.flags, Flag.PLAN_OVERLAP))))
            result.append(plan)
        return result

    def plan_from_layer(
        self, account_key: str, total: int, first: YearMonth, layer: dict[int, LegacyRow]
    ) -> PlanAction:
        numbers = sorted(layer)
        head = layer[numbers[0]]
        slug, original, flags = self.resolve_category(head)
        present: list[PlanInstallment] = []
        for n in numbers:
            row = layer[n]
            assert row.statement is not None
            present.append(PlanInstallment(n, row.sheet_id, abs(row.amount_cents), row.statement))
        if any(b - a > 1 for a, b in pairwise(numbers)):
            flags.append(Flag.PLAN_GAP)
        head_statement = head.statement
        assert head_statement is not None
        return PlanAction(
            account_key=account_key,
            description=base_description(head.description),
            category_slug=slug,
            recurring=any(r.recurring for r in layer.values()),
            total_installments=total,
            first_number=numbers[0],
            purchased_on=layer[1].date if 1 in layer else None,
            first_statement=head_statement,
            installment_cents=abs(layer[numbers[-1]].amount_cents),
            installments=tuple(present),
            original_category=original,
            flags=tuple(dict.fromkeys(flags)),
        )

    # ---- statement payments ------------------------------------------------------------------

    def card_for_checking(self, row: LegacyRow) -> AccountSpec | None:
        same_bank = [
            s
            for s in self.specs.values()
            if s.kind is AccountKind.CREDIT_CARD
            and s.legacy_institution is not None
            and normalize_search(s.legacy_institution) == normalize_search(row.institution)
        ]
        return same_bank[0] if len(same_bank) == 1 else None

    def statement_outstanding(
        self, actions: list[PlannedAction]
    ) -> dict[tuple[str, YearMonth], int]:
        totals: dict[tuple[str, YearMonth], int] = defaultdict(int)
        for a in actions:
            if isinstance(a, EntryAction) and a.statement is not None:
                sign = 1 if a.kind is RowKind.EXPENSE else -1
                totals[(a.account_key, a.statement)] += sign * a.amount_cents
            elif isinstance(a, PlanAction):
                present = {i.number: i for i in a.installments}
                for n in range(a.first_number, a.total_installments + 1):
                    if n in present:
                        amount, month = present[n].amount_cents, present[n].statement
                    else:
                        amount = a.installment_cents
                        month = a.first_statement.add_months(n - a.first_number)
                    totals[(a.account_key, month)] += amount
        return totals

    def resolve_payments(
        self, rows: list[LegacyRow], actions: list[PlannedAction]
    ) -> list[PlannedAction]:
        card_side = [r for r in rows if r.origin is Origin.CARD]
        checking_side = [r for r in rows if r.origin is Origin.CHECKING]
        pairs: list[
            tuple[AccountSpec | None, AccountSpec | None, int, dt.date, tuple[str, ...]]
        ] = []
        used: set[str] = set()
        for card_row in card_side:
            card_spec = self.account_for(card_row)
            match = next(
                (
                    c
                    for c in checking_side
                    if c.sheet_id not in used
                    and -c.amount_cents == card_row.amount_cents
                    and abs((c.date - card_row.date).days) <= PAYMENT_WINDOW_DAYS
                ),
                None,
            )
            if match is not None:
                used.add(match.sheet_id)
                pairs.append(
                    (
                        card_spec,
                        self.account_for(match),
                        card_row.amount_cents,
                        match.date,
                        (card_row.sheet_id, match.sheet_id),
                    )
                )
            else:
                pairs.append(
                    (card_spec, None, card_row.amount_cents, card_row.date, (card_row.sheet_id,))
                )
        result = list(actions)
        for check_row in checking_side:
            if check_row.sheet_id in used:
                continue
            card_spec = self.card_for_checking(check_row)
            if card_spec is None:
                self.issue(IssueLevel.WARNING, "NO_CARD_FOR_PAYMENT", check_row.sheet_id)
                spec = self.account_for(check_row)
                if spec is not None:
                    result.append(
                        TransferAction(
                            (check_row.sheet_id,),
                            check_row.date,
                            spec.key,
                            None,
                            abs(check_row.amount_cents),
                            check_row.description,
                            (Flag.NO_CARD_FOR_PAYMENT,),
                        )
                    )
                continue
            pairs.append(
                (
                    card_spec,
                    self.account_for(check_row),
                    abs(check_row.amount_cents),
                    check_row.date,
                    (check_row.sheet_id,),
                )
            )
        outstanding = self.statement_outstanding(result)
        for card_spec, from_spec, amount, date, ids in sorted(pairs, key=lambda p: p[3]):
            if card_spec is None:
                continue
            statement, flags = self.pick_statement(card_spec, date, amount, outstanding)
            if statement is None:  # pays a statement that is outside the scope (e.g. last year's)
                if from_spec is not None:
                    result.append(
                        TransferAction(
                            ids, date, from_spec.key, None, amount, "", (Flag.UNMATCHED_PAYMENT,)
                        )
                    )
                else:
                    self.issue(IssueLevel.WARNING, "PAYMENT_OUT_OF_SCOPE", ids[0])
                continue
            result.append(
                PaymentAction(
                    ids, date, card_spec.key, from_spec.key if from_spec else None, amount,
                    statement, tuple(flags),
                )
            )  # fmt: skip
        return result

    def pick_statement(
        self,
        card: AccountSpec,
        date: dt.date,
        amount: int,
        outstanding: dict[tuple[str, YearMonth], int],
    ) -> tuple[YearMonth | None, list[Flag]]:
        """The statement whose due date is closest to the payment (at most 12 days away).

        A payment of exactly what is outstanding on a statement wins a tie of distance; partial
        payments are normal, a payment above the balance is flagged.
        """
        if card.due_day is None or card.closes_before_due is None:
            return None, []
        candidates: list[tuple[int, int, YearMonth]] = []
        for (key, month), owed in outstanding.items():
            if key != card.key:
                continue
            _, due = statement_dates(month, card.due_day, card.closes_before_due)
            distance = abs((due - date).days)
            if distance <= DUE_WINDOW_DAYS:
                candidates.append((distance, 0 if owed == amount else 1, month))
        if not candidates:
            return None, []
        best = min(candidates)
        month = best[2]
        owed = outstanding[(card.key, month)]
        flags = [Flag.PAYMENT_ABOVE_OUTSTANDING] if amount > owed else []
        outstanding[(card.key, month)] = owed - amount
        return month, flags

    # ---- review lines ------------------------------------------------------------------------

    def counterparty_lines(self) -> tuple[CounterpartyLine, ...]:
        lines = [
            CounterpartyLine(
                key,
                count,
                out,
                inc,
                self.counterparty_decision.get(key, ""),
                self.suggestion_for(key),
            )
            for key, (count, out, inc) in self.counterparty_stats.items()
        ]
        lines.sort(key=lambda c: (-c.count, c.key))
        return tuple(lines)


def installment_amounts(plan: PlanAction) -> dict[int, int]:
    """Every installment the plan will have (sheet amounts and generated ones), by number."""
    present = {i.number: i.amount_cents for i in plan.installments}
    return {
        n: present.get(n, plan.installment_cents)
        for n in range(plan.first_number, plan.total_installments + 1)
    }


def account_totals(plan: ImportPlan) -> dict[str, int]:
    """Net amount the plan puts on each account (cards: purchases negative, payments positive)."""
    totals: Counter[str] = Counter()
    for a in plan.actions:
        if isinstance(a, EntryAction):
            sign = -1 if a.kind is RowKind.EXPENSE else 1
            totals[a.account_key] += sign * a.amount_cents
        elif isinstance(a, TransferAction):
            if a.from_key:
                totals[a.from_key] -= a.amount_cents
            if a.to_key:
                totals[a.to_key] += a.amount_cents
        elif isinstance(a, PaymentAction):
            if a.from_key:
                totals[a.from_key] -= a.amount_cents
            totals[a.card_key] += a.amount_cents
        else:
            totals[a.account_key] -= sum(installment_amounts(a).values())
    return dict(totals)


def expected_counts(plan: ImportPlan) -> dict[str, int]:
    """How many transactions the plan puts on each account."""
    counts: Counter[str] = Counter()
    for a in plan.actions:
        if isinstance(a, EntryAction):
            counts[a.account_key] += 1
        elif isinstance(a, TransferAction):
            counts.update(k for k in (a.from_key, a.to_key) if k)
        elif isinstance(a, PaymentAction):
            counts.update(k for k in (a.from_key, a.card_key) if k)
        else:
            counts[a.account_key] += len(installment_amounts(a))
    return dict(counts)


def _statements_covered(plan: PlanAction) -> set[YearMonth]:
    return {
        plan.first_statement.add_months(n - plan.first_number)
        for n in range(plan.first_number, plan.total_installments + 1)
    }


def _action_date(action: PlannedAction) -> dt.date:
    if isinstance(action, PlanAction):
        return action.purchased_on or action.first_statement.day(1)
    return action.date


def build_plan(rows: Sequence[LegacyRow], config: ImportConfig) -> ImportPlan:
    """The whole plan for ``config.year``; see the module docstring for the rules."""
    return _Planner(rows, config).run()
