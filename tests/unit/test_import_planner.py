"""The import planner: rows of the old workbook in, a reviewable plan out (CLAUDE.md 13.1)."""

import datetime as dt

from financas.application.imports.model import (
    AccountSpec,
    EntryAction,
    Flag,
    ImportConfig,
    ImportPlan,
    IssueLevel,
    LegacyRow,
    Origin,
    PaymentAction,
    PlanAction,
    RowKind,
    TransferAction,
    TransferHint,
)
from financas.application.imports.planner import base_description, build_plan
from financas.domain.models import AccountKind
from financas.domain.money import YearMonth

D = dt.date
YM = YearMonth

ACCOUNTS = (
    AccountSpec("bb_cc", AccountKind.CHECKING, "Banco do Brasil", "BB c/c", "Banco do Brasil"),
    AccountSpec("inter_cc", AccountKind.CHECKING, "Inter", "Inter c/c", "Banco Inter"),
    AccountSpec(
        "bb_card",
        AccountKind.CREDIT_CARD,
        "Banco do Brasil",
        "BB cartão",
        "Banco do Brasil",
        due_day=5,
        closes_before_due=11,
    ),
    AccountSpec(
        "nu_card",
        AccountKind.CREDIT_CARD,
        "Nubank",
        "Nubank cartão",
        "Nubank",
        due_day=26,
        closes_before_due=7,
    ),
    AccountSpec(
        "inter_card",
        AccountKind.CREDIT_CARD,
        "Inter",
        "Inter cartão",
        "Banco Inter",
        due_day=20,
        closes_before_due=6,
    ),
    AccountSpec("rende", AccountKind.INVESTMENT, "Banco do Brasil", "BB Rende Fácil"),
)
CATEGORY_MAP = {
    "Alimentação": "food",
    "Compras": "shopping",
    "Receita": "other_income",
    "Salario": "salary",
    "Educacao": "education",
    "Estorno": "refund",
    "Transferencia": "transfer",
    "Não categorizado": "uncategorized",
    "Serviços": "services",
}
KINDS = {
    "food": "expense",
    "shopping": "expense",
    "other_income": "income",
    "salary": "income",
    "education": "expense",
    "refund": "neutral",
    "transfer": "neutral",
    "uncategorized": "expense",
    "services": "expense",
}


def config(**overrides: object) -> ImportConfig:
    values: dict[str, object] = {
        "year": 2026,
        "accounts": ACCOUNTS,
        "category_map": CATEGORY_MAP,
        "category_kinds": KINDS,
        "holder_aliases": ("fulano de tal",),
        "sweep_account_key": "rende",
    }
    values.update(overrides)
    return ImportConfig(**values)  # type: ignore[arg-type]


_counter = 0


def row(
    when: dt.date,
    amount: int,
    *,
    kind: RowKind = RowKind.EXPENSE,
    origin: Origin = Origin.CHECKING,
    institution: str = "Banco do Brasil",
    category: str = "Alimentação",
    description: str = "Mercado",
    statement: YearMonth | None = None,
    number: int | None = None,
    total: int | None = None,
    hint: TransferHint = TransferHint.NONE,
    counterparty: str = "",
    recurring: bool = False,
    sheet_id: str | None = None,
) -> LegacyRow:
    global _counter
    _counter += 1
    return LegacyRow(
        row_no=_counter + 1,
        sheet_id=sheet_id or f"id{_counter}",
        date=when,
        statement=statement,
        institution=institution,
        origin=origin,
        kind=kind,
        category=category,
        recurring=recurring,
        description=description,
        place=None,
        amount_cents=amount,
        installment_number=number,
        installment_total=total,
        transfer_hint=hint,
        counterparty=counterparty,
    )


def card_row(
    when: dt.date, amount: int, statement: YearMonth, institution: str = "Nubank", **kw: object
) -> LegacyRow:
    return row(
        when,
        amount,
        origin=Origin.CARD,
        institution=institution,
        statement=statement,
        **kw,  # type: ignore[arg-type]
    )


def actions_of(plan: ImportPlan, kind: type) -> list:  # type: ignore[type-arg]
    return [a for a in plan.actions if isinstance(a, kind)]


# --- scope ---------------------------------------------------------------------------------


