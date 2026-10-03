"""Validation per kind and per business rule, statement assignment and installment planning."""

import dataclasses
import datetime as dt

import pytest

from feed_support import TODAY, paid_statement, synthetic_context
from financas.application.csvfeed import FeedAnalysis, analyze_feed
from financas.application.csvfeed.model import (
    AccountInfo,
    BalanceAction,
    CategoryInfo,
    EntryAction,
    FeedContext,
    PaymentAction,
    PurchaseAction,
    StatementInfo,
    TransferAction,
)
from financas.domain.models import AccountKind, CategoryKind, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import AssignmentReason

HEADER = (
    "date;kind;account;to_account;amount;description;category;recurring;notes;statement;"
    "installments;installment_number;amount_type;gross_amount"
)
FIELDS = HEADER.split(";")


def row(**f: str) -> str:
    unknown = set(f) - set(FIELDS)
    assert not unknown, unknown
    return ";".join(f.get(name, "") for name in FIELDS)


def feed(*rows: str, ctx: FeedContext | None = None) -> FeedAnalysis:
    text = "\n".join([HEADER, *rows]) + "\n"
    return analyze_feed(text.encode(), ctx or synthetic_context())


def errors(*rows: str, ctx: FeedContext | None = None) -> list[tuple[str, int | None, str | None]]:
    analysis = feed(*rows, ctx=ctx)
    assert analysis.plan is None
    return [(i.code, i.line, i.column) for i in analysis.issues]


def one(*rows: str, ctx: FeedContext | None = None):  # type: ignore[no-untyped-def]
    analysis = feed(*rows, ctx=ctx)
    assert analysis.ok, analysis.issues
    assert analysis.plan is not None
    return analysis.plan


def exp(**f: str) -> str:
    base = {"date": "2026-09-10", "kind": "expense", "account": "Conta Corrente",
            "amount": "10,00", "description": "Pão"}  # fmt: skip
    return row(**{**base, **f})


def card(**f: str) -> str:
    return exp(**{"account": "Cartão Exemplo", **f})


# ---- row validation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "code", "column"),
    [
        ({"date": ""}, "REQUIRED_FIELD", "date"),
        ({"account": ""}, "REQUIRED_FIELD", "account"),
        ({"amount": ""}, "REQUIRED_FIELD", "amount"),
        ({"description": ""}, "REQUIRED_FIELD", "description"),
        ({"kind": ""}, "REQUIRED_FIELD", "kind"),
        ({"kind": "gasto"}, "INVALID_CHOICE", "kind"),
        ({"date": "10/09/26"}, "INVALID_DATE", "date"),
        ({"date": "2030-01-01"}, "DATE_TOO_FAR", "date"),
        ({"amount": "1.234"}, "AMOUNT_AMBIGUOUS", "amount"),
        ({"amount": "1,23.45"}, "INVALID_AMOUNT", "amount"),
        ({"amount": "0"}, "AMOUNT_NOT_POSITIVE", "amount"),
        ({"amount": "+5"}, "SIGN_KIND_MISMATCH", "amount"),
        ({"to_account": "Conta Reserva"}, "NOT_ALLOWED_FOR_KIND", "to_account"),
        ({"gross_amount": "5"}, "NOT_ALLOWED_FOR_KIND", "gross_amount"),
        ({"recurring": "talvez"}, "INVALID_BOOLEAN", "recurring"),
        ({"statement": "09/2026"}, "INVALID_YEAR_MONTH", "statement"),
        ({"installments": "0"}, "INVALID_INSTALLMENT_COUNT", "installments"),
        ({"installments": "121"}, "INVALID_INSTALLMENT_COUNT", "installments"),
        ({"installments": "x"}, "INVALID_NUMBER", "installments"),
        ({"installments": "3", "installment_number": "4"}, "INSTALLMENT_OUT_OF_RANGE",
         "installment_number"),
        ({"installments": "3", "amount_type": "tudo"}, "INVALID_CHOICE", "amount_type"),
        ({"installment_number": "2"}, "INSTALLMENT_DETAILS_WITHOUT_COUNT", "installment_number"),
        ({"amount_type": "total"}, "INSTALLMENT_DETAILS_WITHOUT_COUNT", "amount_type"),
        ({"installments": "1", "amount_type": "total"}, "INSTALLMENT_DETAILS_WITHOUT_COUNT",
         "amount_type"),
        ({"installments": "3", "recurring": "yes"}, "INSTALLMENT_NOT_RECURRING", "recurring"),
        ({"description": "x" * 301}, "FIELD_TOO_LONG", "description"),
        ({"notes": "x" * 2001}, "FIELD_TOO_LONG", "notes"),
    ],
)  # fmt: skip
def test_row_errors_carry_code_line_and_column(
    fields: dict[str, str], code: str, column: str
) -> None:
    ok = exp(description="ok")
    assert errors(ok, exp(**fields)) == [(code, 3, column)]


