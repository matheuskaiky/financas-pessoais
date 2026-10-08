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
    DeleteInstallmentPlan,
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
    UpdateInvoicePayment,
    UpdatePaymentCommand,
)
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    Institution,
    StatementStatus,
    TransactionKind,
)
from financas.domain.money import YearMonth
from financas.domain.services.card_cycle import AssignmentReason, last_day_in_statement
from financas.domain.services.countdown import Countdown, CountdownKind
from financas.domain.services.statements import LimitAlert

D = dt.date
YM = YearMonth
CLOCK = FixedClock(D(2026, 7, 1))


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
        (D(2026, 7, 24), YM(2026, 7), D(2026, 7, 25), D(2026, 8, 5)),
        (D(2026, 7, 25), YM(2026, 8), D(2026, 8, 25), D(2026, 9, 5)),  # the closing day: next one
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
    # installment 1 is dated on the purchase, the others on the last day of their statement
    assert [e.posted_on for e in entries] == [D(2026, 7, 26), D(2026, 9, 23), D(2026, 10, 24)]
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
    stored = [uow.statements.get(e.statement_id or "") for e in entries]
    assert all(
        s is not None and e.posted_on == last_day_in_statement(s.closing_date)
        for s, e in zip(stored, entries, strict=True)
    )


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


def dates_by_month(uow: MemoryUnitOfWork) -> dict[str, tuple[dt.date, dt.date]]:
    return {str(s.month): (s.closing_date, s.due_date) for s in uow.statements.items.values()}


def test_statements_follow_the_settings_until_they_close(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    # card: due on day 5, closing 11 days before. Five installments from 07-26: Aug to Dec.
    buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=5)
    SetCardSettings(uow, FixedClock(D(2026, 9, 1))).execute(
        card.id, closing_days_before_due=7, due_day=17, credit_limit_cents=None
    )
    dates = dates_by_month(uow)
    assert dates["2026-08"] == (D(2026, 8, 25), D(2026, 9, 5))  # closed on 08-25: frozen
    assert dates["2026-09"] == (D(2026, 9, 10), D(2026, 9, 17))  # still open: follows the card
    assert dates["2026-10"] == (D(2026, 10, 10), D(2026, 10, 17))  # created in advance: follows
    assert dates["2026-12"] == (D(2026, 12, 10), D(2026, 12, 17))


def test_a_statement_is_frozen_from_its_closing_date_on(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    # on 09-24 the September statement closes: frozen even though today is its closing date
    SetCardSettings(uow, FixedClock(D(2026, 9, 24))).execute(card.id, 7, 17, None)
    dates = dates_by_month(uow)
    assert dates["2026-09"] == (D(2026, 9, 24), D(2026, 10, 5))
    assert dates["2026-10"] == (D(2026, 10, 10), D(2026, 10, 17))


def test_hand_edited_dates_survive_a_settings_change(uow: MemoryUnitOfWork, card: Account) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    october = uow.statements.get_by_card_month(card.id, YM(2026, 10))
    assert october
    SetStatementDates(uow).execute(october.id, D(2026, 10, 28), D(2026, 11, 9))
    SetCardSettings(uow, FixedClock(D(2026, 9, 1))).execute(card.id, 7, 17, None)
    dates = dates_by_month(uow)
    assert dates["2026-10"] == (D(2026, 10, 28), D(2026, 11, 9))  # the user's dates stay
    assert dates["2026-09"] == (D(2026, 9, 10), D(2026, 9, 17))  # the others follow


def test_a_new_closing_date_already_in_the_past_is_not_applied(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 26), total_cents=30_100, installments=3)
    # on 09-15 September is open (closes 09-24); the new recipe would close it on 09-10
    SetCardSettings(uow, FixedClock(D(2026, 9, 15))).execute(card.id, 7, 17, None)
    dates = dates_by_month(uow)
    assert dates["2026-09"] == (D(2026, 9, 24), D(2026, 10, 5))  # what was in force stays
    assert dates["2026-10"] == (D(2026, 10, 10), D(2026, 10, 17))