def test_scope_is_the_year_by_competence() -> None:
    rows = [
        row(D(2025, 12, 31), -100),  # checking: by date -> out
        row(D(2026, 1, 1), -200),
        card_row(D(2025, 12, 19), -300, YM(2026, 1)),  # card: December purchase, January statement
        card_row(D(2025, 12, 1), -400, YM(2025, 12)),  # on the 2025 statement -> out
        card_row(D(2026, 12, 20), -500, YM(2027, 1)),  # on a 2027 statement -> out
    ]
    plan = build_plan(rows, config())
    assert (plan.scope_rows, plan.skipped_rows) == (2, 3)
    assert sorted(a.amount_cents for a in actions_of(plan, EntryAction)) == [200, 300]


def test_skipped_ids_are_left_out() -> None:
    keep, drop = row(D(2026, 3, 1), -100), row(D(2026, 3, 2), -200)
    plan = build_plan([keep, drop], config(skip_ids=frozenset({drop.sheet_id})))
    assert [a.sheet_id for a in actions_of(plan, EntryAction)] == [keep.sheet_id]


def test_unknown_account_is_an_error() -> None:
    plan = build_plan([row(D(2026, 3, 1), -100, institution="Banco Desconhecido")], config())
    assert [i.code for i in plan.errors] == ["UNKNOWN_ACCOUNT"]


# --- categories ----------------------------------------------------------------------------


def test_category_is_mapped_ignoring_case_and_accents() -> None:
    plan = build_plan([row(D(2026, 3, 1), -100, category="ALIMENTACAO")], config())
    (entry,) = actions_of(plan, EntryAction)
    assert entry.category_slug == "food" and entry.flags == ()


def test_the_kind_wins_when_it_disagrees_with_the_category() -> None:
    rows = [
        row(D(2026, 3, 1), 5_000, kind=RowKind.INCOME, category="Educacao"),
        row(D(2026, 3, 2), -700, kind=RowKind.EXPENSE, category="Receita"),
        row(D(2026, 3, 3), 900, kind=RowKind.REFUND, category="Compras"),
    ]
    plan = build_plan(rows, config())
    by_kind = {a.kind: a for a in actions_of(plan, EntryAction)}
    income, expense, refund = (
        by_kind[RowKind.INCOME],
        by_kind[RowKind.EXPENSE],
        by_kind[RowKind.REFUND],
    )
    assert (income.category_slug, income.original_category) == ("other_income", "Educacao")
    assert (expense.category_slug, expense.original_category) == ("uncategorized", "Receita")
    assert (refund.category_slug, refund.original_category) == ("refund", "Compras")
    assert all(Flag.KIND_CATEGORY_MISMATCH in a.flags for a in by_kind.values())


def test_unknown_category_stops_the_plan_and_an_override_fixes_it() -> None:
    odd = row(D(2026, 3, 1), -100, category="Categoria Nova")
    plan = build_plan([odd], config())
    assert [(i.code, i.detail) for i in plan.errors] == [("UNKNOWN_CATEGORY", "Categoria Nova")]
    fixed = build_plan([odd], config(category_overrides={odd.sheet_id: "services"}))
    assert fixed.errors == () and actions_of(fixed, EntryAction)[0].category_slug == "services"


def test_recurring_is_kept() -> None:
    plan = build_plan([row(D(2026, 3, 1), -100, recurring=True)], config())
    assert actions_of(plan, EntryAction)[0].recurring is True


# --- Pix ----------------------------------------------------------------------------------


def pix(when: dt.date, amount: int, who: str, institution: str = "Banco Inter") -> LegacyRow:
    return row(
        when,
        amount,
        kind=RowKind.TRANSFER,
        institution=institution,
        category="Transferencia",
        hint=TransferHint.PIX,
        counterparty=who,
        description=f"{who} | Pix",
    )


def test_third_party_pix_becomes_a_provisional_entry() -> None:
    plan = build_plan(
        [pix(D(2026, 3, 1), -2_500, "joao da silva"), pix(D(2026, 3, 2), 900, "maria")], config()
    )
    out, inc = sorted(actions_of(plan, EntryAction), key=lambda a: a.date)
    assert (out.kind, out.category_slug, out.amount_cents) == (
        RowKind.EXPENSE,
        "uncategorized",
        2_500,
    )
    assert (inc.kind, inc.category_slug) == (RowKind.INCOME, "other_income")
    assert Flag.PROVISIONAL in out.flags and Flag.PROVISIONAL in inc.flags
    assert {c.key: c.decision for c in plan.counterparties} == {
        "joao da silva": "third_party",
        "maria": "third_party",
    }