def test_every_problem_of_every_row_is_collected_and_nothing_is_planned() -> None:
    found = errors(exp(amount="x", date=""), exp(), exp(description="", kind="income", amount="-1"))
    assert found == [
        ("REQUIRED_FIELD", 2, "date"),
        ("INVALID_AMOUNT", 2, "amount"),
        ("REQUIRED_FIELD", 4, "description"),
        ("SIGN_KIND_MISMATCH", 4, "amount"),
    ]


@pytest.mark.parametrize(
    ("kind", "amount", "ok"),
    [
        ("expense", "10", True), ("expense", "-10", True), ("expense", "+10", False),
        ("income", "10", True), ("income", "+10", True), ("income", "-10", False),
        ("refund", "10", True), ("refund", "+10", True), ("refund", "-10", False),
        ("transfer", "10", True), ("transfer", "+10", False), ("transfer", "-10", False),
        ("balance", "10", True), ("balance", "-10", True), ("balance", "0", True),
    ],
)  # fmt: skip
def test_signs_per_kind(kind: str, amount: str, ok: bool) -> None:
    fields = {"kind": kind, "amount": amount}
    if kind == "transfer":
        fields |= {"account": "Conta Corrente", "to_account": "Conta Reserva"}
    if kind == "income":
        fields["description"] = "Salário"
    analysis = feed(exp(**fields))
    assert analysis.ok is ok
    if not ok:
        assert analysis.issues[0].code == "SIGN_KIND_MISMATCH"


def test_description_is_required_only_for_entries() -> None:
    plan = one(
        row(date="2026-09-01", kind="transfer", account="Conta Corrente",
            to_account="Conta Reserva", amount="1"),
        row(date="2026-09-01", kind="balance", account="Conta Corrente", amount="1"),
    )  # fmt: skip
    assert plan.rows_by_kind
    assert errors(exp(kind="refund", description=""))[0][0] == "REQUIRED_FIELD"


def test_refund_cannot_be_an_installment_and_income_takes_no_statement() -> None:
    assert errors(exp(kind="refund", installments="3")) == [
        ("REFUND_NOT_INSTALLMENT", 2, "installments")
    ]
    assert errors(exp(kind="income", statement="2026-09")) == [
        ("NOT_ALLOWED_FOR_KIND", 2, "statement")
    ]


def test_recurring_flag_is_refused_on_transfers_and_balances_only_when_true() -> None:
    base = {"account": "Conta Corrente", "to_account": "Conta Reserva", "kind": "transfer"}
    assert feed(exp(**base, recurring="no")).ok
    assert errors(exp(**base, recurring="sim")) == [("NOT_ALLOWED_FOR_KIND", 2, "recurring")]


# ---- accounts and categories -------------------------------------------------------------------