def test_purchases_use_the_stored_dates_of_existing_statements(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 20), total_cents=100)  # July: closes 07-25
    SetCardSettings(uow, FixedClock(D(2026, 9, 1))).execute(card.id, 7, 17, None)
    # July is closed and frozen: with the new recipe July would close on 07-10 and 07-24 would be
    # late, but the stored dates decide
    inside = buy(uow, card, purchased_on=D(2026, 7, 24), total_cents=100)
    assert inside.preview.assignment.month == YM(2026, 7)
    on_the_day = buy(uow, card, purchased_on=D(2026, 7, 25), total_cents=100)
    assert on_the_day.preview.assignment.reason is AssignmentReason.ON_CLOSING_DAY
    assert on_the_day.preview.assignment.month == YM(2026, 8)  # August does not exist yet: recipe
    assert on_the_day.preview.assignment.closing_date == D(2026, 8, 10)


def test_card_settings_validation(uow: MemoryUnitOfWork, card: Account, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        SetCardSettings(uow, CLOCK).execute(card.id, 40, 5, None)
    assert codes(exc) == "INVALID_DAYS_BEFORE_DUE"
    with pytest.raises(DomainError) as exc:
        SetCardSettings(uow, CLOCK).execute(card.id, 7, 40, None)
    assert codes(exc) == "INVALID_CARD_DAY"
    with pytest.raises(DomainError) as exc:
        SetCardSettings(uow, CLOCK).execute(checking.id, 25, 5, None)
    assert codes(exc) == "CARD_REQUIRED"


# --- statements, payment and limit (9.5) ---


def september_statement(uow: MemoryUnitOfWork, card: Account) -> str:
    """A statement closing 2026-09-24 with R$ 100,00 + R$ 50,00 (due 2026-10-05)."""
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
        (D(2026, 8, 24), StatementStatus.FUTURE),  # August is still taking purchases
        (D(2026, 8, 25), StatementStatus.OPEN),  # September opens when August closes
        (D(2026, 9, 23), StatementStatus.OPEN),
        (D(2026, 9, 24), StatementStatus.CLOSED),  # closing day (due 10-05, 11 days after)
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
    clock = FixedClock(D(2026, 10, 3))  # payments are never dated after today
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
    assert entry.posted_on == D(2026, 9, 23) and entry.description == "Diferença"
    assert entry.category_id is not None
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
    SetCardSettings(uow, CLOCK).execute(card.id, 11, 5, 10_000)
    buy(uow, card, purchased_on=D(2026, 7, 1), total_cents=8_500)
    assert cards_at(uow, D(2026, 7, 2)).cards[0].usage.alert is LimitAlert.WARNING
    buy(uow, card, purchased_on=D(2026, 7, 2), total_cents=1_500)
    assert cards_at(uow, D(2026, 7, 2)).cards[0].usage.alert is LimitAlert.EXCEEDED
    SetCardSettings(uow, CLOCK).execute(card.id, 11, 5, None)
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
    MoveEntryToStatement(uow, FixedClock(D(2026, 8, 1))).execute(entry.id, YM(2026, 7))
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
            CreateAccountCommand(
                AccountKind.CHECKING, inst.id, "CC", closing_days_before_due=7, due_day=5
            )
        )
    assert codes(exc) == "CARD_FIELDS_ONLY_FOR_CARDS"
    created = CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.CREDIT_CARD, inst.id, "Cartão", closing_days_before_due=7, due_day=23
        )
    )
    assert (created.closing_days_before_due, created.due_day, created.credit_limit_cents) == (
        7,
        23,
        None,
    )


def test_a_payment_from_an_untracked_account_creates_only_the_card_leg(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    september = september_statement(uow, card)
    legs = PayStatement(uow, FixedClock(D(2026, 10, 1))).execute(
        PayStatementCommand(september, None, D(2026, 10, 1), 4_000, "Pagamento")
    )
    (leg,) = legs
    assert (leg.account_id, leg.amount_cents, leg.statement_id) == (card.id, 4_000, september)
    view = cards_at(uow, D(2026, 10, 1)).cards[0].statements[0]
    assert (view.paid_cents, view.outstanding_cents) == (4_000, 11_000)


# --- card face telemetry: open balance and day counts ---


def telemetry(uow: MemoryUnitOfWork, card: Account, today: dt.date):
    overview = ListCards(uow, FixedClock(today)).execute()
    (view,) = [v for v in overview.cards if v.account.id == card.id]
    assert view.telemetry
    return view.telemetry


def test_telemetry_open_balance_and_days_to_closing_and_due(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=18_000)
    buy(uow, card, purchased_on=D(2026, 7, 12), total_cents=2_500, description="Outra")
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(  # a refund on the card lowers the running total
            card.id, D(2026, 7, 15), TransactionKind.REFUND, 1_000, "Estorno"
        )
    )
    t = telemetry(uow, card, D(2026, 7, 20))  # closes 07-25, due 08-05
    assert t.open_balance_cents == 18_000 + 2_500 - 1_000
    assert t.closing == Countdown(CountdownKind.IN_DAYS, 5)
    assert t.due == Countdown(CountdownKind.IN_DAYS, 16)
    assert t.pending_due is None and t.pending_month is None


