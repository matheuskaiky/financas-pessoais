"""Selection Mode rules: the current-month window, merge limits and the batch delete."""

import dataclasses
import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.plan_purchases import ListPlanPurchases
from financas.application.queries.selection import ListRowSelections
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.batch_delete import (
    BatchDelete,
    PreviewBatchDelete,
    parse_selection_ids,
)
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.merge import MergeCommand, MergeTransactions
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    SplitItem,
    UpdateTransaction,
    UpdateTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, Transaction, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.services.selection import (
    MAX_SELECTION,
    MergeBlock,
    SelectionLock,
    in_current_month,
    month_lock,
)
from financas.infrastructure.clock import SAO_PAULO, SystemClock

D = dt.date
TODAY = D(2026, 7, 20)  # July's statement (closes 07-25) is open
CLOCK = FixedClock(TODAY)


def cat(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def spend(uow: MemoryUnitOfWork, account: Account, cents: int, **kw: object) -> Transaction:
    values: dict[str, object] = {
        "account_id": account.id,
        "posted_on": D(2026, 7, 10),
        "kind": TransactionKind.EXPENSE,
        "amount_cents": cents,
        "description": "Mercado",
        "category_id": cat(uow, "groceries"),
    }
    values.update(kw)
    return RegisterTransaction(uow).execute(RegisterTransactionCommand(**values))  # type: ignore[arg-type]


def buy(uow: MemoryUnitOfWork, card: Account, **kw: object):
    values: dict[str, object] = {
        "account_id": card.id,
        "description": "Monitor",
        "purchased_on": D(2026, 7, 10),
        "total_cents": 9_000,
    }
    values.update(kw)
    return RegisterCardPurchase(uow).execute(CardPurchaseCommand(**values))  # type: ignore[arg-type]


def code_of(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


def delete(uow: MemoryUnitOfWork, *ids: str, today: dt.date = TODAY):
    return BatchDelete(uow, FixedClock(today)).execute(parse_selection_ids(list(ids)))


# --- the window (R1) ---


@pytest.mark.parametrize(
    ("today", "day", "inside"),
    [
        (D(2026, 10, 31), D(2026, 10, 1), True),
        (D(2026, 10, 31), D(2026, 9, 30), False),
        (D(2026, 10, 31), D(2026, 11, 1), False),
        (D(2026, 11, 1), D(2026, 10, 31), False),
        (
            D(2026, 1, 15),
            D(2025, 1, 15),
            False,
        ),  # the year counts: October 2025 is not October 2026
        (D(2028, 2, 29), D(2028, 2, 1), True),
    ],
)
def test_in_current_month_boundaries(today: dt.date, day: dt.date, inside: bool) -> None:
    assert in_current_month(day, today) is inside


def test_month_lock_tells_past_from_future() -> None:
    assert month_lock(D(2026, 7, 31), TODAY) is None
    assert month_lock(D(2026, 6, 30), TODAY) is SelectionLock.PAST_MONTH
    assert month_lock(D(2026, 8, 1), TODAY) is SelectionLock.FUTURE_MONTH


def test_the_system_clock_reads_the_sao_paulo_calendar() -> None:
    late = dt.datetime(2026, 11, 1, 2, 30, tzinfo=dt.UTC)  # still October 31st in Brazil
    assert late.astimezone(SAO_PAULO).date() == D(2026, 10, 31)
    assert SystemClock().today() == dt.datetime.now(SAO_PAULO).date()


# --- typed ids ---


def test_selection_ids_are_typed_deduplicated_and_capped() -> None:
    parsed = parse_selection_ids(["entry:a1", "plan:p1", "entry:a1", "entry:b2"])
    assert (parsed.entry_ids, parsed.plan_ids, parsed.total) == (("a1", "b2"), ("p1",), 3)
    for bad in ("a1", "entry:", "card:x", "entry:a b", "plan:../x"):
        with pytest.raises(DomainError) as exc:
            parse_selection_ids([bad])
        assert code_of(exc) == "BAD_SELECTION_ID"
    assert parse_selection_ids([f"entry:{n}" for n in range(MAX_SELECTION)]).total == MAX_SELECTION
    with pytest.raises(DomainError) as exc2:
        parse_selection_ids([f"entry:{n}" for n in range(MAX_SELECTION + 1)])
    assert code_of(exc2) == "TOO_MANY_IDS"


# --- merging: current month, nothing itemized ---


def merge(uow: MemoryUnitOfWork, ids: list[str], today: dt.date = TODAY):
    return MergeTransactions(uow, FixedClock(today)).execute(
        MergeCommand(tuple(ids), "Compras", D(2026, 7, 12))
    )


def test_merge_accepts_entries_of_the_current_month(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    a = spend(uow, checking, 1_000)
    b = spend(uow, checking, 2_000, posted_on=D(2026, 7, 1))
    assert merge(uow, [a.id, b.id]).amount_cents == -3_000


def test_merge_rejects_a_prior_month_and_names_the_rows(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    now = spend(uow, checking, 1_000)
    old = spend(uow, checking, 2_000, posted_on=D(2026, 6, 30))
    future = spend(uow, checking, 3_000, posted_on=D(2026, 8, 1))
    with pytest.raises(DomainError) as exc:
        merge(uow, [now.id, old.id, future.id])
    assert code_of(exc) == "MERGE_OUTSIDE_CURRENT_MONTH"
    assert exc.value.params["ids"] == f"{old.id},{future.id}"
    assert uow.transactions.get(old.id) is not None  # nothing changed


def test_merge_rejects_an_entry_that_already_has_items(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    big = spend(uow, checking, 38_000)
    UpdateTransaction(uow, CLOCK).execute(
        UpdateTransactionCommand(
            big.id,
            big.posted_on,
            38_000,
            big.description,
            splits=(
                SplitItem("Feira", cat(uow, "groceries"), 30_000),
                SplitItem("Casa", cat(uow, "home"), 8_000),
            ),
        )
    )
    small = spend(uow, checking, 2_000, description="Bala")
    with pytest.raises(DomainError) as exc:
        merge(uow, [big.id, small.id])
    assert code_of(exc) == "MERGE_ITEMIZED_FORBIDDEN"
    assert uow.transactions.get(small.id) is not None


# --- batch delete ---


def test_batch_delete_removes_entries_and_plans_in_one_go_and_totals_follow(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    keep = spend(uow, checking, 1_000, description="Fica")
    gone = spend(uow, checking, 2_000, description="Some")
    plan = buy(uow, card, installments=3).plan
    assert plan
    before = GetSummary(uow).execute(Period.month(YearMonth(2026, 7))).expenses_cents
    result = delete(uow, f"entry:{gone.id}", f"plan:{plan.id}")
    assert (result.deleted, result.skipped) == (2, 0)
    assert uow.transactions.get(gone.id) is None and uow.plans.get(plan.id) is None
    assert uow.transactions.get(keep.id) is not None
    after = GetSummary(uow).execute(Period.month(YearMonth(2026, 7))).expenses_cents
    assert before - after == 2_000 + 3_000  # computed on read: nothing else to recalculate


def test_batch_delete_skips_ids_that_are_already_gone(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 1_000)
    first = delete(uow, f"entry:{entry.id}", "entry:ghost", "plan:ghost")
    assert (first.deleted, first.skipped) == (1, 2)
    again = delete(uow, f"entry:{entry.id}")  # a retry of the same request is harmless
    assert (again.deleted, again.skipped) == (0, 1)


def test_batch_delete_is_all_or_nothing(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    plain = spend(uow, checking, 1_000)
    on_card = buy(uow, card, total_cents=4_000).transactions[0]
    assert on_card.statement_id
    today = D(2026, 7, 28)  # July's statement closed on 07-25
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(on_card.statement_id, checking.id, today)
    )
    with pytest.raises(DomainError) as exc:  # the card entry is on a paid statement
        delete(uow, f"entry:{plain.id}", f"entry:{on_card.id}", today=today)
    assert code_of(exc) == "STATEMENT_ALREADY_PAID"
    assert uow.transactions.get(plain.id) is not None  # the earlier delete rolled back
    assert uow.transactions.get(on_card.id) is not None


def test_batch_delete_rejects_rows_outside_the_current_month(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    now = spend(uow, checking, 1_000)
    old = spend(uow, checking, 2_000, posted_on=D(2026, 6, 30))
    with pytest.raises(DomainError) as exc:
        delete(uow, f"entry:{now.id}", f"entry:{old.id}")
    assert code_of(exc) == "DELETE_OUTSIDE_CURRENT_MONTH"
    assert exc.value.params["ids"] == f"entry:{old.id}"
    assert uow.transactions.get(now.id) is not None and uow.transactions.get(old.id) is not None


def test_a_plan_is_judged_by_its_purchase_date(uow: MemoryUnitOfWork, card: Account) -> None:
    june = buy(uow, card, purchased_on=D(2026, 6, 10), installments=2).plan
    assert june
    with pytest.raises(DomainError) as exc:
        delete(uow, f"plan:{june.id}")
    assert code_of(exc) == "DELETE_OUTSIDE_CURRENT_MONTH"
    assert exc.value.params["ids"] == f"plan:{june.id}"


def test_plan_deletion_keeps_paid_installments_as_history(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    plan = buy(uow, card, installments=3).plan
    assert plan
    first = uow.transactions.list_by_plan(plan.id)[0]
    assert first.statement_id
    today = D(2026, 7, 28)
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(first.statement_id, checking.id, today)
    )
    result = delete(uow, f"plan:{plan.id}", today=today)
    assert result.deleted == 1  # one row, the plan
    assert [t.installment_number for t in uow.transactions.list_by_plan(plan.id)] == [1]
    assert uow.plans.get(plan.id) is not None  # the plan record stays for the audit trail


def test_the_preview_describes_each_plan_and_changes_nothing(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    plan = buy(uow, card, description="Monitor Gamer", installments=3).plan
    assert plan
    first = uow.transactions.list_by_plan(plan.id)[0]
    assert first.statement_id
    today = D(2026, 7, 28)
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(first.statement_id, checking.id, today)
    )
    plain = spend(uow, checking, 1_000)
    preview = PreviewBatchDelete(uow, FixedClock(today)).execute(
        parse_selection_ids([f"plan:{plan.id}", f"entry:{plain.id}", "entry:ghost"])
    )
    assert (preview.rows, preview.skipped) == (2, 1)
    (note,) = preview.plans
    assert (note.description, note.installments, note.installment_cents) == (
        "Monitor Gamer",
        3,
        3_000,
    )
    assert (note.pending_count, note.pending_cents, note.paid_count) == (2, 6_000, 1)
    assert len(uow.transactions.list_by_plan(plan.id)) == 3 and uow.transactions.get(plain.id)


def test_the_preview_of_nothing_that_exists_is_not_found(uow: MemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as exc:
        PreviewBatchDelete(uow, CLOCK).execute(parse_selection_ids(["entry:ghost"]))
    assert code_of(exc) == "NOT_FOUND"


# --- what each row may do (the data-sel-* hooks) ---


def selections(uow: MemoryUnitOfWork, entries: list[Transaction], today: dt.date = TODAY):
    purchases = {p.plan.id: p for p in ListPlanPurchases(uow).execute()}
    splits = uow.transactions.splits_for([e.id for e in entries])
    return ListRowSelections(uow, FixedClock(today)).execute(entries, purchases, splits)


def test_rows_outside_the_month_are_locked_and_the_others_are_not(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    now = spend(uow, checking, 1_000)
    old = spend(uow, checking, 2_000, posted_on=D(2026, 6, 30))
    future = spend(uow, checking, 3_000, posted_on=D(2026, 8, 1))
    got = selections(uow, [now, old, future])
    assert got[now.id].lock is None and got[now.id].sel_id == f"entry:{now.id}"
    assert got[old.id].lock is SelectionLock.PAST_MONTH
    assert got[future.id].lock is SelectionLock.FUTURE_MONTH
    assert got[now.id].key == checking.id


def test_plan_refunded_itemized_and_income_rows_can_be_deleted_but_not_merged(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    plan_purchase = buy(uow, card, installments=3)
    anchor = plan_purchase.transactions[0]
    refunded = uow.transactions.get(spend(uow, checking, 1_000).id)
    assert refunded
    uow.transactions.update(dataclasses.replace(refunded, is_refunded=True))
    itemized = spend(uow, checking, 10_000)
    UpdateTransaction(uow, CLOCK).execute(
        UpdateTransactionCommand(
            itemized.id,
            itemized.posted_on,
            10_000,
            itemized.description,
            splits=(
                SplitItem("A", cat(uow, "groceries"), 6_000),
                SplitItem("B", cat(uow, "home"), 4_000),
            ),
        )
    )
    itemized = uow.transactions.get(itemized.id)  # type: ignore[assignment]
    salary = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            account_id=checking.id,
            posted_on=D(2026, 7, 5),
            kind=TransactionKind.INCOME,
            amount_cents=500_000,
            description="Salário",
        )
    )
    plain = spend(uow, checking, 700)
    marked = uow.transactions.get(refunded.id)
    assert marked and itemized
    rows = [anchor, marked, itemized, salary, plain]
    got = selections(uow, rows)
    assert got[anchor.id].sel_id == f"plan:{anchor.plan_id}" and got[anchor.id].lock is None
    assert got[anchor.id].merge_block is MergeBlock.PLAN and got[anchor.id].plan_installments == 3
    assert got[marked.id].merge_block is MergeBlock.REFUNDED
    assert got[itemized.id].merge_block is MergeBlock.ITEMIZED
    assert got[salary.id].merge_block is MergeBlock.NOT_EXPENSE
    assert got[plain.id].merge_block is None and got[plain.id].lock is None
    assert all(s.lock is None for s in got.values())  # all of them can be checked


def test_a_plan_dated_before_the_month_or_fully_paid_is_locked(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    old = buy(uow, card, purchased_on=D(2026, 6, 10), installments=2).transactions[0]
    assert selections(uow, [old])[old.id].lock is SelectionLock.PAST_MONTH
    on_card = spend(uow, card, 1_500)
    assert on_card.statement_id
    today = D(2026, 7, 28)
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(on_card.statement_id, checking.id, today)
    )
    got = selections(uow, [on_card], today=today)  # a paid statement is history
    assert got[on_card.id].lock is SelectionLock.STATEMENT_PAID
    assert got[on_card.id].key == f"{card.id}:{on_card.statement_id}"