def test_names_match_without_accents_or_case_and_text_is_kept_intact() -> None:
    plan = one(
        exp(
            account="  conta   corrente ", category="SUPERMERCADO", description="  Café  São João "
        ),
        exp(category="groceries", account="CONTA CORRENTE"),
        exp(category="Alimentacao", account="conta corrente"),
        card(account="CARTAO EXEMPLO"),
    )
    entries = [a for a in plan.actions if isinstance(a, EntryAction)]
    assert [e.category_id for e in entries[:3]] == [
        "cat-groceries", "cat-groceries", "cat-food",
    ]  # fmt: skip
    assert entries[0].description == "Café  São João"  # trimmed, inner spaces kept


def test_unknown_account_lists_the_closest_names() -> None:
    analysis = feed(exp(account="Conta Corent"))
    issue = analysis.issues[0]
    assert (issue.code, issue.line, issue.column) == ("UNKNOWN_ACCOUNT", 2, "account")
    assert "Conta Corrente" in str(issue.params["suggestions"])


def test_ambiguous_account_and_category_are_never_guessed() -> None:
    ctx = synthetic_context()
    twin = AccountInfo("twin", "CONTA CORRENTE", AccountKind.CHECKING, True)
    ctx = dataclasses.replace(ctx, accounts=(*ctx.accounts, twin))
    assert errors(exp(), ctx=ctx) == [("AMBIGUOUS_ACCOUNT", 2, "account")]
    clash = CategoryInfo("c2", "casa", "Supermercado", CategoryKind.EXPENSE)
    ctx = dataclasses.replace(
        synthetic_context(), categories=(*synthetic_context().categories, clash)
    )
    assert errors(exp(category="supermercado"), ctx=ctx) == [("AMBIGUOUS_CATEGORY", 2, "category")]
    assert feed(exp(category="Casa"), ctx=ctx).issues[0].code == "AMBIGUOUS_CATEGORY"


def test_unknown_category() -> None:
    assert errors(exp(category="Mercadinho")) == [("UNKNOWN_CATEGORY", 2, "category")]


def test_blank_category_defaults_by_kind() -> None:
    plan = one(
        exp(),
        exp(kind="income", description="Bônus"),
        exp(kind="refund", description="Volta"),
    )
    entries = [a for a in plan.actions if isinstance(a, EntryAction)]
    assert [e.category_id for e in entries] == [
        "cat-uncategorized", "cat-other_income", "cat-refund",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "category"),
    [("expense", "Salário"), ("income", "Casa"), ("refund", "Casa"), ("expense", "Transferência")],
)
def test_category_kind_must_match_the_entry_kind(kind: str, category: str) -> None:
    found = errors(exp(kind=kind, category=category))
    assert found == [("CATEGORY_KIND_MISMATCH", 2, "category")]


def test_neutral_categories_for_refund_and_fixed_category_for_transfer() -> None:
    assert feed(exp(kind="refund", category="Estorno")).ok
    base = {"kind": "transfer", "account": "Conta Corrente", "to_account": "Conta Reserva"}
    assert feed(exp(**base, category="Transferência")).ok
    assert errors(exp(**base, category="Casa")) == [("CATEGORY_KIND_MISMATCH", 2, "category")]
    assert errors(exp(**base, category="Estorno")) == [("TRANSFER_CATEGORY_FIXED", 2, "category")]


def test_account_kind_rules() -> None:
    assert errors(exp(account="Conta Antiga")) == [("ACCOUNT_INACTIVE", 2, "account")]
    assert errors(card(kind="income", description="x")) == [
        ("ACCOUNT_KIND_NOT_ALLOWED", 2, "account")
    ]
    assert errors(exp(account="Caixinha Exemplo")) == [("ACCOUNT_KIND_NOT_ALLOWED", 2, "account")]
    assert errors(exp(statement="2026-09")) == [("STATEMENT_ONLY_FOR_CARDS", 2, "statement")]
    assert errors(exp(installments="3")) == [("INSTALLMENTS_ONLY_ON_CARDS", 2, "installments")]
    assert feed(exp(installments="1")).ok  # a single payment is not an installment purchase


