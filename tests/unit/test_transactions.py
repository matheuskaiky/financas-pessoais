import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    PurchaseResult,
    RegisterCardPurchase,
)
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    SetAccountActive,
)
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    GetEntryEditState,
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
    SuggestCategory,
    UpdateTransaction,
    UpdateTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    PaymentMethod,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth

K = TransactionKind
PM = PaymentMethod
D = dt.date


def register(uow: MemoryUnitOfWork, account: Account, **overrides: object):
    values: dict[str, object] = {
        "account_id": account.id,
        "posted_on": D(2026, 7, 10),
        "kind": K.EXPENSE,
        "amount_cents": 12_345,
        "description": "Padaria São João",
    }
    values.update(overrides)
    return RegisterTransaction(uow).execute(RegisterTransactionCommand(**values))  # type: ignore[arg-type]


def category_id(uow: MemoryUnitOfWork, slug: str) -> str:
    category = uow.categories.get_by_slug(slug)
    assert category
    return category.id


def test_expense_is_stored_negative_with_search_key(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    t = register(uow, checking, category_id=category_id(uow, "food"))
    assert t.amount_cents == -12_345
    assert t.kind is K.EXPENSE
    assert t.description == "Padaria São João"
    assert t.description_search == "padaria sao joao"
    assert uow.transactions.get(t.id) == t


def test_income_and_refund_are_stored_positive(uow: MemoryUnitOfWork, checking: Account) -> None:
    income = register(uow, checking, kind=K.INCOME, description="Salário")
    refund = register(uow, checking, kind=K.REFUND, description="Estorno loja")
    assert income.amount_cents == 12_345 and refund.amount_cents == 12_345


@pytest.mark.parametrize(
    ("kind", "slug"),
    [
        (K.EXPENSE, "uncategorized"),
        (K.INCOME, "other_income"),
        (K.REFUND, "refund"),
    ],
)
def test_default_category_by_kind(
    uow: MemoryUnitOfWork, checking: Account, kind: TransactionKind, slug: str
) -> None:
    t = register(uow, checking, kind=kind)
    assert t.category_id == category_id(uow, slug)


def test_category_must_match_kind(uow: MemoryUnitOfWork, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, kind=K.INCOME, category_id=category_id(uow, "food"))
    assert exc.value.code == "CATEGORY_KIND_MISMATCH"
    assert uow.transactions.items == {}


@pytest.mark.parametrize("amount", [0, -5])
def test_amount_must_be_positive(uow: MemoryUnitOfWork, checking: Account, amount: int) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, amount_cents=amount)
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"


def test_description_is_required(uow: MemoryUnitOfWork, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, description="   ")
    assert exc.value.code == "EMPTY_DESCRIPTION"


def test_transfers_do_not_go_through_register_transaction(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, kind=K.TRANSFER)
    assert exc.value.code == "USE_TRANSFER_FOR_TRANSFERS"


def test_unknown_account_inactive_account_and_card_are_rejected(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        RegisterTransaction(uow).execute(
            RegisterTransactionCommand("nope", D(2026, 7, 1), K.EXPENSE, 100, "x")
        )
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(DomainError) as exc:
        register(uow, card, kind=K.INCOME)  # income never lands on a card
    assert exc.value.code == "ACCOUNT_KIND_NOT_ALLOWED"
    SetAccountActive(uow).execute(checking.id, False)
    with pytest.raises(DomainError) as exc:
        register(uow, checking)
    assert exc.value.code == "ACCOUNT_INACTIVE"


def test_transfer_between_tracked_accounts_creates_two_opposite_legs(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 5), 50_000, "Aporte")
    )
    assert len(legs) == 2
    out, into = legs
    assert (out.account_id, out.amount_cents) == (checking.id, -50_000)
    assert (into.account_id, into.amount_cents) == (savings.id, 50_000)
    assert out.transfer_id and out.transfer_id == into.transfer_id
    assert {leg.kind for leg in legs} == {K.TRANSFER}
    assert out.category_id == category_id(uow, "transfer")