def test_the_decision_for_a_counterparty_can_be_a_category() -> None:
    rows = [pix(D(2026, 3, 1), -2_500, "padaria sao jose")]
    plan = build_plan(rows, config(counterparty_decisions={"padaria sao jose": "category:food"}))
    (entry,) = actions_of(plan, EntryAction)
    assert entry.category_slug == "food" and entry.flags == ()
    bad = build_plan(rows, config(counterparty_decisions={"padaria sao jose": "category:salary"}))
    assert [i.code for i in bad.errors] == ["BAD_DECISION"]


def test_own_pix_is_paired_into_one_transfer() -> None:
    out = pix(D(2026, 3, 1), -10_000, "fulano de tal", "Banco Inter")
    inc = pix(D(2026, 3, 2), 10_000, "fulano de tal", "Banco do Brasil")
    plan = build_plan([out, inc], config())
    (transfer,) = actions_of(plan, TransferAction)
    assert (transfer.from_key, transfer.to_key, transfer.amount_cents) == (
        "inter_cc",
        "bb_cc",
        10_000,
    )
    assert set(transfer.sheet_ids) == {out.sheet_id, inc.sheet_id} and transfer.flags == ()
    assert actions_of(plan, EntryAction) == []


def test_an_unmatched_own_pix_keeps_one_leg() -> None:
    plan = build_plan(
        [pix(D(2026, 3, 1), -10_000, "fulano de tal"), pix(D(2026, 5, 1), 500, "fulano de tal")],
        config(),
    )
    first, second = sorted(actions_of(plan, TransferAction), key=lambda a: a.date)
    assert (first.from_key, first.to_key) == ("inter_cc", None)
    assert (second.from_key, second.to_key) == (None, "inter_cc")
    assert all(Flag.UNMATCHED_OWN_TRANSFER in t.flags for t in (first, second))


def test_pairing_never_matches_the_same_account_or_far_dates() -> None:
    same_account = [
        pix(D(2026, 3, 1), -100, "fulano de tal"),
        pix(D(2026, 3, 1), 100, "fulano de tal"),
    ]
    assert len(actions_of(build_plan(same_account, config()), TransferAction)) == 2
    far = [
        pix(D(2026, 3, 1), -100, "fulano de tal", "Banco Inter"),
        pix(D(2026, 3, 20), 100, "fulano de tal", "Banco do Brasil"),
    ]
    assert len(actions_of(build_plan(far, config()), TransferAction)) == 2


def test_an_empty_counterparty_is_a_third_party() -> None:
    plan = build_plan([pix(D(2026, 3, 1), -100, "")], config())
    assert actions_of(plan, EntryAction)[0].flags == (Flag.PROVISIONAL,)


# --- the investment sweep -------------------------------------------------------------------


def sweep(when: dt.date, amount: int) -> LegacyRow:
    return row(
        when,
        amount,
        kind=RowKind.TRANSFER,
        category="Transferencia",
        hint=TransferHint.SWEEP,
        description="BB Rende Fácil",
    )


def test_sweep_movements_are_transfers_with_the_investment_account() -> None:
    plan = build_plan([sweep(D(2026, 3, 1), -5_000), sweep(D(2026, 3, 9), 3_000)], config())
    into, back = sorted(actions_of(plan, TransferAction), key=lambda a: a.date)
    assert (into.from_key, into.to_key, into.amount_cents) == ("bb_cc", "rende", 5_000)
    assert (back.from_key, back.to_key, back.amount_cents) == ("rende", "bb_cc", 3_000)


def test_sweep_without_an_account_is_an_error() -> None:
    plan = build_plan([sweep(D(2026, 3, 1), -5_000)], config(sweep_account_key=None))
    assert [i.code for i in plan.errors] == ["NO_SWEEP_ACCOUNT"]


# --- card entries ---------------------------------------------------------------------------


def test_card_entries_keep_the_sheets_statement_and_flag_disagreements() -> None:
    rows = [
        # Nubank closes on the 19th (due 26, 7 days before): a purchase on the 19th is on the next
        card_row(D(2026, 3, 19), -1_000, YM(2026, 4), "Nubank"),
        # the sheet put a BB purchase on the closing day (24 Sep, due Oct 5 - 11 days) on September
        card_row(D(2026, 9, 24), -2_000, YM(2026, 9), "Banco do Brasil"),
    ]
    plan = build_plan(rows, config())
    nubank, bb = actions_of(plan, EntryAction)
    assert nubank.statement == YM(2026, 4) and nubank.flags == ()
    assert bb.statement == YM(2026, 9) and bb.flags == (Flag.STATEMENT_DISAGREES,)