def test_signs_and_totals_per_account() -> None:
    plan = one(
        exp(amount="-10,00"),
        exp(kind="income", amount="100,00", description="Bônus"),
        exp(kind="refund", amount="3,00", description="Volta"),
    )
    assert {t.account_id: (t.count, t.sum_cents) for t in plan.account_totals} == {"chk": (3, 9300)}
    entries = [a for a in plan.actions if isinstance(a, EntryAction)]
    assert [(e.kind, e.amount_cents) for e in entries] == [
        (TransactionKind.EXPENSE, 1000),
        (TransactionKind.INCOME, 10000),
        (TransactionKind.REFUND, 300),
    ]


# ---- transfers ---------------------------------------------------------------------------------


def transfer(**f: str) -> str:
    return row(**{"date": "2026-09-15", "kind": "transfer", "amount": "300,00", **f})


def test_transfer_between_own_accounts_and_one_leg_to_untracked() -> None:
    plan = one(
        transfer(account="Conta Corrente", to_account="Caixinha Exemplo"),
        transfer(account="Conta Corrente"),  # money leaves to an account the system does not track
        transfer(to_account="Conta Reserva"),  # money arrives from one it does not track
    )
    legs = [a for a in plan.actions if isinstance(a, TransferAction)]
    assert [(t.from_id, t.to_id) for t in legs] == [("chk", "inv"), ("chk", None), (None, "res")]
    totals = {t.account_id: (t.count, t.sum_cents) for t in plan.account_totals}
    assert totals == {"chk": (2, -60000), "inv": (1, 30000), "res": (1, 30000)}


def test_transfer_errors() -> None:
    assert errors(transfer()) == [("TRANSFER_NEEDS_ACCOUNT", 2, "account")]
    assert errors(transfer(account="Conta Corrente", to_account="conta corrente")) == [
        ("TRANSFER_SAME_ACCOUNT", 2, "to_account")
    ]
    assert errors(transfer(account="Cartão Exemplo", to_account="Conta Corrente")) == [
        ("ACCOUNT_KIND_NOT_ALLOWED", 2, "account")
    ]
    assert errors(transfer(account="Conta Corrente", to_account="Corretora Por Aplicação")) == [
        ("HOLDING_REQUIRED", 2, "to_account")
    ]
    assert errors(transfer(account="Conta Antiga", to_account="Conta Reserva")) == [
        ("ACCOUNT_INACTIVE", 2, "account")
    ]
    assert errors(
        transfer(account="Conta Corrente", to_account="Conta Reserva", statement="2026-09")
    ) == [("STATEMENT_ONLY_FOR_CARDS", 2, "statement")]


# ---- cards: statement assignment (CLAUDE.md 9.3) -----------------------------------------------


@pytest.mark.parametrize(
    ("purchase", "month", "closes", "due", "reason"),
    [
        ("2026-07-20", "2026-07", "2026-07-25", "2026-08-05", AssignmentReason.BEFORE_CLOSING),
        ("2026-07-24", "2026-07", "2026-07-25", "2026-08-05", AssignmentReason.BEFORE_CLOSING),
        ("2026-07-25", "2026-08", "2026-08-25", "2026-09-05", AssignmentReason.ON_CLOSING_DAY),
        ("2026-09-23", "2026-09", "2026-09-24", "2026-10-05", AssignmentReason.BEFORE_CLOSING),
        ("2026-09-24", "2026-10", "2026-10-25", "2026-11-05", AssignmentReason.ON_CLOSING_DAY),
        ("2026-12-25", "2027-01", "2027-01-25", "2027-02-05", AssignmentReason.ON_CLOSING_DAY),
        ("2027-02-21", "2027-02", "2027-02-22", "2027-03-05", AssignmentReason.BEFORE_CLOSING),
        ("2027-02-22", "2027-03", "2027-03-25", "2027-04-05", AssignmentReason.ON_CLOSING_DAY),
    ],
)  # fmt: skip
def test_card_purchase_goes_to_the_statement_of_the_cycle(
    purchase: str, month: str, closes: str, due: str, reason: AssignmentReason
) -> None:
    ctx = dataclasses.replace(synthetic_context(), today=dt.date(2028, 1, 1))
    plan = one(card(date=purchase), ctx=ctx)
    action = plan.actions[0]
    assert isinstance(action, EntryAction) and action.statement is not None
    choice = action.statement
    assert (str(choice.month), choice.closing_date, choice.due_date, choice.reason) == (
        month, dt.date.fromisoformat(closes), dt.date.fromisoformat(due), reason,
    )  # fmt: skip
    assert choice.cycle_month == choice.month and not choice.exists
    assert plan.reasons == {reason: 1}