@pytest.mark.parametrize(("side", "sign"), [("from", -1), ("to", 1)])
def test_transfer_to_or_from_an_untracked_account_has_one_leg(
    uow: MemoryUnitOfWork, checking: Account, side: str, sign: int
) -> None:
    cmd = RegisterTransferCommand(
        checking.id if side == "from" else None,
        checking.id if side == "to" else None,
        D(2026, 7, 5),
        1_000,
    )
    legs = RegisterTransfer(uow).execute(cmd)
    assert len(legs) == 1 and legs[0].amount_cents == sign * 1_000


def test_transfer_rules(uow: MemoryUnitOfWork, checking: Account, card: Account) -> None:
    use_case = RegisterTransfer(uow)
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(None, None, D(2026, 7, 5), 100))
    assert exc.value.code == "TRANSFER_NEEDS_ACCOUNT"
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(checking.id, checking.id, D(2026, 7, 5), 100))
    assert exc.value.code == "TRANSFER_SAME_ACCOUNT"
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(checking.id, None, D(2026, 7, 5), 0))
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"
    with pytest.raises(DomainError) as exc:
        use_case.execute(RegisterTransferCommand(checking.id, card.id, D(2026, 7, 5), 100))
    assert exc.value.code == "ACCOUNT_KIND_NOT_ALLOWED"


def test_a_failing_second_leg_leaves_no_first_leg(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    with pytest.raises(DomainError):
        RegisterTransfer(uow).execute(
            RegisterTransferCommand(checking.id, card.id, D(2026, 7, 5), 100)
        )
    assert uow.transactions.items == {}


def test_deleting_one_transfer_leg_deletes_both(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 5), 100)
    )
    assert DeleteTransaction(uow, FixedClock()).execute(legs[0].id) == 2
    assert uow.transactions.items == {}


def test_deleting_a_plain_entry_and_unknown_id(uow: MemoryUnitOfWork, checking: Account) -> None:
    t = register(uow, checking)
    assert DeleteTransaction(uow, FixedClock()).execute(t.id) == 1
    with pytest.raises(DomainError) as exc:
        DeleteTransaction(uow, FixedClock()).execute(t.id)
    assert exc.value.code == "NOT_FOUND"


def test_suggests_the_last_category_for_the_same_description(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    register(uow, checking, posted_on=D(2026, 6, 1), category_id=category_id(uow, "food"))
    register(uow, checking, posted_on=D(2026, 7, 1), category_id=category_id(uow, "groceries"))
    suggest = SuggestCategory(uow)
    # same normalized description, different case and accents
    result = suggest.execute("PADARIA SAO JOAO", K.EXPENSE)
    assert result and result.slug == "groceries"
    assert suggest.execute("Outra coisa", K.EXPENSE) is None
    assert suggest.execute("Padaria São João", K.INCOME) is None
    assert suggest.execute("   ", K.EXPENSE) is None


# --- editing an entry ---


TODAY = D(2026, 7, 25)


def edit(uow: MemoryUnitOfWork, entry_id: str, today: dt.date = TODAY, **overrides: object):
    values: dict[str, object] = {
        "transaction_id": entry_id,
        "posted_on": D(2026, 7, 10),
        "amount_cents": 12_345,
        "description": "Padaria São João",
    }
    values.update(overrides)
    return UpdateTransaction(uow, FixedClock(today)).execute(
        UpdateTransactionCommand(**values)  # type: ignore[arg-type]
    )


def test_edit_changes_amount_date_category_and_text(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = register(uow, checking, category_id=category_id(uow, "food"))
    groceries = category_id(uow, "groceries")
    updated = edit(
        uow,
        entry.id,
        posted_on=D(2026, 7, 12),
        amount_cents=9_990,
        description="  Ação & Café ",
        category_id=groceries,
        notes="conferido",
    )
    assert updated.id == entry.id
    assert updated.amount_cents == -9_990  # the sign still follows the kind
    assert updated.posted_on == D(2026, 7, 12)
    assert updated.category_id == groceries
    assert updated.description == "Ação & Café"
    assert updated.description_search == "acao & cafe"
    assert updated.notes == "conferido"
    assert uow.transactions.get(entry.id) == updated


def test_edit_keeps_the_category_when_none_is_given(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = register(uow, checking, category_id=category_id(uow, "food"))
    assert edit(uow, entry.id).category_id == entry.category_id


def test_edit_income_stays_positive(uow: MemoryUnitOfWork, checking: Account) -> None:
    entry = register(uow, checking, kind=K.INCOME, description="Salário")
    assert edit(uow, entry.id, amount_cents=500_000, description="Salário").amount_cents == 500_000


def test_edit_validates_category_kind_amount_and_description(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = register(uow, checking)
    for overrides, code in [
        ({"category_id": category_id(uow, "salary")}, "CATEGORY_KIND_MISMATCH"),
        ({"amount_cents": 0}, "AMOUNT_NOT_POSITIVE"),
        ({"description": "  "}, "EMPTY_DESCRIPTION"),
    ]:
        with pytest.raises(DomainError) as exc:
            edit(uow, entry.id, **overrides)
        assert exc.value.code == code
    assert uow.transactions.get(entry.id) == entry


def test_edit_moves_between_checking_accounts_only(
    uow: MemoryUnitOfWork, checking: Account, savings: Account, card: Account
) -> None:
    other = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, checking.institution_id, "Segunda")
    )
    entry = register(uow, checking)
    assert edit(uow, entry.id, account_id=other.id).account_id == other.id
    for target in (card, savings):
        with pytest.raises(DomainError) as exc:
            edit(uow, entry.id, account_id=target.id)
        assert exc.value.code == "ACCOUNT_KIND_CHANGE_NOT_ALLOWED"


def test_edit_refuses_a_transfer(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 10), 1_000)
    )
    with pytest.raises(DomainError) as exc:
        edit(uow, legs[0].id, amount_cents=2_000)
    assert exc.value.code == "TRANSFER_NOT_EDITABLE"