def test_a_card_refund_is_kept_as_a_refund() -> None:
    plan = build_plan(
        [card_row(D(2026, 3, 1), 4_000, YM(2026, 3), kind=RowKind.REFUND, category="Estorno")],
        config(),
    )
    (entry,) = actions_of(plan, EntryAction)
    assert (entry.kind, entry.category_slug, entry.amount_cents) == (
        RowKind.REFUND,
        "refund",
        4_000,
    )


# --- installment plans ----------------------------------------------------------------------


def inst(
    n: int, total: int, month: YearMonth, amount: int, desc: str = "Notebook", **kw: object
) -> LegacyRow:
    return card_row(
        D(month.year, month.month, 10),
        -amount,
        month,
        number=n,
        total=total,
        description=f"{desc} - Parcela {n}/{total}",
        category="Compras",
        **kw,  # type: ignore[arg-type]
    )


def test_base_description_drops_the_installment_suffix() -> None:
    assert base_description("Ebn *Loja - Parcela 4/6") == "Ebn *Loja"
    assert base_description("LOJA PARCELA 10 / 12") == "LOJA"
    assert base_description("Sem sufixo") == "Sem sufixo"


def test_a_running_purchase_generates_from_the_first_installment_in_the_sheet() -> None:
    rows = [inst(3, 6, YM(2026, 3), 9_679), inst(4, 6, YM(2026, 4), 9_679)]
    plan = build_plan(rows, config())
    (p,) = actions_of(plan, PlanAction)
    assert p.description == "Notebook" and p.category_slug == "shopping"
    assert (p.first_number, p.total_installments, p.first_statement) == (3, 6, YM(2026, 3))
    assert p.purchased_on is None and p.installment_cents == 9_679
    assert [(i.number, i.statement) for i in p.installments] == [
        (3, YM(2026, 3)),
        (4, YM(2026, 4)),
    ]
    assert p.flags == ()


def test_the_first_installment_gives_the_purchase_date() -> None:
    plan = build_plan([inst(1, 3, YM(2026, 2), 1_000)], config())
    (p,) = actions_of(plan, PlanAction)
    assert p.purchased_on == D(2026, 2, 10) and p.first_number == 1


def test_plans_are_never_grouped_by_amount() -> None:
    rows = [
        inst(2, 6, YM(2026, 3), 5_000, "Loja A"),
        inst(2, 6, YM(2026, 3), 5_000, "Loja B"),  # same amount, other purchase
        inst(2, 6, YM(2026, 3), 5_000, "Loja A", institution="Banco do Brasil"),  # other card
        inst(2, 6, YM(2026, 5), 5_000, "Loja A"),  # same text, other first statement
    ]
    plan = build_plan(rows, config())
    assert len(actions_of(plan, PlanAction)) == 4


def test_identical_purchases_become_separate_plans() -> None:
    rows = [inst(2, 6, YM(2026, 3), 5_000), inst(2, 6, YM(2026, 3), 5_000)]
    assert len(actions_of(build_plan(rows, config()), PlanAction)) == 2


def test_a_gap_between_present_installments_is_flagged() -> None:
    plan = build_plan([inst(2, 6, YM(2026, 3), 100), inst(4, 6, YM(2026, 5), 100)], config())
    (p,) = actions_of(plan, PlanAction)
    assert Flag.PLAN_GAP in p.flags


def test_plans_of_the_same_item_that_overlap_are_flagged() -> None:
    # installment 3 on a statement that does not fit the first one: another "first statement",
    # so the planner sees two purchases whose generated installments would collide
    first = inst(2, 6, YM(2026, 3), 100)
    shifted = inst(3, 6, YM(2026, 6), 100)
    plans = actions_of(build_plan([first, shifted], config()), PlanAction)
    assert len(plans) == 2
    assert all(Flag.PLAN_OVERLAP in p.flags for p in plans)
    far = [inst(1, 6, YM(2026, 1), 100), inst(1, 6, YM(2027, 1), 100)]  # a year apart: no overlap
    assert all(
        Flag.PLAN_OVERLAP not in p.flags for p in actions_of(build_plan(far, config()), PlanAction)
    )


def test_bad_installment_numbers_are_treated_as_single_entries() -> None:
    plan = build_plan([inst(9, 6, YM(2026, 3), 100)], config())
    assert actions_of(plan, PlanAction) == [] and len(actions_of(plan, EntryAction)) == 1


# --- statement payments ---------------------------------------------------------------------


def payment_card(
    when: dt.date, amount: int, month: YearMonth, institution: str = "Nubank"
) -> LegacyRow:
    return card_row(
        when,
        amount,
        month,
        institution,
        kind=RowKind.TRANSFER,
        category="Transferencia",
        hint=TransferHint.STATEMENT_PAYMENT,
        description="Pagamento recebido",
    )