def test_explicit_statement_overrides_the_cycle_and_is_reported() -> None:
    plan = one(card(date="2026-09-10", statement="2026-11"))
    action = plan.actions[0]
    assert isinstance(action, EntryAction) and action.statement is not None
    assert action.statement.month == YearMonth(2026, 11)
    assert action.statement.reason is AssignmentReason.EXPLICIT
    assert action.statement.cycle_month == YearMonth(2026, 9)
    [warning] = plan.warnings
    assert (warning.code, warning.line) == ("STATEMENT_OVERRIDES_CYCLE", 2)
    assert warning.params == {"statement": "2026-11", "cycle": "2026-09"}
    # an explicit statement equal to the cycle is not a surprise
    assert not one(card(date="2026-09-10", statement="2026-09")).warnings


def test_stored_dates_of_existing_statements_win_over_the_card_settings() -> None:
    edited = StatementInfo(
        "card", YearMonth(2026, 8), dt.date(2026, 8, 20), dt.date(2026, 8, 30), False, 0, 0
    )
    ctx = synthetic_context(statements=(edited,))
    plan = one(card(date="2026-08-22"), ctx=ctx)  # the settings would keep it in August (closes 25)
    action = plan.actions[0]
    assert isinstance(action, EntryAction) and action.statement is not None
    assert action.statement.month == YearMonth(2026, 9)
    touch = plan.statements[0]
    assert (touch.month, touch.exists) == (YearMonth(2026, 9), False)


def test_card_refund_and_charges_go_to_a_statement_and_reduce_what_is_owed() -> None:
    plan = one(
        card(date="2026-09-10"),
        card(date="2026-09-11", kind="refund", amount="4,00", description="x"),
    )
    touch = plan.statements[0]
    assert (touch.entries, touch.owed_cents, touch.signed_sum_cents) == (2, 600, -600)
    assert [(t.count, t.sum_cents) for t in plan.account_totals] == [(2, -600)]


def test_entries_on_a_paid_statement_are_refused() -> None:
    ctx = synthetic_context(statements=(paid_statement(YearMonth(2026, 8)),))
    assert errors(card(date="2026-08-20"), ctx=ctx) == [("STATEMENT_ALREADY_PAID", 2, "date")]
    assert errors(card(date="2026-09-10", statement="2026-08"), ctx=ctx) == [
        ("STATEMENT_ALREADY_PAID", 2, "statement")
    ]
    assert feed(card(date="2026-09-10"), ctx=ctx).ok


# ---- cards: installments (CLAUDE.md 9.4) -------------------------------------------------------


def purchase_of(plan) -> PurchaseAction:  # type: ignore[no-untyped-def]
    [action] = [a for a in plan.actions if isinstance(a, PurchaseAction)]
    return action


def test_installments_split_the_total_with_the_remainder_on_the_first() -> None:
    plan = one(card(date="2026-07-26", amount="301,00", installments="3"))
    action = purchase_of(plan)
    assert [
        (ln.number, str(ln.month), ln.amount_cents, ln.due_date.isoformat()) for ln in action.lines
    ] == [
        (1, "2026-08", 10034, "2026-09-05"),
        (2, "2026-09", 10033, "2026-10-05"),
        (3, "2026-10", 10033, "2026-11-05"),
    ]
    assert action.lines[0].posted_on == dt.date(2026, 7, 26)  # installment 1: the purchase date
    assert action.lines[1].posted_on == dt.date(2026, 9, 23)  # the rest: last day of the statement
    assert [(t.count, t.sum_cents) for t in plan.account_totals] == [(3, -30100)]
    assert [(t.month.month, t.entries) for t in plan.statements] == [(8, 1), (9, 1), (10, 1)]


