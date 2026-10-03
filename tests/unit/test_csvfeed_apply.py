"""Applying a plan through the use cases (memory repositories) and verifying the result."""

from dataclasses import replace

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.csvfeed import (
    ApplyFeed,
    FeedPlan,
    analyze_feed,
    load_context,
    snapshot,
    verify_feed,
)
from financas.domain.models import Account, TransactionKind
from financas.domain.money import YearMonth

HEADER = (
    "date;kind;account;to_account;amount;description;category;recurring;notes;statement;"
    "installments;installment_number;amount_type;gross_amount\n"
)
FILE = HEADER + "\n".join(
    [
        "2026-06-01;expense;Conta Corrente;;-100,00;Aluguel;Casa;yes;;;;;;",
        "2026-06-02;income;Conta Corrente;;2.000,00;Salário;;;;;;;;",
        "2026-06-03;transfer;Conta Corrente;Caixinha;500,00;Aporte;;;;;;;;",
        "2026-06-10;expense;Cartão;;301,00;Fone;Compras;;;;3;;total;",
        "2026-06-11;expense;Cartão;;50,00;Mercado;Supermercado;;;;;;;",
        "2026-06-12;refund;Cartão;;5,00;Volta;;;;;;;;",
        "2026-07-05;transfer;Conta Corrente;Cartão;200,00;;;;;2026-06;;;;",
        "2026-06-30;balance;Conta Corrente;;1.400,00;;;;;;;;;",
        "2026-06-30;balance;Caixinha;;520,00;;;;;;;;;525,00",
    ]
)


def run(uow: MemoryUnitOfWork, clock: FixedClock, text: str = FILE) -> tuple[FeedPlan, object]:
    analysis = analyze_feed(text.encode(), load_context(uow, clock))
    assert analysis.ok, analysis.issues
    plan = analysis.plan
    assert plan is not None
    before = snapshot(uow, plan)
    result = ApplyFeed(uow, clock).execute(plan, "Pagamento de fatura")
    verification = verify_feed(uow, plan, before)
    assert verification.ok, [c for c in verification.checks if not c.ok]
    return plan, result


def test_apply_creates_exactly_what_the_plan_promised(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, savings: Account, card: Account
) -> None:
    plan, result = run(uow, clock)
    assert (result.entries, result.purchases, result.transfers, result.payments) == (  # type: ignore[attr-defined]
        4, 1, 1, 1
    )  # fmt: skip
    with uow as work:
        rows = {a.id: work.transactions.list_by_account(a.id) for a in (checking, savings, card)}
        statements = {s.month: s for s in work.statements.list_for_card(card.id)}
    assert len(rows[card.id]) == plan.transactions - len(rows[checking.id]) - len(rows[savings.id])
    assert sorted(str(m) for m in statements) == ["2026-06", "2026-07", "2026-08"]
    june = YearMonth(2026, 6)
    # 2026-06-10 is before the closing date (06-24): installment 1 and the card entries are in June
    assert statements[june].closing_date.isoformat() == "2026-06-24"
    plan_rows = [t for t in rows[card.id] if t.plan_id]
    assert sorted((t.installment_number, -t.amount_cents) for t in plan_rows) == [
        (1, 10034), (2, 10033), (3, 10033),
    ]  # fmt: skip
    payment = [t for t in rows[card.id] if t.kind is TransactionKind.TRANSFER]
    assert len(payment) == 1 and payment[0].description == "Pagamento de fatura"
    assert payment[0].statement_id == statements[june].id
    assert sum(t.amount_cents for t in rows[checking.id]) == -10000 + 200000 - 50000 - 20000


def test_balance_difference_comes_from_the_use_case(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, savings: Account, card: Account
) -> None:
    _, result = run(uow, clock)
    by_account = {b.account_id: b for b in result.balances}  # type: ignore[attr-defined]
    assert by_account[checking.id].informed_cents == 140000
    assert by_account[savings.id].informed_cents == 52000
    with uow as work:
        gross = work.anchors.list_for_account(savings.id)[0].gross_balance_cents
    assert gross == 52500


def test_a_second_file_sees_the_statements_the_first_created(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, savings: Account, card: Account
) -> None:
    run(uow, clock)
    second = HEADER + "2026-07-01;expense;Cartão;;10,00;Padaria;;;;;;;;\n"
    analysis = analyze_feed(second.encode(), load_context(uow, clock))
    assert analysis.plan is not None
    [touch] = analysis.plan.statements
    assert touch.exists and touch.month == YearMonth(2026, 7)


def test_verification_catches_a_missing_transaction(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, savings: Account, card: Account
) -> None:
    analysis = analyze_feed(FILE.encode(), load_context(uow, clock))
    plan = analysis.plan
    assert plan is not None
    before = snapshot(uow, plan)
    ApplyFeed(uow, clock).execute(plan, "x")
    with uow as work:
        victim = work.transactions.list_by_account(checking.id)[0]
        work.transactions.delete(victim.id)
        work.commit()
    verification = verify_feed(uow, plan, before)
    assert not verification.ok
    assert {c.code for c in verification.checks if not c.ok} >= {"COUNT_BY_ACCOUNT"}


def test_a_changed_plan_amount_is_caught_by_the_sums(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, savings: Account, card: Account
) -> None:
    analysis = analyze_feed(FILE.encode(), load_context(uow, clock))
    plan = analysis.plan
    assert plan is not None
    before = snapshot(uow, plan)
    ApplyFeed(uow, clock).execute(plan, "x")
    lying = replace(
        plan,
        account_totals=tuple(replace(t, sum_cents=t.sum_cents + 1) for t in plan.account_totals),
    )
    assert {c.code for c in verify_feed(uow, lying, before).checks if not c.ok} >= {
        "SUM_BY_ACCOUNT"
    }