def buy(uow: MemoryUnitOfWork, card: Account, **kw: object) -> PurchaseResult:
    values: dict[str, object] = {
        "account_id": card.id,
        "description": "Fone",
        "purchased_on": D(2026, 7, 10),
        "total_cents": 5_000,
    }
    values.update(kw)
    return RegisterCardPurchase(uow).execute(CardPurchaseCommand(**values))  # type: ignore[arg-type]


def card_purchase(uow: MemoryUnitOfWork, card: Account, **kw: object) -> Transaction:
    (entry,) = buy(uow, card, **kw).transactions
    return entry


EARLY = D(2026, 7, 1)  # every installment of the 07-10 purchase is still ahead


def installments(uow: MemoryUnitOfWork, card: Account) -> list[Transaction]:
    """3x R$ 30,00 bought on 07-10: statements 2026-07, 2026-08 and 2026-09."""
    result = buy(uow, card, installments=3, total_cents=9_000)
    return list(result.transactions)


def edit_installment(
    uow: MemoryUnitOfWork, entry: Transaction, today: dt.date = EARLY, **overrides: object
):
    values: dict[str, object] = {
        "posted_on": entry.posted_on,
        "amount_cents": -entry.amount_cents,
        "description": entry.description,
    }
    values.update(overrides)
    return edit(uow, entry.id, today=today, **values)


def snapshot(uow: MemoryUnitOfWork, entries: list[Transaction]) -> list[Transaction]:
    return [t for e in entries if (t := uow.transactions.get(e.id))]