def payment_checking(when: dt.date, amount: int, institution: str = "Banco Inter") -> LegacyRow:
    return row(
        when,
        -amount,
        kind=RowKind.TRANSFER,
        institution=institution,
        category="Transferencia",
        hint=TransferHint.STATEMENT_PAYMENT,
        description="Pagamento Fatura",
    )


def test_a_payment_is_matched_to_the_oldest_closed_statement_with_balance() -> None:
    rows = [
        card_row(D(2026, 1, 10), -10_000, YM(2026, 1)),
        card_row(D(2026, 2, 10), -5_000, YM(2026, 2)),
        payment_card(D(2026, 2, 26), 10_000, YM(2026, 3)),
        payment_checking(D(2026, 2, 26), 10_000),
    ]
    plan = build_plan(rows, config())
    (pay,) = actions_of(plan, PaymentAction)
    assert (pay.card_key, pay.from_key, pay.amount_cents) == ("nu_card", "inter_cc", 10_000)
    assert pay.statement == YM(2026, 1) and pay.flags == ()
    assert len(pay.sheet_ids) == 2


def test_a_payment_before_the_closing_does_not_pay_that_statement() -> None:
    rows = [
        card_row(D(2026, 2, 10), -5_000, YM(2026, 2)),  # Nubank closes on 2026-02-19
        payment_checking(D(2026, 2, 12), 5_000, "Nubank"),  # before the closing
    ]
    plan = build_plan(rows, config())
    assert actions_of(plan, PaymentAction) == []


def test_a_payment_of_a_statement_outside_the_scope_is_a_single_leg() -> None:
    rows = [
        payment_card(D(2026, 1, 26), 7_000, YM(2026, 2)),
        payment_checking(D(2026, 1, 26), 7_000),
    ]
    plan = build_plan(rows, config())
    (transfer,) = actions_of(plan, TransferAction)
    assert (transfer.from_key, transfer.to_key, transfer.amount_cents) == ("inter_cc", None, 7_000)
    assert Flag.UNMATCHED_PAYMENT in transfer.flags and actions_of(plan, PaymentAction) == []


def test_an_unmatched_checking_payment_goes_to_the_card_of_the_same_bank() -> None:
    rows = [
        card_row(D(2026, 2, 10), -3_000, YM(2026, 2), "Banco do Brasil"),
        payment_checking(D(2026, 3, 5), 3_000, "Banco do Brasil"),
    ]
    plan = build_plan(rows, config())
    (pay,) = actions_of(plan, PaymentAction)
    assert (pay.card_key, pay.from_key, pay.statement) == ("bb_card", "bb_cc", YM(2026, 2))


def test_a_card_side_payment_alone_keeps_only_the_card_leg() -> None:
    rows = [
        card_row(D(2026, 1, 10), -6_000, YM(2026, 1)),
        payment_card(D(2026, 2, 26), 6_000, YM(2026, 3)),
    ]
    (pay,) = actions_of(build_plan(rows, config()), PaymentAction)
    assert pay.from_key is None and pay.statement == YM(2026, 1)


def test_a_payment_above_the_balance_is_flagged() -> None:
    rows = [
        card_row(D(2026, 1, 10), -6_000, YM(2026, 1)),
        payment_card(D(2026, 2, 26), 9_000, YM(2026, 3)),
        payment_checking(D(2026, 2, 26), 9_000),
    ]
    (pay,) = actions_of(build_plan(rows, config()), PaymentAction)
    assert Flag.PAYMENT_ABOVE_OUTSTANDING in pay.flags


def test_generated_installments_count_in_the_statement_balances() -> None:
    rows = [
        inst(1, 3, YM(2026, 1), 4_000),  # installments 2 and 3 are generated (Feb, Mar)
        payment_checking(D(2026, 3, 1), 4_000),
        payment_card(D(2026, 3, 1), 4_000, YM(2026, 3)),
    ]
    plan = build_plan(rows, config())
    (pay,) = actions_of(plan, PaymentAction)
    assert pay.statement == YM(2026, 1)


def test_actions_come_out_in_date_order_and_issues_are_empty_for_clean_data() -> None:
    plan = build_plan([row(D(2026, 5, 1), -1), row(D(2026, 3, 1), -2)], config())
    dates = [a.date for a in plan.actions if isinstance(a, EntryAction)]
    assert dates == sorted(dates)
    assert all(i.level is not IssueLevel.ERROR for i in plan.issues)