def test_telemetry_the_day_before_closing_says_tomorrow(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=500)
    t = telemetry(uow, card, D(2026, 7, 24))
    assert t.closing == Countdown(CountdownKind.TOMORROW, 0)
    assert t.due == Countdown(CountdownKind.IN_DAYS, 12)


def test_telemetry_on_the_closing_day_the_next_statement_is_the_open_one(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    """9.3: the closing date is the first day of the next cycle, so the closed statement is
    reported apart (closed, owing, due in 11 days) and the open one is August's."""
    buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=4_000)
    buy(uow, card, purchased_on=D(2026, 7, 25), total_cents=700, description="No fechamento")
    t = telemetry(uow, card, D(2026, 7, 25))
    assert t.open_balance_cents == 700  # only the purchase that went to the next statement
    assert t.closing == Countdown(CountdownKind.IN_DAYS, 31)  # 08-25
    assert t.pending_month == YM(2026, 7)
    assert t.pending_due == Countdown(CountdownKind.IN_DAYS, 11)  # 08-05


def test_telemetry_closed_statement_owing_and_overdue(uow: MemoryUnitOfWork, card: Account) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=4_000)
    t = telemetry(uow, card, D(2026, 8, 10))  # July's statement was due on 08-05
    assert t.pending_due == Countdown(CountdownKind.PAST, 5)
    assert t.open_balance_cents == 0 and t.closing == Countdown(CountdownKind.IN_DAYS, 15)


def test_telemetry_without_any_purchase_uses_the_card_settings(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    t = telemetry(uow, card, D(2026, 7, 20))
    assert t.open_balance_cents == 0
    assert t.closing == Countdown(CountdownKind.IN_DAYS, 5)
    assert t.due == Countdown(CountdownKind.IN_DAYS, 16)


def test_telemetry_paid_statement_is_not_pending(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    entry = buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=4_000).transactions[0]
    assert entry.statement_id
    today = D(2026, 7, 28)
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(entry.statement_id, checking.id, today)
    )
    assert telemetry(uow, card, today).pending_due is None


# --- deleting what is pending of an installment plan ---