def test_edit_installment_description_and_category_spread_across_the_plan(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    first, second, third = installments(uow, card)
    groceries = category_id(uow, "groceries")
    edit_installment(uow, second, description="Geladeira nova", category_id=groceries)
    for entry in snapshot(uow, [first, second, third]):
        assert (entry.description, entry.category_id) == ("Geladeira nova", groceries)
        assert entry.description_search == "geladeira nova"
    assert [t.amount_cents for t in snapshot(uow, [first, second, third])] == [-3_000] * 3
    (plan,) = uow.plans.items.values()
    assert (plan.description, plan.category_id) == ("Geladeira nova", groceries)


def test_edit_installment_without_propagation_changes_only_that_entry(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    first, second, third = installments(uow, card)
    edit_installment(
        uow,
        second,
        description="Só esta",
        category_id=category_id(uow, "groceries"),
        propagate_plan_metadata=False,
    )
    after = snapshot(uow, [first, second, third])
    assert after[0] == first and after[2] == third
    assert after[1].description == "Só esta"
    (plan,) = uow.plans.items.values()
    assert plan.description == first.description


def test_edit_installment_amount_and_notes_never_leave_the_entry(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    first, second, third = installments(uow, card)
    updated = edit_installment(uow, second, amount_cents=3_500, notes="ajustada")
    assert (updated.amount_cents, updated.notes) == (-3_500, "ajustada")
    after = snapshot(uow, [first, second, third])
    assert after[0] == first and after[2] == third  # the others are untouched
    assert sum(-t.amount_cents for t in after) == 3_000 + 3_500 + 3_000


def test_installment_propagation_skips_closed_and_paid_statements(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    first, second, third = installments(uow, card)
    today = D(2026, 8, 26)  # 2026-07 closed (07-25) and paid below; 2026-08 closed (08-25)
    assert first.statement_id
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(first.statement_id, checking.id, today)
    )
    # edit the last installment: the siblings on a paid and on a closed statement stay as they are
    edit_installment(uow, third, today=today, description="Nova")
    after = snapshot(uow, [first, second, third])
    assert after[0].description == first.description  # paid: history
    assert after[1].description == second.description  # closed: left alone
    assert after[2].description == "Nova"


def test_edit_installment_on_a_closed_statement_needs_acknowledgement(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    first, second, _ = installments(uow, card)
    today = D(2026, 7, 26)  # the first installment's statement closed on 07-25, unpaid
    state = GetEntryEditState(uow, FixedClock(today)).execute(first.id)
    assert state.is_closed_statement and state.is_installment
    assert state.plan and state.plan.installment_total == 3
    with pytest.raises(DomainError) as exc:
        edit_installment(uow, first, today=today, amount_cents=3_100)
    assert exc.value.code == "STATEMENT_CLOSED_NEEDS_ACK"
    assert uow.transactions.get(first.id) == first
    done = edit_installment(uow, first, today=today, amount_cents=3_100, acknowledge_closed=True)
    assert done.amount_cents == -3_100
    # installment 2 is on the open 2026-08 statement: no acknowledgement
    assert edit_installment(uow, second, today=today, amount_cents=3_050).amount_cents == -3_050


def test_edit_installment_on_a_paid_statement_is_refused(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    first, *_ = installments(uow, card)
    today = D(2026, 8, 1)
    assert first.statement_id
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(first.statement_id, checking.id, today)
    )
    with pytest.raises(DomainError) as exc:
        edit_installment(uow, first, today=today, amount_cents=1_000, acknowledge_closed=True)
    assert exc.value.code == "STATEMENT_ALREADY_PAID"


def test_installment_date_and_card_cannot_change(uow: MemoryUnitOfWork, card: Account) -> None:
    _, second, _ = installments(uow, card)
    with pytest.raises(DomainError) as exc:
        edit_installment(uow, second, posted_on=D(2026, 8, 1))
    assert exc.value.code == "INSTALLMENT_FIELD_LOCKED"
    with pytest.raises(DomainError) as exc2:
        edit_installment(uow, second, account_id="another")
    assert exc2.value.code == "INSTALLMENT_FIELD_LOCKED"


def test_edit_card_purchase_on_open_statement_needs_no_acknowledgement(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    entry = card_purchase(uow, card)  # 07-10 -> statement 2026-07 (closes 07-25)
    state = GetEntryEditState(uow, FixedClock(D(2026, 7, 20))).execute(entry.id)
    assert state.statement and not state.is_closed_statement and not state.is_locked
    updated = edit(uow, entry.id, today=D(2026, 7, 20), amount_cents=7_500, description="Fone")
    assert updated.amount_cents == -7_500 and updated.statement_id == entry.statement_id


def test_edit_card_purchase_on_closed_statement_needs_acknowledgement(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    entry = card_purchase(uow, card)
    today = D(2026, 7, 26)  # the 2026-07 statement closed on 07-25 and is unpaid
    state = GetEntryEditState(uow, FixedClock(today)).execute(entry.id)
    assert state.is_closed_statement and not state.is_locked
    with pytest.raises(DomainError) as exc:
        edit(uow, entry.id, today=today, amount_cents=7_500, description="Fone")
    assert exc.value.code == "STATEMENT_CLOSED_NEEDS_ACK"
    assert uow.transactions.get(entry.id) == entry
    updated = edit(
        uow, entry.id, today=today, amount_cents=7_500, description="Fone", acknowledge_closed=True
    )
    assert updated.amount_cents == -7_500


def test_edit_card_purchase_on_paid_statement_is_refused_even_when_acknowledged(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    entry = card_purchase(uow, card)
    today = D(2026, 8, 1)
    assert entry.statement_id
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(entry.statement_id, checking.id, today)
    )
    assert GetEntryEditState(uow, FixedClock(today)).execute(entry.id).is_locked
    with pytest.raises(DomainError) as exc:
        edit(uow, entry.id, today=today, acknowledge_closed=True)
    assert exc.value.code == "STATEMENT_ALREADY_PAID"


def test_edit_card_purchase_date_reassigns_the_statement(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    entry = card_purchase(uow, card)  # 2026-07
    today = D(2026, 7, 15)
    on_closing_day = edit(
        uow, entry.id, today=today, posted_on=D(2026, 7, 25), amount_cents=5_000, description="Fone"
    )
    assert on_closing_day.statement_id != entry.statement_id  # the closing day goes to the next
    statement = uow.statements.get(on_closing_day.statement_id or "")
    assert statement and str(statement.month) == "2026-08"
    back_again = edit(
        uow, entry.id, today=today, posted_on=D(2026, 7, 24), amount_cents=5_000, description="Fone"
    )
    assert back_again.statement_id == entry.statement_id


def test_edit_card_purchase_cannot_move_into_a_paid_statement(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    earlier = card_purchase(uow, card, purchased_on=D(2026, 6, 10), total_cents=1_000)
    later = card_purchase(uow, card, purchased_on=D(2026, 7, 10))
    assert earlier.statement_id
    today = D(2026, 7, 1)
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(earlier.statement_id, checking.id, today)
    )
    with pytest.raises(DomainError) as exc:
        edit(uow, later.id, today=today, posted_on=D(2026, 6, 10), description="Fone")
    assert exc.value.code == "STATEMENT_ALREADY_PAID"
    assert uow.transactions.get(later.id) == later


# --- refunded purchases ("Compra estornada") ---


def mark_refunded(uow: MemoryUnitOfWork, entry: Transaction, today: dt.date = TODAY, **kw: object):
    return edit(
        uow,
        entry.id,
        today=today,
        posted_on=entry.posted_on,
        amount_cents=abs(entry.amount_cents),
        description=entry.description,
        is_refunded=True,
        **kw,
    )


def statement_total(uow: MemoryUnitOfWork, statement_id: str, today: dt.date) -> int:
    from financas.application.queries.cards import statement_view

    statement = uow.statements.get(statement_id)
    assert statement
    return statement_view(uow, statement, today).total_cents


def test_refunded_card_purchase_leaves_statement_limit_and_open_balance(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    from financas.application.queries.cards import ListCards

    kept = card_purchase(uow, card, total_cents=2_000, description="Fica")
    gone = card_purchase(uow, card, total_cents=5_000, description="Estornada")
    today = D(2026, 7, 20)
    assert kept.statement_id and kept.statement_id == gone.statement_id
    assert statement_total(uow, kept.statement_id, today) == 7_000
    mark_refunded(uow, gone, today=today)
    stored = uow.transactions.get(gone.id)
    assert stored and stored.is_refunded and stored.amount_cents == -5_000  # the record stays
    assert statement_total(uow, kept.statement_id, today) == 2_000
    view = ListCards(uow, FixedClock(today)).execute().cards[0]
    assert view.usage.committed_cents == 2_000  # the limit came back
    assert view.telemetry and view.telemetry.open_balance_cents == 2_000
    # un-marking brings it all back
    edit(
        uow,
        gone.id,
        today=today,
        posted_on=gone.posted_on,
        amount_cents=5_000,
        description="Estornada",
        is_refunded=False,
    )
    assert statement_total(uow, kept.statement_id, today) == 7_000


def test_edit_without_the_flag_keeps_it(uow: MemoryUnitOfWork, card: Account) -> None:
    entry = card_purchase(uow, card)
    mark_refunded(uow, entry, today=EARLY)
    again = edit(
        uow,
        entry.id,
        today=EARLY,
        posted_on=entry.posted_on,
        amount_cents=5_100,
        description="Fone",  # ``is_refunded`` not given: kept
    )
    assert again.is_refunded and again.amount_cents == -5_100


def test_refunded_expense_on_checking_leaves_summary_and_balance(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    from financas.application.queries.summary import GetSummary, Period

    keep = register(uow, checking, amount_cents=1_000, description="Fica")
    gone = register(uow, checking, amount_cents=4_000, description="Volta")
    period = Period.month(YearMonth(2026, 7))
    assert GetSummary(uow).execute(period).expenses_cents == 5_000
    mark_refunded(uow, gone)
    assert GetSummary(uow).execute(period).expenses_cents == 1_000
    assert [m[1] for m in uow.transactions.movements(checking.id)] == [keep.amount_cents]


def test_only_expenses_can_be_refunded(uow: MemoryUnitOfWork, checking: Account) -> None:
    income = register(uow, checking, kind=K.INCOME, description="Salário")
    with pytest.raises(DomainError) as exc:
        mark_refunded(uow, income)
    assert exc.value.code == "REFUND_ONLY_FOR_EXPENSES"


def test_marking_refunded_on_a_closed_statement_needs_acknowledgement_and_paid_is_locked(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    entry = card_purchase(uow, card)
    today = D(2026, 7, 26)  # the statement closed on 07-25, unpaid
    with pytest.raises(DomainError) as exc:
        mark_refunded(uow, entry, today=today)
    assert exc.value.code == "STATEMENT_CLOSED_NEEDS_ACK"
    assert not uow.transactions.items[entry.id].is_refunded
    assert mark_refunded(uow, entry, today=today, acknowledge_closed=True).is_refunded
    # a paid statement is history: the flag cannot change, not even acknowledged
    other = card_purchase(uow, card, description="Outra", purchased_on=D(2026, 6, 10))
    assert other.statement_id
    paid_on = D(2026, 7, 1)
    PayStatement(uow, FixedClock(paid_on)).execute(
        PayStatementCommand(other.statement_id, checking.id, paid_on)
    )
    with pytest.raises(DomainError) as exc2:
        mark_refunded(uow, other, today=paid_on, acknowledge_closed=True)
    assert exc2.value.code == "STATEMENT_ALREADY_PAID"


def test_refund_one_installment_or_every_pending_one(uow: MemoryUnitOfWork, card: Account) -> None:
    first, second, third = installments(uow, card)
    mark_refunded(uow, second, today=EARLY)  # only this one
    flags = [t.is_refunded for t in snapshot(uow, [first, second, third])]
    assert flags == [False, True, False]
    # the refunded installment leaves the plan's own queries and the statement
    assert [t.id for t in uow.transactions.list_by_plan(first.plan_id or "")] == [
        first.id,
        third.id,
    ]
    # now every pending one, from the third installment
    edit_installment(uow, third, is_refunded=True, refund_pending_installments=True)
    assert [t.is_refunded for t in snapshot(uow, [first, second, third])] == [True, True, True]
    # unmarking spreads the same way
    edit_installment(uow, third, is_refunded=False, refund_pending_installments=True)
    assert [t.is_refunded for t in snapshot(uow, [first, second, third])] == [False] * 3


def test_refund_spread_skips_installments_on_closed_statements(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    first, second, third = installments(uow, card)
    today = D(2026, 7, 26)  # installment 1's statement (07) is closed; 08 is open; 09 future
    edit_installment(uow, second, today=today, is_refunded=True, refund_pending_installments=True)
    assert [t.is_refunded for t in snapshot(uow, [first, second, third])] == [False, True, True]


# --- a statement payment seen by the edit form ---


def test_edit_state_recognizes_both_legs_of_a_statement_payment(
    uow: MemoryUnitOfWork, card: Account, checking: Account, savings: Account
) -> None:
    entry = card_purchase(uow, card)
    today = D(2026, 7, 28)
    assert entry.statement_id
    legs = PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(entry.statement_id, checking.id, today)
    )
    state = GetEntryEditState(uow, FixedClock(today))
    for leg in legs:  # from the checking row or from the card row: the same payment
        seen = state.execute(leg.id)
        assert seen.is_transfer and seen.is_payment and seen.payment
        assert seen.payment.card_leg.account_id == card.id
        assert seen.payment.origin_leg and seen.payment.origin_leg.account_id == checking.id
        assert seen.payment.statement.status.value == "paid"
        assert not seen.is_locked  # the payment itself is editable even on a paid statement
    # a plain transfer between own accounts is not a payment
    own = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, today, 500)
    )
    assert not state.execute(own[0].id).is_payment
    # and the generic edit still refuses transfers: payments have their own use case
    with pytest.raises(DomainError) as exc:
        edit(uow, legs[0].id, today=today, amount_cents=100)
    assert exc.value.code == "TRANSFER_NOT_EDITABLE"


# --- payment method ---


def test_a_bank_entry_keeps_the_payment_method_it_was_given(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    assert register(uow, checking).payment_method is None  # not informed
    for method in (PM.PIX, PM.DEBIT, PM.BOLETO, PM.TRANSFER, PM.CASH, PM.OTHER):
        stored = uow.transactions.get(register(uow, checking, payment_method=method).id)
        assert stored and stored.payment_method is method
    salary = register(uow, checking, kind=K.INCOME, payment_method=PM.PIX)
    assert salary.payment_method is PM.PIX  # a PIX received


def test_a_card_purchase_is_always_credit_card(uow: MemoryUnitOfWork, card: Account) -> None:
    assert register(uow, card).payment_method is PM.CREDIT_CARD
    assert register(uow, card, payment_method=PM.CREDIT_CARD).payment_method is PM.CREDIT_CARD
    with pytest.raises(DomainError) as exc:
        register(uow, card, payment_method=PM.PIX)
    assert exc.value.code == "INVALID_PAYMENT_METHOD"


def test_credit_card_is_not_a_bank_method(uow: MemoryUnitOfWork, checking: Account) -> None:
    with pytest.raises(DomainError) as exc:
        register(uow, checking, payment_method=PM.CREDIT_CARD)
    assert exc.value.code == "INVALID_PAYMENT_METHOD"


def test_installments_and_statement_lines_are_credit_card(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            account_id=card.id,
            description="Monitor",
            purchased_on=D(2026, 7, 10),
            installments=3,
            total_cents=9_000,
        )
    )
    assert {t.payment_method for t in result.transactions} == {PM.CREDIT_CARD}


def test_editing_changes_or_keeps_the_payment_method(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = register(uow, checking, payment_method=PM.PIX)

    def edit(**kw: object) -> Transaction:
        return UpdateTransaction(uow, FixedClock(D(2026, 7, 20))).execute(
            UpdateTransactionCommand(
                entry.id,
                entry.posted_on,
                12_345,
                entry.description,
                **kw,  # type: ignore[arg-type]
            )
        )

    assert edit().payment_method is PM.PIX  # not mentioned: kept
    assert edit(payment_method=PM.BOLETO).payment_method is PM.BOLETO
    assert edit(payment_method=None).payment_method is None  # cleared
    with pytest.raises(DomainError) as exc:
        edit(payment_method=PM.CREDIT_CARD)
    assert exc.value.code == "INVALID_PAYMENT_METHOD"


def test_transfers_carry_no_payment_method(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    legs = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 10), 5_000, "Aporte")
    )
    assert {leg.payment_method for leg in legs} == {None}