def test_remainder_example_from_a_real_statement_pattern() -> None:
    action = purchase_of(one(card(amount="78,23", installments="10")))
    amounts = [ln.amount_cents for ln in action.lines]
    assert amounts == [785] + [782] * 9 and sum(amounts) == 7823


def test_installment_value_repeats_and_total_type_is_the_default() -> None:
    action = purchase_of(one(card(amount="100,00", installments="4", amount_type="installment")))
    assert [ln.amount_cents for ln in action.lines] == [10000] * 4
    assert purchase_of(one(card(amount="100,00", installments="4"))).total_cents == 10000


def test_running_purchase_generates_installments_n_to_total() -> None:
    action = purchase_of(
        one(
            card(
                date="2026-03-10",
                amount="350,00",
                installments="10",
                installment_number="3",
                statement="2026-09",
                amount_type="installment",
            )
        )
    )
    assert [ln.number for ln in action.lines] == list(range(3, 11))
    assert str(action.lines[0].month) == "2026-09" and str(action.lines[-1].month) == "2027-04"
    assert action.first_number == 3 and action.explicit_statement == YearMonth(2026, 9)
    assert action.statement.cycle_month is None  # the date is the old purchase: not comparable


def test_running_total_uses_the_installment_amounts_of_the_whole_split() -> None:
    action = purchase_of(
        one(card(amount="78,23", installments="10", installment_number="2", statement="2026-09"))
    )
    assert [ln.amount_cents for ln in action.lines] == [782] * 9


def test_installment_errors() -> None:
    assert errors(card(installments="3", installment_number="2")) == [
        ("STATEMENT_REQUIRED", 2, "statement")
    ]
    assert errors(card(amount="0,05", installments="10")) == [
        ("INSTALLMENT_AMOUNT_TOO_SMALL", 2, "amount")
    ]
    ctx = synthetic_context(statements=(paid_statement(YearMonth(2026, 10)),))
    assert errors(card(date="2026-09-10", amount="30,00", installments="3"), ctx=ctx) == [
        ("STATEMENT_ALREADY_PAID", 2, "date")
    ]


# ---- payments (CLAUDE.md 9.5) ------------------------------------------------------------------


def test_payment_of_a_statement_that_the_file_creates() -> None:
    plan = one(
        transfer(account="Conta Corrente", to_account="Cartão Exemplo", statement="2026-09",
                 amount="150,00", date="2026-10-05"),
        card(date="2026-09-10", amount="100,00"),
    )  # fmt: skip
    kinds = [type(a) for a in plan.actions]
    assert kinds == [EntryAction, PaymentAction]  # the payment runs after the purchases
    touch = plan.statements[0]
    assert (touch.entries, touch.payments, touch.owed_cents, touch.paid_cents) == (
        1,
        1,
        10000,
        15000,
    )
    assert touch.signed_sum_cents == 5000
    assert {t.account_id: t.sum_cents for t in plan.account_totals} == {"card": 5000, "chk": -15000}
    assert [w.code for w in plan.warnings] == ["PAYMENT_ABOVE_OUTSTANDING"]


def test_payment_of_an_existing_statement_and_from_an_untracked_account() -> None:
    stored = StatementInfo(
        "card", YearMonth(2026, 9), dt.date(2026, 9, 24), dt.date(2026, 10, 5), False, 5000, 0
    )
    ctx = synthetic_context(statements=(stored,))
    plan = one(transfer(to_account="Cartão Exemplo", statement="2026-09", amount="50,00"), ctx=ctx)
    [payment] = plan.actions
    assert isinstance(payment, PaymentAction) and payment.from_id is None
    assert not plan.warnings and plan.statements[0].exists