def test_delete_installment_plan_with_nothing_paid_removes_plan_and_entries(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = buy(uow, card, purchased_on=D(2026, 7, 10), installments=3, total_cents=9_000).plan
    assert plan
    before = ListCards(uow, CLOCK).execute().cards[0].usage.committed_cents
    result = DeleteInstallmentPlan(uow, CLOCK).execute(plan.id)
    assert (result.deleted, result.kept, result.plan_removed) == (3, 0, True)
    assert uow.transactions.list_by_plan(plan.id) == [] and uow.plans.get(plan.id) is None
    after = ListCards(uow, CLOCK).execute().cards[0].usage.committed_cents
    assert (before, after) == (9_000, 0)  # the limit is computed from the entries: it came back


def test_delete_installment_plan_keeps_installments_on_paid_statements(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    plan = buy(uow, card, purchased_on=D(2026, 7, 10), installments=3, total_cents=9_000).plan
    assert plan
    first = uow.transactions.list_by_plan(plan.id)[0]
    assert first.statement_id
    today = D(2026, 7, 28)  # July's statement closed on 07-25
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(first.statement_id, checking.id, today)
    )
    result = DeleteInstallmentPlan(uow, FixedClock(today)).execute(plan.id)
    assert (result.deleted, result.kept, result.plan_removed) == (2, 1, False)
    left = uow.transactions.list_by_plan(plan.id)
    assert [t.installment_number for t in left] == [1]  # the paid one is history
    assert uow.plans.get(plan.id) == plan  # and so is the plan record
    with pytest.raises(DomainError) as exc:  # nothing pending is left
        DeleteInstallmentPlan(uow, FixedClock(today)).execute(plan.id)
    assert codes(exc) == "NOTHING_TO_DELETE"


def test_delete_installment_plan_is_atomic_and_checks_the_plan_exists(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plan = buy(uow, card, purchased_on=D(2026, 7, 10), installments=2, total_cents=2_000).plan
    assert plan
    with pytest.raises(DomainError) as exc:
        DeleteInstallmentPlan(uow, CLOCK).execute("nope")
    assert codes(exc) == "NOT_FOUND"
    uow.statements.items.clear()  # a broken reference fails the whole operation
    with pytest.raises(DomainError):
        DeleteInstallmentPlan(uow, CLOCK).execute(plan.id)
    assert len(uow.transactions.list_by_plan(plan.id)) == 2


# --- editing a statement payment ---

PAY_DAY = D(2026, 7, 28)  # July's statement closed on 07-25 and is unpaid
PAY_CLOCK = FixedClock(PAY_DAY)


def pay_setup(uow: MemoryUnitOfWork, card: Account, checking: Account, total: int = 4_000):
    entry = buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=total).transactions[0]
    assert entry.statement_id
    legs = PayStatement(uow, PAY_CLOCK).execute(
        PayStatementCommand(entry.statement_id, checking.id, PAY_DAY, description="Pagamento")
    )
    debit = next(t for t in legs if t.account_id == checking.id)
    credit = next(t for t in legs if t.account_id == card.id)
    return entry.statement_id, debit, credit


def status_of(uow: MemoryUnitOfWork, statement_id: str) -> StatementStatus:
    from financas.application.queries.cards import statement_view

    statement = uow.statements.get(statement_id)
    assert statement
    return statement_view(uow, statement, PAY_DAY).status


def update_payment(uow: MemoryUnitOfWork, entry_id: str, **overrides: object):
    values: dict[str, object] = {"paid_on": PAY_DAY, "amount_cents": 4_000}
    values.update(overrides)
    return UpdateInvoicePayment(uow, PAY_CLOCK).execute(
        UpdatePaymentCommand(entry_id, **values)  # type: ignore[arg-type]
    )


def test_update_payment_moves_both_legs_together(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    statement_id, debit, credit = pay_setup(uow, card, checking)
    result = update_payment(
        uow, credit.id, paid_on=D(2026, 7, 27), amount_cents=3_500, notes="  conferido "
    )
    new_debit = uow.transactions.get(debit.id)
    new_credit = uow.transactions.get(credit.id)
    assert new_debit and new_credit
    assert (new_debit.amount_cents, new_credit.amount_cents) == (-3_500, 3_500)  # opposite, equal
    assert new_debit.posted_on == new_credit.posted_on == D(2026, 7, 27)
    assert new_debit.notes == new_credit.notes == "conferido"
    assert new_debit.transfer_id == new_credit.transfer_id == debit.transfer_id
    assert new_credit.statement_id == statement_id and new_debit.statement_id is None
    assert {t.id for t in result.legs} == {debit.id, credit.id}
    assert [t.id for t in uow.transactions.list_by_transfer(debit.transfer_id or "")] != []


def test_either_leg_can_name_the_payment(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    _, debit, credit = pay_setup(uow, card, checking)
    update_payment(uow, debit.id, amount_cents=1_000)  # the checking leg
    assert uow.transactions.items[credit.id].amount_cents == 1_000
    assert uow.transactions.items[debit.id].amount_cents == -1_000


def test_a_smaller_payment_takes_the_statement_back_from_paid_to_closed(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    statement_id, _, credit = pay_setup(uow, card, checking)
    assert status_of(uow, statement_id) is StatementStatus.PAID
    reduced = update_payment(uow, credit.id, amount_cents=2_500)
    assert (reduced.status_before, reduced.status_after) == (
        StatementStatus.PAID,
        StatementStatus.CLOSED,
    )
    assert status_of(uow, statement_id) is StatementStatus.CLOSED
    # the part still owed can be paid again, and the entries of the statement are editable again
    assert PayStatement(uow, PAY_CLOCK).execute(
        PayStatementCommand(statement_id, checking.id, PAY_DAY)
    )[0].amount_cents in {-1_500, 1_500}
    assert status_of(uow, statement_id) is StatementStatus.PAID
    # a bigger payment keeps it paid
    raised = update_payment(uow, credit.id, amount_cents=4_000)
    assert (raised.status_before, raised.status_after) == (
        StatementStatus.PAID,
        StatementStatus.PAID,
    )


def test_reopened_statement_can_be_edited_again_and_limit_follows(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    from financas.application.use_cases.transactions import (
        UpdateTransaction,
        UpdateTransactionCommand,
    )

    statement_id, _, credit = pay_setup(uow, card, checking)
    purchase = next(
        t for t in uow.transactions.list_by_statement(statement_id) if t.transfer_id is None
    )
    committed_paid = ListCards(uow, PAY_CLOCK).execute().cards[0].usage.committed_cents
    assert committed_paid == 0  # paid in full: the limit is back
    with pytest.raises(DomainError) as locked:  # paid statements are history
        UpdateTransaction(uow, PAY_CLOCK).execute(
            UpdateTransactionCommand(
                purchase.id,
                purchase.posted_on,
                4_100,
                purchase.description,
                acknowledge_closed=True,
            )
        )
    assert codes(locked) == "STATEMENT_ALREADY_PAID"
    update_payment(uow, credit.id, amount_cents=1_000)
    assert ListCards(uow, PAY_CLOCK).execute().cards[0].usage.committed_cents == 3_000
    reopened = UpdateTransaction(uow, PAY_CLOCK).execute(
        UpdateTransactionCommand(
            purchase.id, purchase.posted_on, 4_100, purchase.description, acknowledge_closed=True
        )
    )
    assert reopened.amount_cents == -4_100


def test_changing_the_source_account_restores_one_balance_and_charges_the_other(
    uow: MemoryUnitOfWork, card: Account, checking: Account, institution: Institution
) -> None:
    from financas.application.queries.balances import ListAccountBalances
    from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
    from financas.application.use_cases.catalog import CreateAccount, CreateAccountCommand

    other = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Segunda")
    )
    for account in (checking, other):
        RecordBalance(uow).execute(RecordBalanceCommand(account.id, D(2026, 7, 1), 100_000))
    _, debit, credit = pay_setup(uow, card, checking)

    def balances() -> dict[str, int | None]:
        rows = ListAccountBalances(uow).execute(PAY_DAY)
        return {b.account_id: b.balance_cents for b in rows}

    assert balances() == {checking.id: 96_000, other.id: 100_000}
    update_payment(uow, credit.id, from_account_id=other.id)
    assert balances() == {checking.id: 100_000, other.id: 96_000}  # restored / charged
    assert uow.transactions.items[debit.id].account_id == other.id
    update_payment(uow, credit.id, amount_cents=3_000)  # keeps the origin when not told
    assert balances() == {checking.id: 100_000, other.id: 97_000}


def test_a_payment_from_an_untracked_account_can_gain_or_lose_its_debit(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    statement_id, debit, credit = pay_setup(uow, card, checking)
    update_payment(uow, credit.id, from_account_id=None)
    assert uow.transactions.get(debit.id) is None  # the checking leg went away
    assert uow.transactions.get(credit.id).amount_cents == 4_000  # type: ignore[union-attr]
    assert status_of(uow, statement_id) is StatementStatus.PAID  # the card side still pays it
    update_payment(uow, credit.id, from_account_id=checking.id, amount_cents=3_000)
    legs = uow.transactions.list_by_transfer(credit.transfer_id or "")
    assert sorted(t.amount_cents for t in legs) == [-3_000, 3_000]
    assert {t.account_id for t in legs} == {checking.id, card.id}


def test_update_payment_validations_and_atomicity(
    uow: MemoryUnitOfWork,
    card: Account,
    checking: Account,
    savings: Account,
    institution: Institution,
) -> None:
    from financas.application.use_cases.catalog import (
        CreateAccount,
        CreateAccountCommand,
        SetAccountActive,
    )
    from financas.application.use_cases.transactions import (
        RegisterTransfer,
        RegisterTransferCommand,
    )

    _, debit, credit = pay_setup(uow, card, checking)
    for overrides, code in [
        ({"amount_cents": 0}, "AMOUNT_NOT_POSITIVE"),
        ({"amount_cents": -5}, "AMOUNT_NOT_POSITIVE"),
        ({"from_account_id": savings.id}, "ACCOUNT_KIND_NOT_ALLOWED"),
        ({"from_account_id": card.id}, "ACCOUNT_KIND_NOT_ALLOWED"),
        ({"from_account_id": "nope"}, "NOT_FOUND"),
    ]:
        with pytest.raises(DomainError) as exc:
            update_payment(uow, credit.id, **overrides)
        assert codes(exc) == code
    retired = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Antiga")
    )
    SetAccountActive(uow).execute(retired.id, False)
    with pytest.raises(DomainError) as inactive:
        update_payment(uow, credit.id, from_account_id=retired.id)
    assert codes(inactive) == "ACCOUNT_INACTIVE"
    # nothing was written by any of the failures
    assert uow.transactions.get(debit.id).amount_cents == -4_000  # type: ignore[union-attr]
    assert uow.transactions.get(credit.id).amount_cents == 4_000  # type: ignore[union-attr]
    assert uow.transactions.get(debit.id).account_id == checking.id  # type: ignore[union-attr]
    # only payments: an own-account transfer, a purchase and an unknown id are refused
    own = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, PAY_DAY, 500)
    )
    purchase = next(t for t in uow.transactions.items.values() if t.transfer_id is None)
    for entry_id in (own[0].id, purchase.id):
        with pytest.raises(DomainError) as refused:
            update_payment(uow, entry_id)
        assert codes(refused) == "NOT_A_STATEMENT_PAYMENT"
    with pytest.raises(DomainError) as missing:
        update_payment(uow, "nope")
    assert codes(missing) == "NOT_FOUND"


# --- payment date bounds (9.5): previous statement's due date .. today ---

BOUNDS_TODAY = D(2026, 10, 3)
BOUNDS_CLOCK = FixedClock(BOUNDS_TODAY)


def two_statements(uow: MemoryUnitOfWork, card: Account) -> tuple[str, str]:
    """August (closes 08-25, due 09-05; the first) and September (closes 09-24, due 10-05)."""
    (august,) = buy(uow, card, purchased_on=D(2026, 8, 10), total_cents=10_000).transactions
    return august.statement_id or "", september_statement(uow, card)


def pay_on(uow: MemoryUnitOfWork, statement_id: str, account: Account, day: dt.date, **kw: object):
    return PayStatement(uow, BOUNDS_CLOCK).execute(
        PayStatementCommand(statement_id, account.id, day, 1_000, **kw)  # type: ignore[arg-type]
    )


def test_payment_bounds_are_the_previous_due_date_and_today(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    from financas.application.queries.cards import payment_date_boundaries

    august, september = two_statements(uow, card)
    statement = uow.statements.get(september)
    assert statement
    later = payment_date_boundaries(uow, statement, BOUNDS_TODAY)
    assert (later.min_date, later.max_date, later.from_previous_due) == (
        D(2026, 9, 5),  # August's due date
        BOUNDS_TODAY,
        True,
    )
    first = uow.statements.get(august)
    assert first
    opening = payment_date_boundaries(uow, first, BOUNDS_TODAY)
    # no previous statement: the cycle opens when July's closes (due 08-05, closing 11 days before)
    assert (opening.min_date, opening.from_previous_due) == (D(2026, 7, 25), False)


def test_a_future_payment_is_forbidden(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    _, september = two_statements(uow, card)
    before = dict(uow.transactions.items)
    with pytest.raises(DomainError) as exc:
        pay_on(uow, september, checking, D(2026, 10, 4))  # tomorrow
    assert codes(exc) == "FUTURE_PAYMENT_FORBIDDEN"
    assert uow.transactions.items == before


def test_a_payment_before_the_previous_due_date_is_refused(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    _, september = two_statements(uow, card)
    with pytest.raises(DomainError) as exc:
        pay_on(uow, september, checking, D(2026, 9, 4))  # the day before August's due date
    assert codes(exc) == "PAYMENT_DATE_BEFORE_PREVIOUS_DUE"
    assert exc.value.params["min_date"] == "2026-09-05"


@pytest.mark.parametrize("day", [D(2026, 9, 5), D(2026, 10, 3)])
def test_a_payment_exactly_on_the_boundaries_succeeds(
    uow: MemoryUnitOfWork, card: Account, checking: Account, day: dt.date
) -> None:
    _, september = two_statements(uow, card)
    legs = pay_on(uow, september, checking, day)
    assert {leg.posted_on for leg in legs} == {day}


def test_the_first_statement_opens_with_its_cycle(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    august, _ = two_statements(uow, card)
    with pytest.raises(DomainError) as exc:
        pay_on(uow, august, checking, D(2026, 7, 24))
    assert codes(exc) == "PAYMENT_DATE_BEFORE_PREVIOUS_DUE"
    assert pay_on(uow, august, checking, D(2026, 7, 25))  # the opening day itself


def test_editing_a_payment_is_bound_by_the_same_dates(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    _, september = two_statements(uow, card)
    credit = next(
        t for t in pay_on(uow, september, checking, D(2026, 10, 1)) if t.account_id == card.id
    )

    def edit(day: dt.date, amount: int = 1_000):
        return UpdateInvoicePayment(uow, BOUNDS_CLOCK).execute(
            UpdatePaymentCommand(credit.id, day, amount)
        )

    with pytest.raises(DomainError) as exc:
        edit(D(2026, 10, 4))
    assert codes(exc) == "FUTURE_PAYMENT_FORBIDDEN"
    with pytest.raises(DomainError) as exc:
        edit(D(2026, 9, 4))
    assert codes(exc) == "PAYMENT_DATE_BEFORE_PREVIOUS_DUE"
    assert uow.transactions.items[credit.id].posted_on == D(2026, 10, 1)  # nothing moved
    edit(D(2026, 9, 5))  # both boundaries are valid
    edit(BOUNDS_TODAY)
    assert {t.posted_on for t in uow.transactions.list_by_transfer(credit.transfer_id or "")} == {
        BOUNDS_TODAY
    }


def test_a_payment_with_an_old_date_can_still_change_its_amount(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    from dataclasses import replace

    _, september = two_statements(uow, card)
    credit = next(
        t for t in pay_on(uow, september, checking, D(2026, 10, 1)) if t.account_id == card.id
    )
    uow.transactions.update(replace(credit, posted_on=D(2026, 8, 1)))  # recorded before the rule
    UpdateInvoicePayment(uow, BOUNDS_CLOCK).execute(
        UpdatePaymentCommand(credit.id, D(2026, 8, 1), 2_000)  # the date is not what changes
    )
    assert uow.transactions.items[credit.id].amount_cents == 2_000


# --- committed limit: the unpaid part of every statement, never netted across statements ---


def test_committed_equals_the_open_statement_when_nothing_else_is_pending(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=6_456)
    view = ListCards(uow, FixedClock(D(2026, 7, 20))).execute().cards[0]
    assert view.telemetry and view.telemetry.open_balance_cents == 6_456
    assert view.usage.committed_cents == 6_456  # "Fatura atual" and "Comprometido" agree
    assert round(view.usage.percent or 0, 2) == round(6_456 / 1_200_000 * 100, 2)


def test_a_credit_on_an_older_statement_does_not_hide_the_open_charges(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    """The Banco Inter case: the sum of every entry was 0 (credit above, charges below), so the
    limit showed R$ 0,00 committed next to an open statement with charges."""
    buy(uow, card, purchased_on=D(2026, 6, 10), total_cents=1_000)  # June statement
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            card.id, D(2026, 6, 12), TransactionKind.REFUND, 5_000, "Estorno em excesso"
        )
    )  # June ends with a credit of R$ 40,00
    buy(uow, card, purchased_on=D(2026, 7, 10), total_cents=6_456)  # July: open
    today = FixedClock(D(2026, 7, 20))
    view = ListCards(uow, today).execute().cards[0]
    assert view.telemetry and view.telemetry.open_balance_cents == 6_456
    assert view.usage.committed_cents == 6_456  # the credit stays on its own statement


def test_future_installments_are_committed_with_the_open_statement(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy(uow, card, purchased_on=D(2026, 7, 10), installments=3, total_cents=9_000)
    buy(uow, card, purchased_on=D(2026, 7, 11), total_cents=1_000)
    view = ListCards(uow, FixedClock(D(2026, 7, 20))).execute().cards[0]
    assert view.usage.committed_cents == 10_000
    assert view.telemetry and view.telemetry.open_balance_cents == 4_000  # 3,000 + 1,000
