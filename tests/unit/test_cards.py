"""Phase 2 acceptance tests: CLAUDE.md sections 9.3 to 9.5 and the card parts of section 10."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.cards import (
    InstallmentSchedule,
    ListActiveInstallments,
    ListCards,
    ListMonthPurchases,
)
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.cards import (
    AdjustInstallment,
    CardPurchaseCommand,
    DeletePurchase,
    InformStatementTotal,
    MoveEntryToStatement,
    PayStatement,
    PayStatementCommand,
    PostStatementDifference,
    PreviewCardPurchase,
    RegisterCardPurchase,
    SetCardSettings,
    SetStatementDates,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, StatementStatus, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import AssignmentReason
from financas.domain.services.statements import LimitAlert

D = dt.date
YM = YearMonth


def buy(uow: MemoryUnitOfWork, card: Account, **kw: object):
    values: dict[str, object] = {"account_id": card.id, "description": "Fone de ouvido"}
    values.update(kw)
    return RegisterCardPurchase(uow).execute(CardPurchaseCommand(**values))  # type: ignore[arg-type]


def codes(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


# --- statement assignment (9.3) ---


@pytest.mark.parametrize(
    ("purchase", "month", "closes", "due"),
    [
        (D(2026, 7, 20), YM(2026, 7), D(2026, 7, 25), D(2026, 8, 5)),
        (D(2026, 7, 25), YM(2026, 7), D(2026, 7, 25), D(2026, 8, 5)),
        (D(2026, 7, 26), YM(2026, 8), D(2026, 8, 25), D(2026, 9, 5)),
        (D(2026, 12, 26), YM(2027, 1), D(2027, 1, 25), D(2027, 2, 5)),
    ],
)
def test_single_purchase_goes_to_the_statement_of_the_card_cycle(
    uow: MemoryUnitOfWork,
    card: Account,
    purchase: dt.date,
    month: YearMonth,
    closes: dt.date,
    due: dt.date,
) -> None:
    result = buy(uow, card, purchased_on=purchase, total_cents=5_000)
    (entry,) = result.transactions
    statement = uow.statements.get(entry.statement_id or "")
    assert statement and (statement.month, statement.closing_date, statement.due_date) == (
        month,
        closes,
        due,
    )
    assert entry.amount_cents == -5_000 and entry.kind is TransactionKind.EXPENSE
    assert entry.plan_id is None and entry.installment_number is None
    assert entry.posted_on == purchase
    assert result.plan is None


def test_two_purchases_share_one_statement(uow: MemoryUnitOfWork, card: Account) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=100)
    buy(uow, card, purchased_on=D(2026, 7, 20), total_cents=200)
    assert len(uow.statements.list_for_card(card.id)) == 1


def test_explicit_statement_overrides_the_cycle(uow: MemoryUnitOfWork, card: Account) -> None:
    preview = PreviewCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id,
            "x",
            D(2026, 7, 26),
            total_cents=300,
            installments=3,
            statement_month=YM(2026, 7),
        )
    )
    assert preview.assignment.reason is AssignmentReason.EXPLICIT
    assert [str(line.statement_month) for line in preview.lines] == [
        "2026-07",
        "2026-08",
        "2026-09",
    ]


# --- installments (9.4) ---


def test_three_installments_example_from_the_spec(uow: MemoryUnitOfWork, card: Account) -> None:
    result = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    assert result.plan and result.plan.installment_total == 3
    assert result.plan.purchased_on == D(2026, 7, 26)
    lines = result.preview.lines
    assert [line.amount_cents for line in lines] == [10_034, 10_033, 10_033]
    assert [str(line.statement_month) for line in lines] == ["2026-08", "2026-09", "2026-10"]
    assert [line.due_date for line in lines] == [D(2026, 9, 5), D(2026, 10, 5), D(2026, 11, 5)]
    assert result.preview.total_cents == 30_100
    assert result.preview.assignment.reason is AssignmentReason.AFTER_CLOSING
    entries = result.transactions
    assert [e.installment_number for e in entries] == [1, 2, 3]
    assert {e.plan_id for e in entries} == {result.plan.id}
    # installment 1 is dated on the purchase, the others on the closing date of their statement
    assert [e.posted_on for e in entries] == [D(2026, 7, 26), D(2026, 9, 25), D(2026, 10, 25)]
    assert -sum(e.amount_cents for e in entries) == 30_100


def test_running_purchase_creates_only_the_remaining_installments(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy(
        uow, card, installments=10, current_installment=3, installment_cents=6_188,
        statement_month=YM(2026, 9),
    )  # fmt: skip
    entries = result.transactions
    assert len(entries) == 8
    assert [e.installment_number for e in entries] == list(range(3, 11))
    months = [uow.statements.get(e.statement_id or "").month for e in entries]  # type: ignore[union-attr]
    assert (str(months[0]), str(months[-1])) == ("2026-09", "2027-04")
    assert result.plan and result.plan.installment_total == 10 and result.plan.purchased_on is None
    assert all(
        e.posted_on == uow.statements.get(e.statement_id or "").closing_date for e in entries
    )  # type: ignore[union-attr]


def test_purchase_validation(uow: MemoryUnitOfWork, card: Account, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        buy(uow, card, installments=10, current_installment=3, total_cents=1_000)
    assert codes(exc) == "STATEMENT_REQUIRED"
    with pytest.raises(DomainError) as exc:
        buy(uow, card, total_cents=1_000)
    assert codes(exc) == "PURCHASE_DATE_REQUIRED"
    with pytest.raises(DomainError) as exc:
        buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=1_000, installment_cents=500)
    assert codes(exc) == "AMOUNT_REQUIRED"
    with pytest.raises(DomainError) as exc:
        buy(
            uow,
            card,
            purchased_on=D(2026, 7, 1),
            total_cents=1_000,
            installments=3,
            current_installment=4,
        )
    assert codes(exc) == "INSTALLMENT_OUT_OF_RANGE"
    with pytest.raises(DomainError) as exc:
        buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=1_000, description="  ")
    assert codes(exc) == "EMPTY_DESCRIPTION"
    with pytest.raises(DomainError) as exc:
        buy(uow, checking, purchased_on=D(2026, 7, 1), total_cents=1_000)
    assert codes(exc) == "CARD_REQUIRED"
    income = uow.categories.get_by_slug("salary")
    assert income
    with pytest.raises(DomainError) as exc:
        buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=1_000, category_id=income.id)
    assert codes(exc) == "CATEGORY_KIND_MISMATCH"
    assert uow.transactions.items == {} and uow.plans.items == {}


def test_default_category_is_uncategorized(uow: MemoryUnitOfWork, card: Account) -> None:
    (entry,) = buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=100).transactions
    assert entry.category_id == (uow.categories.get_by_slug("uncategorized") or object).id  # type: ignore[attr-defined]


def test_preview_writes_nothing_and_flags_existing_statements(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    cmd = CardPurchaseCommand(card.id, "x", D(2026, 7, 26), total_cents=300, installments=3)
    first = PreviewCardPurchase(uow).execute(cmd)
    assert uow.transactions.items == {} and uow.statements.items == {}
    assert [line.statement_exists for line in first.lines] == [False] * 3
    buy(uow, card, purchased_on=D(2026, 8, 1), total_cents=100)  # creates the 2026-08 statement
    again = PreviewCardPurchase(uow).execute(cmd)
    assert [line.statement_exists for line in again.lines] == [True, False, False]


def test_changing_the_closing_day_only_affects_new_statements(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 20), total_cents=100)  # statement 2026-07 closes 07-25
    SetCardSettings(uow).execute(card.id, closing_day=10, due_day=17, credit_limit_cents=None)
    july = uow.statements.get_by_card_month(card.id, YM(2026, 7))
    assert july and july.closing_date == D(2026, 7, 25)
    (entry,) = buy(uow, card, purchased_on=D(2026, 9, 5), total_cents=100).transactions
    september = uow.statements.get(entry.statement_id or "")
    assert september and (september.closing_date, september.due_date) == (
        D(2026, 9, 10),
        D(2026, 9, 17),
    )


def test_card_settings_validation(uow: MemoryUnitOfWork, card: Account, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        SetCardSettings(uow).execute(card.id, 40, 5, None)
    assert codes(exc) == "INVALID_CARD_DAY"
    with pytest.raises(DomainError) as exc:
        SetCardSettings(uow).execute(checking.id, 25, 5, None)
    assert codes(exc) == "CARD_REQUIRED"


# --- statements, payment and limit (9.5) ---


def september_statement(uow: MemoryUnitOfWork, card: Account) -> str:
    """A statement closing 2026-09-25 with R$ 100,00 + R$ 50,00 (due 2026-10-05)."""
    buy(uow, card, purchased_on=D(2026, 9, 1), total_cents=10_000)
    (entry,) = buy(uow, card, purchased_on=D(2026, 9, 10), total_cents=5_000).transactions
    return entry.statement_id or ""


def cards_at(uow: MemoryUnitOfWork, today: dt.date):
    return ListCards(uow, FixedClock(today)).execute()


def test_statement_total_status_and_due_date(uow: MemoryUnitOfWork, card: Account) -> None:
    september = september_statement(uow, card)
    # refund and charge on the same statement
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            card.id, D(2026, 9, 12), TransactionKind.REFUND, 2_000, "Estorno"
        )
    )
    fees = uow.categories.get_by_slug("fees")
    assert fees
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            card.id, D(2026, 9, 20), TransactionKind.EXPENSE, 700, "IOF", category_id=fees.id
        )
    )
    for today, status in [
        (D(2026, 8, 25), StatementStatus.FUTURE),
        (D(2026, 8, 26), StatementStatus.OPEN),
        (D(2026, 9, 25), StatementStatus.OPEN),
        (D(2026, 9, 26), StatementStatus.CLOSED),
    ]:
        (view,) = [
            s for s in cards_at(uow, today).cards[0].statements if s.statement.id == september
        ]
        assert view.status is status
        assert (view.total_cents, view.paid_cents, view.outstanding_cents) == (13_700, 0, 13_700)
        assert view.statement.due_date == D(2026, 10, 5)
    closed = cards_at(uow, D(2026, 10, 1)).cards[0].statements[0]
    assert closed.days_to_due == 4


def test_pay_statement_partial_then_full(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    september = september_statement(uow, card)
    clock = FixedClock(D(2026, 10, 1))
    pay = PayStatement(uow, clock)
    legs = pay.execute(
        PayStatementCommand(september, checking.id, D(2026, 10, 1), 4_000, "Pagamento")
    )
    out, into = legs
    assert (out.account_id, out.amount_cents, out.statement_id) == (checking.id, -4_000, None)
    assert (into.account_id, into.amount_cents, into.statement_id) == (card.id, 4_000, september)
    assert out.transfer_id and out.transfer_id == into.transfer_id
    assert {leg.kind for leg in legs} == {TransactionKind.TRANSFER}
    view = cards_at(uow, D(2026, 10, 1)).cards[0].statements[0]
    assert (view.paid_cents, view.outstanding_cents, view.status) == (
        4_000,
        11_000,
        StatementStatus.CLOSED,
    )
    pay.execute(PayStatementCommand(september, checking.id, D(2026, 10, 2)))  # the rest
    view = cards_at(uow, D(2026, 10, 2)).cards[0].statements[0]
    assert (view.paid_cents, view.outstanding_cents, view.status) == (
        15_000,
        0,
        StatementStatus.PAID,
    )
    with pytest.raises(DomainError) as exc:
        pay.execute(PayStatementCommand(september, checking.id, D(2026, 10, 3)))
    assert codes(exc) == "NOTHING_TO_PAY"
    with pytest.raises(DomainError) as exc:
        pay.execute(PayStatementCommand(september, checking.id, D(2026, 10, 3), 0))
    assert codes(exc) == "AMOUNT_NOT_POSITIVE"


def test_pay_statement_rules_and_atomicity(
    uow: MemoryUnitOfWork, card: Account, savings: Account
) -> None:
    september = september_statement(uow, card)
    pay = PayStatement(uow, FixedClock(D(2026, 10, 1)))
    before = dict(uow.transactions.items)
    with pytest.raises(DomainError) as exc:
        pay.execute(PayStatementCommand(september, savings.id, D(2026, 10, 1)))
    assert codes(exc) == "ACCOUNT_KIND_NOT_ALLOWED"
    with pytest.raises(DomainError) as exc:
        pay.execute(PayStatementCommand("nope", savings.id, D(2026, 10, 1)))
    assert codes(exc) == "NOT_FOUND"
    assert uow.transactions.items == before


def test_payments_are_not_expenses_in_the_totals(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    september = september_statement(uow, card)
    PayStatement(uow, FixedClock(D(2026, 10, 1))).execute(
        PayStatementCommand(september, checking.id, D(2026, 10, 1))
    )
    summary = GetSummary(uow).execute(Period.month(YM(2026, 9)))
    assert summary.expenses_cents == 15_000  # counted once, in the statement month
    assert GetSummary(uow).execute(Period.month(YM(2026, 10))).expenses_cents == 0


def test_reconciliation_and_posting_the_difference(uow: MemoryUnitOfWork, card: Account) -> None:
    september = september_statement(uow, card)
    with pytest.raises(DomainError) as exc:
        PostStatementDifference(uow).execute(september)
    assert codes(exc) == "TOTAL_NOT_INFORMED"
    InformStatementTotal(uow).execute(september, 15_380)
    view = cards_at(uow, D(2026, 10, 1)).cards[0].statements[0]
    rec = view.reconciliation
    assert (rec.entered_cents, rec.informed_cents, rec.difference_cents) == (15_000, 15_380, 380)
    entry = PostStatementDifference(uow).execute(september, "Diferença")
    assert entry.amount_cents == -380 and entry.statement_id == september
    assert entry.posted_on == D(2026, 9, 25) and entry.description == "Diferença"
    category = uow.categories.get(entry.category_id)
    assert category and category.slug == "uncategorized"
    view = cards_at(uow, D(2026, 10, 1)).cards[0].statements[0]
    assert (view.total_cents, view.reconciliation.difference_cents) == (15_380, 0)
    with pytest.raises(DomainError) as exc:
        PostStatementDifference(uow).execute(september)
    assert codes(exc) == "DIFFERENCE_NOT_POSITIVE"


def test_inform_total_can_be_cleared_and_validated(uow: MemoryUnitOfWork, card: Account) -> None:
    september = september_statement(uow, card)
    use_case = InformStatementTotal(uow)
    assert use_case.execute(september, 100).informed_total_cents == 100
    assert use_case.execute(september, None).informed_total_cents is None
    with pytest.raises(DomainError):
        use_case.execute(september, -1)


def test_statement_dates_can_be_edited(uow: MemoryUnitOfWork, card: Account) -> None:
    september = september_statement(uow, card)
    updated = SetStatementDates(uow).execute(september, D(2026, 9, 24), D(2026, 10, 6))
    assert (updated.closing_date, updated.due_date) == (D(2026, 9, 24), D(2026, 10, 6))
    with pytest.raises(DomainError) as exc:
        SetStatementDates(uow).execute(september, D(2026, 9, 24), D(2026, 9, 24))
    assert codes(exc) == "INVALID_STATEMENT_DATES"


def test_committed_limit_counts_everything_unpaid_including_future_installments(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    buy(uow, card, purchased_on=D(2026, 7, 27), total_cents=5_000)
    overview = cards_at(uow, D(2026, 8, 1))
    usage = overview.cards[0].usage
    assert usage.committed_cents == 35_100
    assert usage.available_cents == 1_200_000 - 35_100
    assert usage.alert is LimitAlert.NONE
    august = overview.cards[0].statements[0]
    PayStatement(uow, FixedClock(D(2026, 9, 1))).execute(
        PayStatementCommand(august.statement.id, checking.id, D(2026, 9, 1))
    )
    # paying August gives back exactly that amount of limit
    assert cards_at(uow, D(2026, 9, 1)).cards[0].usage.committed_cents == 35_100 - 15_034


def test_limit_alert_and_not_informed(uow: MemoryUnitOfWork, card: Account) -> None:
    SetCardSettings(uow).execute(card.id, 25, 5, 10_000)
    buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=8_500)
    assert cards_at(uow, D(2026, 7, 2)).cards[0].usage.alert is LimitAlert.WARNING
    buy(uow, card, purchased_on=D(2026, 7, 2), total_cents=1_500)
    assert cards_at(uow, D(2026, 7, 2)).cards[0].usage.alert is LimitAlert.EXCEEDED
    SetCardSettings(uow).execute(card.id, 25, 5, None)
    usage = cards_at(uow, D(2026, 7, 2)).cards[0].usage
    assert usage.alert is LimitAlert.NOT_INFORMED and usage.percent is None
    assert cards_at(uow, D(2026, 7, 2)).available_cents is None


def test_overview_totals_by_status(uow: MemoryUnitOfWork, card: Account) -> None:
    # today 2026-10-01: Sep closed (150), Oct open (60), Nov+Dec future (installments)
    september_statement(uow, card)
    buy(uow, card, purchased_on=D(2026, 9, 30), total_cents=6_000)  # October statement
    buy(uow, card, purchased_on=D(2026, 9, 26), total_cents=9_000, installments=3)  # Oct, Nov, Dec
    overview = cards_at(uow, D(2026, 10, 1))
    assert overview.closed_to_pay_cents == 15_000
    assert overview.open_total_cents == 6_000 + 3_000
    assert overview.future_installments_cents == 6_000
    assert overview.committed_cents == 15_000 + 6_000 + 9_000


# --- adjusting, deleting, moving ---


def test_adjust_installment_by_hand_but_not_on_a_paid_statement(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    result = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    second = result.transactions[1]
    AdjustInstallment(uow, FixedClock(D(2026, 8, 1))).execute(second.id, 10_100)
    assert uow.transactions.get(second.id).amount_cents == -10_100  # type: ignore[union-attr]
    august = result.transactions[0].statement_id or ""
    PayStatement(uow, FixedClock(D(2026, 9, 1))).execute(
        PayStatementCommand(august, checking.id, D(2026, 9, 1))
    )
    with pytest.raises(DomainError) as exc:
        AdjustInstallment(uow, FixedClock(D(2026, 9, 1))).execute(result.transactions[0].id, 1)
    assert codes(exc) == "STATEMENT_ALREADY_PAID"
    with pytest.raises(DomainError) as exc:
        AdjustInstallment(uow, FixedClock(D(2026, 9, 1))).execute(second.id, 0)
    assert codes(exc) == "AMOUNT_NOT_POSITIVE"


def test_delete_purchase_removes_the_plan_and_all_installments(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    result = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    assert result.plan
    deleted = DeletePurchase(uow, FixedClock(D(2026, 8, 1))).execute(result.plan.id)
    assert deleted == 3 and uow.transactions.items == {} and uow.plans.items == {}
    again = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    assert again.plan
    august = again.transactions[0].statement_id or ""
    PayStatement(uow, FixedClock(D(2026, 9, 1))).execute(
        PayStatementCommand(august, checking.id, D(2026, 9, 1))
    )
    with pytest.raises(DomainError) as exc:
        DeletePurchase(uow, FixedClock(D(2026, 9, 1))).execute(again.plan.id)
    assert codes(exc) == "STATEMENT_ALREADY_PAID"
    assert len(uow.plans.items) == 1


def test_move_an_entry_to_another_statement(uow: MemoryUnitOfWork, card: Account) -> None:
    (entry,) = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=500).transactions
    MoveEntryToStatement(uow).execute(entry.id, YM(2026, 7))
    moved = uow.transactions.get(entry.id)
    statement = uow.statements.get(moved.statement_id or "")  # type: ignore[union-attr]
    assert statement and str(statement.month) == "2026-07"


# --- active installments, schedule, purchases of the month ---


def test_active_installments_and_monthly_schedule(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    result = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    buy(uow, card, purchased_on=D(2026, 7, 27), total_cents=1_000)  # single: not an installment
    # August is paid; September closed (today 2026-10-01), October open
    PayStatement(uow, FixedClock(D(2026, 9, 6))).execute(
        PayStatementCommand(result.transactions[0].statement_id or "", checking.id, D(2026, 9, 6))
    )
    clock = FixedClock(D(2026, 10, 1))
    (plan,) = ListActiveInstallments(uow, clock).execute()
    assert plan.plan.description == "Fone de ouvido"
    assert (plan.paid_count, plan.remaining_count, plan.to_pay_cents) == (0, 2, 20_066) or (
        plan.paid_count,
        plan.remaining_count,
    ) == (1, 2)
    assert plan.paid_count == 1 and plan.remaining_count == 2 and plan.to_pay_cents == 20_066
    assert str(plan.next_statement_month) == "2026-09"
    rows = InstallmentSchedule(uow, clock).execute()
    assert [(str(r.month), r.amount_cents) for r in rows] == [
        ("2026-09", 10_033),
        ("2026-10", 10_033),
    ]
    assert [r.status for r in rows] == [StatementStatus.CLOSED, StatementStatus.OPEN]


def test_a_fully_paid_plan_is_not_active(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    result = buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=400, installments=2)
    pay = PayStatement(uow, FixedClock(D(2026, 12, 1)))
    for entry in result.transactions:
        pay.execute(PayStatementCommand(entry.statement_id or "", checking.id, D(2026, 12, 1)))
    assert ListActiveInstallments(uow, FixedClock(D(2026, 12, 1))).execute() == []


def test_month_purchases_show_the_full_value_with_installments(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(
        uow,
        card,
        description="Fone",
        purchased_on=D(2026, 9, 26),
        total_cents=30_100,
        installments=3,
    )
    buy(uow, card, description="Tênis", purchased_on=D(2026, 9, 14), total_cents=28_970)
    buy(uow, card, description="Antiga", purchased_on=D(2026, 8, 14), total_cents=1_000)
    buy(uow, card, description="Em andamento", installments=10, current_installment=3,
        installment_cents=500, statement_month=YM(2026, 9))  # fmt: skip
    rows = ListMonthPurchases(uow).execute(D(2026, 9, 1), D(2026, 9, 30))
    assert [(p.description, p.installments, p.total_cents) for p in rows] == [
        ("Fone", 3, 30_100),
        ("Tênis", 1, 28_970),
    ]


# --- competence (section 10) and entries on cards ---


def test_card_expenses_count_in_the_statement_month(uow: MemoryUnitOfWork, card: Account) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    totals = {
        m: GetSummary(uow).execute(Period.month(m)).expenses_cents
        for m in (YM(2026, 7), YM(2026, 8), YM(2026, 9), YM(2026, 10))
    }
    assert totals == {
        YM(2026, 7): 0,
        YM(2026, 8): 10_034,
        YM(2026, 9): 10_033,
        YM(2026, 10): 10_033,
    }
    assert GetSummary(uow).execute(Period.year(2026)).expenses_cents == 30_100
    months = GetSummary(uow).months_of_year(2026)
    assert [m.expenses_cents for m in months][6:10] == [0, 10_034, 10_033, 10_033]


def test_refund_on_the_card_reduces_the_statement_and_counts_apart(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=10_000)
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(card.id, D(2026, 7, 5), TransactionKind.REFUND, 2_500, "Estorno")
    )
    summary = GetSummary(uow).execute(Period.month(YM(2026, 7)))
    assert (summary.expenses_cents, summary.refunds_cents, summary.net_expenses_cents) == (
        10_000,
        2_500,
        7_500,
    )
    assert cards_at(uow, D(2026, 7, 10)).cards[0].statements[0].total_cents == 7_500


def test_register_transaction_on_a_card_follows_the_cycle_and_the_override(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    after = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(card.id, D(2026, 7, 26), TransactionKind.EXPENSE, 100, "x")
    )
    statement = uow.statements.get(after.statement_id or "")
    assert statement and str(statement.month) == "2026-08"
    forced = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            card.id, D(2026, 7, 26), TransactionKind.EXPENSE, 100, "y", statement_month=YM(2026, 7)
        )
    )
    assert str(uow.statements.get(forced.statement_id or "").month) == "2026-07"  # type: ignore[union-attr]
    with pytest.raises(DomainError) as exc:
        RegisterTransaction(uow).execute(
            RegisterTransactionCommand(
                checking.id,
                D(2026, 7, 26),
                TransactionKind.EXPENSE,
                100,
                "z",
                statement_month=YM(2026, 7),
            )
        )
    assert codes(exc) == "STATEMENT_ONLY_FOR_CARDS"
    plain = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(checking.id, D(2026, 7, 26), TransactionKind.EXPENSE, 100, "w")
    )
    assert plain.statement_id is None


def test_cards_have_no_informed_balance(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    from financas.application.queries.balances import ListAccountBalances
    from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand

    with pytest.raises(DomainError) as exc:
        RecordBalance(uow).execute(RecordBalanceCommand(card.id, D(2026, 7, 1), 100))
    assert codes(exc) == "BALANCE_NOT_FOR_CARDS"
    assert {b.account_id for b in ListAccountBalances(uow).execute(D(2026, 7, 1))} == {checking.id}


def test_card_account_creation_rules(uow: MemoryUnitOfWork, institution: object) -> None:
    from financas.application.use_cases.catalog import CreateAccount, CreateAccountCommand
    from financas.domain.models import AccountKind

    inst = uow.institutions.list_all()[0]
    with pytest.raises(DomainError) as exc:
        CreateAccount(uow).execute(CreateAccountCommand(AccountKind.CREDIT_CARD, inst.id, "Cartão"))
    assert codes(exc) == "CARD_DAYS_REQUIRED"
    with pytest.raises(DomainError) as exc:
        CreateAccount(uow).execute(
            CreateAccountCommand(AccountKind.CHECKING, inst.id, "CC", closing_day=25, due_day=5)
        )
    assert codes(exc) == "CARD_FIELDS_ONLY_FOR_CARDS"
    created = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CREDIT_CARD, inst.id, "Cartão", closing_day=16, due_day=23)
    )
    assert (created.closing_day, created.due_day, created.credit_limit_cents) == (16, 23, None)