def test_payment_errors() -> None:
    base = {"account": "Conta Corrente", "to_account": "Cartão Exemplo"}
    assert errors(transfer(**base)) == [("STATEMENT_REQUIRED", 2, "statement")]
    assert errors(transfer(**base, statement="2026-09")) == [
        ("STATEMENT_NOT_FOUND", 2, "statement")
    ]
    assert errors(transfer(**base, statement="2026-09", notes="x")) == [
        ("PAYMENT_NOTES_NOT_SUPPORTED", 2, "notes")
    ]
    assert errors(
        transfer(account="Caixinha Exemplo", to_account="Cartão Exemplo", statement="2026-09")
    ) == [("ACCOUNT_KIND_NOT_ALLOWED", 2, "account")]


# ---- balances (CLAUDE.md 9.7) ------------------------------------------------------------------


def balance(**f: str) -> str:
    return row(**{"date": "2026-09-30", "kind": "balance", "account": "Conta Corrente",
                  "amount": "100,00", **f})  # fmt: skip


def test_balances_are_signed_and_investments_take_a_gross_value() -> None:
    plan = one(
        balance(amount="-250,00"),
        balance(account="Caixinha Exemplo", amount="1.000,00", gross_amount="1.020,00"),
        balance(account="Conta Reserva", amount="0", description="zerada"),
    )
    actions = [a for a in plan.actions if isinstance(a, BalanceAction)]
    assert [(a.balance_cents, a.gross_cents) for a in actions] == [
        (-25000, None), (100000, 102000), (0, None)
    ]  # fmt: skip
    assert plan.account_totals == ()  # balances create no transactions


def test_balance_errors_and_warning() -> None:
    assert errors(balance(account="Cartão Exemplo")) == [("BALANCE_NOT_FOR_CARDS", 2, "account")]
    assert errors(balance(account="Corretora Por Aplicação")) == [
        ("ACCOUNT_TRACKS_HOLDINGS", 2, "account")
    ]
    assert errors(balance(gross_amount="200,00")) == [
        ("GROSS_ONLY_FOR_INVESTMENTS", 2, "gross_amount")
    ]
    assert errors(balance(account="Caixinha Exemplo", gross_amount="50,00")) == [
        ("INVALID_GROSS_BALANCE", 2, "gross_amount")
    ]
    assert errors(balance(), balance(amount="5,00")) == [("DUPLICATE_BALANCE", 3, "date")]
    assert feed(balance(), balance(date="2026-09-29")).ok
    ctx = synthetic_context(anchors=frozenset({("chk", dt.date(2026, 9, 30))}))
    assert [w.code for w in one(balance(), ctx=ctx).warnings] == ["BALANCE_REPLACES_EXISTING"]


# ---- plan shape --------------------------------------------------------------------------------


def test_actions_run_entries_first_then_payments_then_balances() -> None:
    plan = one(
        balance(),
        transfer(account="Conta Corrente", to_account="Cartão Exemplo", statement="2026-09"),
        exp(),
        card(),
        transfer(account="Conta Corrente", to_account="Conta Reserva"),
    )
    assert [type(a).__name__ for a in plan.actions] == [
        "EntryAction", "EntryAction", "TransferAction", "PaymentAction", "BalanceAction",
    ]  # fmt: skip


def test_identical_rows_are_kept_and_only_counted() -> None:
    plan = one(exp(), exp(), exp(), exp(description="outro"))
    assert len([a for a in plan.actions if isinstance(a, EntryAction)]) == 4
    assert plan.duplicate_rows == 2
    assert [(w.code, w.params) for w in plan.warnings] == [("DUPLICATE_ROWS", {"count": 2})]


def test_plan_totals_by_kind() -> None:
    plan = one(exp(), exp(), balance())
    assert {k.value: n for k, n in plan.rows_by_kind.items()} == {"expense": 2, "balance": 1}
    assert plan.transactions == 2 and plan.balances == 1 and TODAY
