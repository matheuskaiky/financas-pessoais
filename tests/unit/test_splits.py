"""Itemized expenses ("Desdobramento") and merging expenses into one (CLAUDE.md 9.1, 10)."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.cards import ListCards, statement_view
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    PayStatement,
    PayStatementCommand,
    RegisterCardPurchase,
)
from financas.application.use_cases.merge import MergeCommand, MergeTransactions
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    RegisterTransaction,
    RegisterTransactionCommand,
    SplitItem,
    UpdateTransaction,
    UpdateTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, Transaction, TransactionKind
from financas.domain.money import YearMonth
from financas.domain.services.splits import allocations, validate_split_amounts

D = dt.date
K = TransactionKind
TODAY = D(2026, 7, 20)  # July's statement (closes 07-25) is open


def cat(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def spend(uow: MemoryUnitOfWork, account: Account, cents: int, **kw: object) -> Transaction:
    values: dict[str, object] = {
        "account_id": account.id,
        "posted_on": D(2026, 7, 10),
        "kind": K.EXPENSE,
        "amount_cents": cents,
        "description": "Mercado",
        "category_id": cat(uow, "groceries"),
    }
    values.update(kw)
    return RegisterTransaction(uow).execute(RegisterTransactionCommand(**values))  # type: ignore[arg-type]


def itemize(
    uow: MemoryUnitOfWork,
    entry: Transaction,
    items: tuple[SplitItem, ...] | None,
    today: dt.date = TODAY,
    **kw: object,
) -> Transaction:
    return UpdateTransaction(uow, FixedClock(today)).execute(
        UpdateTransactionCommand(
            entry.id,
            entry.posted_on,
            abs(entry.amount_cents),
            entry.description,
            splits=items,
            **kw,  # type: ignore[arg-type]
        )
    )


def market_items(uow: MemoryUnitOfWork) -> tuple[SplitItem, ...]:
    return (
        SplitItem("Feira e laticínios", cat(uow, "groceries"), 22_000),
        SplitItem("Higiene pessoal", cat(uow, "health"), 9_000),
        SplitItem("Limpeza", cat(uow, "home"), 7_000),
    )


def code(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


# --- the sum invariant ---


@pytest.mark.parametrize(
    ("total", "amounts", "error", "remaining"),
    [
        (38_000, [22_000, 9_000, 7_000], None, None),
        (38_000, [30_000, 8_000], None, None),
        (38_000, [38_000], "SPLIT_NEEDS_TWO_ITEMS", None),
        (38_000, [], "SPLIT_NEEDS_TWO_ITEMS", None),
        (38_000, [38_000, 0], "AMOUNT_NOT_POSITIVE", None),
        (38_000, [40_000, -2_000], "AMOUNT_NOT_POSITIVE", None),
        (38_000, [20_000, 10_000], "SPLIT_SUM_MISMATCH", 8_000),  # falls short: "restam"
        (38_000, [30_000, 9_000], "SPLIT_SUM_MISMATCH", -1_000),  # goes over
    ],
)
def test_split_amounts_must_add_up_exactly(
    total: int, amounts: list[int], error: str | None, remaining: int | None
) -> None:
    if error is None:
        validate_split_amounts(total, amounts)
        return
    with pytest.raises(DomainError) as exc:
        validate_split_amounts(total, amounts)
    assert exc.value.code == error
    if remaining is not None:
        assert dict(exc.value.params)["remaining_cents"] == remaining


def test_allocations_follow_the_items_or_the_entry(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    assert allocations(entry, {}) == [(entry.category_id, -38_000)]
    itemize(uow, entry, market_items(uow))
    parts = allocations(entry, uow.transactions.splits_for([entry.id]))
    assert [c for c, _ in parts] == [cat(uow, "groceries"), cat(uow, "health"), cat(uow, "home")]
    assert sum(cents for _, cents in parts) == entry.amount_cents  # the parts add up to the entry


# --- itemizing an entry ---


def test_itemize_saves_items_and_keeps_the_parent_whole(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    after = itemize(uow, entry, market_items(uow))
    assert after.amount_cents == -38_000 and after.id == entry.id
    items = uow.transactions.splits_for([entry.id])[entry.id]
    assert [(i.description, i.amount_cents) for i in items] == [
        ("Feira e laticínios", 22_000),
        ("Higiene pessoal", 9_000),
        ("Limpeza", 7_000),
    ]
    assert sum(i.amount_cents for i in items) == 38_000


def test_a_wrong_sum_is_refused_and_nothing_changes(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    bad = (SplitItem("A", cat(uow, "groceries"), 20_000), SplitItem("B", cat(uow, "home"), 10_000))
    with pytest.raises(DomainError) as exc:
        itemize(uow, entry, bad)
    assert code(exc) == "SPLIT_SUM_MISMATCH"
    assert uow.transactions.splits_for([entry.id]) == {}


def test_items_are_validated(uow: MemoryUnitOfWork, checking: Account) -> None:
    entry = spend(uow, checking, 10_000)
    income_category = cat(uow, "salary")
    for items, expected in [
        ((SplitItem("A", income_category, 5_000), SplitItem("B", cat(uow, "home"), 5_000)),
         "CATEGORY_KIND_MISMATCH"),
        ((SplitItem("  ", cat(uow, "home"), 5_000), SplitItem("B", cat(uow, "home"), 5_000)),
         "EMPTY_DESCRIPTION"),
        ((SplitItem("A", "nope", 5_000), SplitItem("B", cat(uow, "home"), 5_000)), "NOT_FOUND"),
    ]:  # fmt: skip
        with pytest.raises(DomainError) as exc:
            itemize(uow, entry, items)
        assert code(exc) == expected


def test_only_expenses_can_be_itemized(uow: MemoryUnitOfWork, checking: Account) -> None:
    income = spend(uow, checking, 10_000, kind=K.INCOME, category_id=cat(uow, "salary"))
    items = (SplitItem("A", cat(uow, "salary"), 5_000), SplitItem("B", cat(uow, "salary"), 5_000))
    with pytest.raises(DomainError) as exc:
        itemize(uow, income, items)
    assert code(exc) == "SPLIT_ONLY_FOR_EXPENSES"


def test_changing_the_amount_of_an_itemized_entry_needs_new_items(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    itemize(uow, entry, market_items(uow))
    itemized = uow.transactions.get(entry.id)
    update = UpdateTransaction(uow, FixedClock(TODAY))
    with pytest.raises(DomainError) as exc:  # items kept, amount moved: they no longer add up
        update.execute(
            UpdateTransactionCommand(entry.id, entry.posted_on, 40_000, entry.description)
        )
    assert code(exc) == "SPLIT_SUM_MISMATCH"
    assert uow.transactions.get(entry.id) == itemized  # atomic
    # a plain edit that keeps the amount keeps the items
    update.execute(UpdateTransactionCommand(entry.id, entry.posted_on, 38_000, "Mercadão"))
    assert len(uow.transactions.splits_for([entry.id])[entry.id]) == 3
    # an empty tuple removes them
    itemize(uow, entry, ())
    assert uow.transactions.splits_for([entry.id]) == {}


def test_deleting_an_itemized_entry_deletes_its_items(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    itemize(uow, entry, market_items(uow))
    DeleteTransaction(uow, FixedClock(TODAY)).execute(entry.id)
    assert uow.transactions.splits_for([entry.id]) == {}


# --- what sees the parent and what sees the items ---


def test_spending_by_category_follows_the_items_and_the_totals_stay_whole(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    spend(uow, checking, 5_000, description="Padaria", category_id=cat(uow, "food"))
    period = Period.month(YearMonth(2026, 7))
    before = GetSummary(uow).execute(period)
    assert {r.category_id: r.total_cents for r in before.by_category}[
        cat(uow, "groceries")
    ] == 38_000
    itemize(uow, entry, market_items(uow))
    after = GetSummary(uow).execute(period)
    by_category = {r.category_id: r.total_cents for r in after.by_category}
    assert by_category[cat(uow, "groceries")] == 22_000
    assert by_category[cat(uow, "health")] == 9_000
    assert by_category[cat(uow, "home")] == 7_000
    assert by_category[cat(uow, "food")] == 5_000
    assert after.expenses_cents == before.expenses_cents == 43_000  # nothing drifts
    assert sum(by_category.values()) == after.expenses_cents
    assert after.balance_cents == before.balance_cents


def test_statement_limit_and_balance_see_only_the_parent(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    entry = spend(uow, card, 38_000)
    other = spend(uow, checking, 1_000, description="Taxa")
    itemize(uow, entry, market_items(uow))
    itemize(
        uow,
        other,
        (SplitItem("A", cat(uow, "home"), 400), SplitItem("B", cat(uow, "health"), 600)),
    )
    view = ListCards(uow, FixedClock(TODAY)).execute().cards[0]
    assert view.usage.committed_cents == 38_000
    assert view.telemetry and view.telemetry.open_balance_cents == 38_000
    statement = uow.statements.get(entry.statement_id or "")
    assert statement and statement_view(uow, statement, TODAY).total_cents == 38_000
    assert statement_view(uow, statement, TODAY).entry_count == 1  # one entry, not three
    assert [m[1] for m in uow.transactions.movements(checking.id)] == [-1_000]


# --- merging ---


def merge(uow: MemoryUnitOfWork, ids: list[str], today: dt.date = TODAY, **kw: object):
    return MergeTransactions(uow, FixedClock(today)).execute(
        MergeCommand(tuple(ids), "Compras da semana", D(2026, 7, 12), **kw)  # type: ignore[arg-type]
    )


def test_merge_two_card_purchases_into_one_itemized_entry(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    a = spend(uow, card, 22_000, description="Feira", category_id=cat(uow, "groceries"))
    b = spend(uow, card, 6_000, description="Sabão", category_id=cat(uow, "home"))
    assert a.statement_id == b.statement_id
    parent = merge(uow, [a.id, b.id])
    assert parent.amount_cents == -28_000 and parent.kind is K.EXPENSE
    assert (parent.description, parent.posted_on) == ("Compras da semana", D(2026, 7, 12))
    assert parent.account_id == card.id and parent.statement_id == a.statement_id
    assert parent.description_search == "compras da semana"
    assert uow.transactions.get(a.id) is None and uow.transactions.get(b.id) is None
    items = uow.transactions.splits_for([parent.id])[parent.id]
    assert [(i.description, i.category_id, i.amount_cents) for i in items] == [
        ("Feira", cat(uow, "groceries"), 22_000),
        ("Sabão", cat(uow, "home"), 6_000),
    ]
    # the statement and the limit see the same total as before: one entry of the same amount
    view = ListCards(uow, FixedClock(TODAY)).execute().cards[0]
    assert view.usage.committed_cents == 28_000
    # an itemized entry has no category of its own: its items carry them
    assert parent.category_id is None


def test_merge_checking_expenses_and_keep_spending_by_category(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    a = spend(uow, checking, 3_000, description="Café", category_id=cat(uow, "food"))
    b = spend(uow, checking, 4_500, description="Pão", category_id=cat(uow, "groceries"))
    period = Period.month(YearMonth(2026, 7))
    before = GetSummary(uow).execute(period)
    merge(uow, [a.id, b.id])
    after = GetSummary(uow).execute(period)
    assert after.expenses_cents == before.expenses_cents == 7_500
    assert {r.category_id: r.total_cents for r in after.by_category} == {
        r.category_id: r.total_cents for r in before.by_category
    }


def test_merging_an_itemized_entry_is_refused(uow: MemoryUnitOfWork, checking: Account) -> None:
    big = spend(uow, checking, 38_000)
    itemize(uow, big, market_items(uow))
    small = spend(uow, checking, 2_000, description="Bala", category_id=cat(uow, "food"))
    with pytest.raises(DomainError) as exc:
        merge(uow, [big.id, small.id])
    assert code(exc) == "MERGE_ITEMIZED_FORBIDDEN"
    assert len(uow.transactions.splits_for([big.id])[big.id]) == 3  # nothing changed
    assert uow.transactions.get(small.id) is not None


@pytest.mark.parametrize("count", [0, 1])
def test_merge_needs_two_entries(uow: MemoryUnitOfWork, checking: Account, count: int) -> None:
    ids = [spend(uow, checking, 1_000).id][:count]
    with pytest.raises(DomainError) as exc:
        merge(uow, ids + ids)  # the same entry twice is still one entry
    assert code(exc) == "MERGE_NEEDS_TWO"


def test_merge_requires_one_account_and_one_card_cycle(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    a = spend(uow, checking, 1_000)
    b = spend(uow, card, 2_000)
    with pytest.raises(DomainError) as exc:
        merge(uow, [a.id, b.id])
    assert code(exc) == "MERGE_ACCOUNT_MISMATCH"
    july = spend(uow, card, 1_000, posted_on=D(2026, 7, 10))
    august = spend(uow, card, 1_500, posted_on=D(2026, 7, 26))  # after the closing date: August
    assert july.statement_id != august.statement_id
    with pytest.raises(DomainError) as exc2:
        merge(uow, [july.id, august.id])
    assert code(exc2) == "MERGE_STATEMENT_MISMATCH"
    assert uow.transactions.get(july.id) == july and uow.transactions.get(august.id) == august


def test_merge_refuses_entries_that_are_not_plain_expenses(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    plain = spend(uow, checking, 1_000)
    income = spend(uow, checking, 500, kind=K.INCOME, category_id=cat(uow, "salary"))
    with pytest.raises(DomainError) as exc:
        merge(uow, [plain.id, income.id])
    assert code(exc) == "MERGE_ONLY_PLAIN_EXPENSES"
    plan = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Fone", D(2026, 7, 10), installments=2, total_cents=2_000)
    )
    other = spend(uow, card, 700)
    with pytest.raises(DomainError) as exc2:
        merge(uow, [plan.transactions[0].id, other.id])
    assert code(exc2) == "MERGE_ONLY_PLAIN_EXPENSES"


def test_merge_respects_paid_and_closed_statements(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    a = spend(uow, card, 1_000, description="A")
    b = spend(uow, card, 2_000, description="B")
    closed_day = D(2026, 7, 26)  # July's statement closed on 07-25, unpaid
    with pytest.raises(DomainError) as exc:
        merge(uow, [a.id, b.id], today=closed_day)
    assert code(exc) == "STATEMENT_CLOSED_NEEDS_ACK"
    assert uow.transactions.get(a.id) == a
    PayStatement(uow, FixedClock(closed_day)).execute(
        PayStatementCommand(a.statement_id or "", checking.id, closed_day)
    )
    with pytest.raises(DomainError) as exc2:  # paid is history, acknowledged or not
        merge(uow, [a.id, b.id], today=closed_day, acknowledge_closed=True)
    assert code(exc2) == "STATEMENT_ALREADY_PAID"


def test_merge_with_a_closed_unpaid_statement_works_once_acknowledged(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    a = spend(uow, card, 1_000, description="A")
    b = spend(uow, card, 2_000, description="B")
    parent = merge(uow, [a.id, b.id], today=D(2026, 7, 26), acknowledge_closed=True)
    assert parent.amount_cents == -3_000


def test_merge_requires_a_description_and_is_atomic(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    a = spend(uow, checking, 1_000)
    b = spend(uow, checking, 2_000)
    with pytest.raises(DomainError) as exc:
        MergeTransactions(uow, FixedClock(TODAY)).execute(
            MergeCommand((a.id, b.id), "   ", D(2026, 7, 12))
        )
    assert code(exc) == "EMPTY_DESCRIPTION"


def test_budget_spending_follows_the_items(uow: MemoryUnitOfWork, checking: Account) -> None:
    from financas.application.queries.planning import GetBudget
    from financas.application.use_cases.budget import SetCategoryBudget

    SetCategoryBudget(uow).execute(cat(uow, "home"), 10_000)
    entry = spend(uow, checking, 38_000, posted_on=D(2026, 7, 10))
    itemize(uow, entry, market_items(uow))
    budget = GetBudget(uow, FixedClock(D(2026, 10, 6))).execute().budget  # Jul-Sep are closed
    by_id = {r.category_id: r for r in [*budget.with_goal, *budget.without_goal]}
    assert by_id[cat(uow, "home")].months == (7_000, 0, 0)  # the item, not the parent's category
    assert by_id[cat(uow, "groceries")].months == (22_000, 0, 0)
    assert by_id[cat(uow, "health")].months == (9_000, 0, 0)


# --- the sums the items editor reported wrongly (R$ 25,52, R$ 1.250,00, signs) ---

from financas.domain.money import parse_brl  # noqa: E402


@pytest.mark.parametrize(
    ("total", "typed"),
    [
        ("25,52", ["15,52", "10,00"]),  # R$ 25,52 = 15,52 + 10,00
        ("1.250,00", ["1.000,00", "250,00"]),  # thousands: "." is a mark, not a decimal point
        ("0,03", ["0,01", "0,02"]),
        ("10.000,01", ["9.999,99", "0,02"]),
        ("R$ 25,52", ["R$ 15,52", "R$ 10,00"]),  # with the currency symbol
        ("25,52", ["-15,52", "-10,00"]),  # a minus typed on the items: the sign never counts
    ],
)
def test_typed_items_that_add_up_are_accepted(
    uow: MemoryUnitOfWork, checking: Account, total: str, typed: list[str]
) -> None:
    entry = spend(uow, checking, abs(parse_brl(total)))
    items = tuple(
        SplitItem(f"item {n}", cat(uow, "groceries"), parse_brl(text))  # signs as typed
        for n, text in enumerate(typed)
    )
    saved = itemize_with(uow, entry, abs(parse_brl(total)), items)
    stored = uow.transactions.splits_for([entry.id])[entry.id]
    assert saved.amount_cents == -abs(parse_brl(total))  # the ledger keeps the negative expense
    assert sum(i.amount_cents for i in stored) == abs(parse_brl(total))
    assert all(i.amount_cents > 0 for i in stored)  # stored as positive magnitudes


def itemize_with(
    uow: MemoryUnitOfWork, entry: Transaction, cents: int, items: tuple[SplitItem, ...]
) -> Transaction:
    return UpdateTransaction(uow, FixedClock(TODAY)).execute(
        UpdateTransactionCommand(entry.id, entry.posted_on, cents, entry.description, splits=items)
    )


def test_a_negative_ledger_amount_matches_positive_items(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    """The parent is stored as -2552, the items typed positive: compared as magnitudes."""
    entry = spend(uow, checking, 2_552)
    assert entry.amount_cents == -2_552
    items = (
        SplitItem("A", cat(uow, "groceries"), 1_552),
        SplitItem("B", cat(uow, "home"), 1_000),
    )
    itemize_with(uow, entry, 2_552, items)
    parts = allocations(entry, uow.transactions.splits_for([entry.id]))
    assert sum(cents for _, cents in parts) == entry.amount_cents == -2_552  # signed, same total


@pytest.mark.parametrize(
    ("items", "remaining"),
    [
        ((1_552, 999), 1),  # one cent short
        ((1_552, 1_001), -1),  # one cent over
        ((1_000,), None),  # a single item is not a split
    ],
)
def test_a_real_mismatch_still_reports_the_difference(
    uow: MemoryUnitOfWork,
    checking: Account,
    items: tuple[int, ...],
    remaining: int | None,
) -> None:
    entry = spend(uow, checking, 2_552)
    split = tuple(SplitItem(f"i{n}", cat(uow, "groceries"), c) for n, c in enumerate(items))
    with pytest.raises(DomainError) as exc:
        itemize_with(uow, entry, 2_552, split)
    if remaining is None:
        assert exc.value.code == "SPLIT_NEEDS_TWO_ITEMS"
    else:
        assert exc.value.code == "SPLIT_SUM_MISMATCH"
        assert dict(exc.value.params)["remaining_cents"] == remaining
    assert uow.transactions.splits_for([entry.id]) == {}


# --- an itemized entry has no category of its own (CLAUDE.md 9.10) ---


def test_saving_items_clears_the_parents_category_and_removing_them_asks_for_one(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    assert entry.category_id == cat(uow, "groceries")
    saved = itemize(uow, entry, market_items(uow))
    assert saved.category_id is None
    assert uow.transactions.get(entry.id).category_id is None  # type: ignore[union-attr]
    # clearing the items without choosing a category falls back to "uncategorized"...
    cleared = itemize(uow, entry, ())
    assert cleared.category_id == cat(uow, "uncategorized")
    # ...and a chosen category is taken
    itemize(uow, entry, market_items(uow))
    chosen = UpdateTransaction(uow, FixedClock(TODAY)).execute(
        UpdateTransactionCommand(
            entry.id, entry.posted_on, 38_000, entry.description, cat(uow, "food"), splits=()
        )
    )
    assert chosen.category_id == cat(uow, "food")


def test_a_parent_category_is_refused_while_items_are_attached(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 38_000)
    update = UpdateTransaction(uow, FixedClock(TODAY))
    with pytest.raises(DomainError) as together:  # new items and a category in one request
        update.execute(
            UpdateTransactionCommand(
                entry.id,
                entry.posted_on,
                38_000,
                entry.description,
                cat(uow, "food"),
                splits=market_items(uow),
            )
        )
    assert code(together) == "PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS"
    assert uow.transactions.get(entry.id) == entry and uow.transactions.splits_for([entry.id]) == {}
    itemize(uow, entry, market_items(uow))
    with pytest.raises(DomainError) as kept:  # items already there, a category sent: still refused
        update.execute(
            UpdateTransactionCommand(
                entry.id, entry.posted_on, 38_000, entry.description, cat(uow, "food")
            )
        )
    assert code(kept) == "PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS"
    assert uow.transactions.get(entry.id).category_id is None  # type: ignore[union-attr]


def test_category_totals_are_the_items_plus_the_plain_entries_and_never_the_parent(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    from financas.application.queries.charts import GetCategoryBreakdown

    run = spend(uow, checking, 38_000)  # filed under groceries until it is itemized
    spend(uow, checking, 5_000, description="Padaria", category_id=cat(uow, "food"))
    spend(uow, checking, 9_900, description="Livro", category_id=cat(uow, "shopping"))
    period = Period.month(YearMonth(2026, 7))
    itemize(uow, run, market_items(uow))
    summary = GetSummary(uow).execute(period)
    by_category = {r.category_id: r.total_cents for r in summary.by_category}
    assert by_category == {
        cat(uow, "groceries"): 22_000,  # the item, not the parent's R$ 380,00
        cat(uow, "health"): 9_000,
        cat(uow, "home"): 7_000,
        cat(uow, "food"): 5_000,
        cat(uow, "shopping"): 9_900,
    }
    plain = 5_000 + 9_900
    assert sum(by_category.values()) == plain + 22_000 + 9_000 + 7_000  # unsplit + child items
    assert sum(by_category.values()) == summary.expenses_cents  # no orphaned parent amount
    assert summary.expenses_cents == 52_900  # the cash flow still reads the parent once
    breakdown = GetCategoryBreakdown(uow).execute(period)
    assert breakdown.total_cents == 52_900
    assert sum(s.total_cents for s in breakdown.top) + breakdown.rest_total_cents == 52_900
    slices = [*breakdown.top, *breakdown.rest]
    assert {s.category_id for s in slices} == set(
        by_category
    )  # only the items' and plain categories
    assert (
        sum(s.count for s in slices) == 5
    )  # three items + two plain entries: the parent is no row


# --- itemized installment plans ---


def buy_items(
    uow: MemoryUnitOfWork,
    card: Account,
    items: tuple[SplitItem, ...],
    *,
    total: int | None = 45_000,
    installments: int = 3,
    **kw: object,
):
    values: dict[str, object] = {
        "account_id": card.id,
        "description": "Monitor",
        "purchased_on": D(2026, 7, 10),
        "installments": installments,
        "total_cents": total,
        "splits": items,
    }
    values.update(kw)
    return RegisterCardPurchase(uow).execute(CardPurchaseCommand(**values))  # type: ignore[arg-type]


def monitor_items(uow: MemoryUnitOfWork) -> tuple[SplitItem, ...]:
    return (
        SplitItem("Monitor Gamer", cat(uow, "shopping"), 35_000),
        SplitItem("Cabo HDMI e Suporte", cat(uow, "home"), 10_000),
    )


def items_of(uow: MemoryUnitOfWork, entry: Transaction) -> list[tuple[str, int]]:
    return [
        (i.description, i.amount_cents) for i in uow.transactions.splits_for([entry.id])[entry.id]
    ]


def test_an_itemized_plan_gives_every_installment_its_share_of_every_item(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy_items(uow, card, monitor_items(uow))
    assert result.plan and len(result.transactions) == 3
    shares = [items_of(uow, t) for t in result.transactions]
    assert shares == [
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],  # 117,00 + 33,00
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],  # 117,00 + 33,00
        [("Monitor Gamer", 11_666), ("Cabo HDMI e Suporte", 3_334)],  # 116,00 + 34,00
    ]
    for entry, row in zip(result.transactions, shares, strict=True):
        assert entry.category_id is None  # every installment: no category of its own
        assert entry.amount_cents == -15_000
        assert sum(cents for _, cents in row) == -entry.amount_cents  # the installment adds up
    for column in range(2):  # and every item adds up across the plan
        assert sum(row[column][1] for row in shares) == (35_000, 10_000)[column]
    assert result.plan.category_id == cat(uow, "shopping")  # display only: the biggest item's


def test_uneven_installments_still_reconcile_to_the_cent(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    items = (
        SplitItem("A", cat(uow, "groceries"), 5_000),
        SplitItem("B", cat(uow, "health"), 3_000),
        SplitItem("C", cat(uow, "home"), 2_000),
    )
    result = buy_items(uow, card, items, total=10_000)  # 33,34 + 33,33 + 33,33
    assert [-t.amount_cents for t in result.transactions] == [3_334, 3_333, 3_333]
    rows = [items_of(uow, t) for t in result.transactions]
    assert [sum(c for _, c in row) for row in rows] == [3_334, 3_333, 3_333]
    assert [sum(row[j][1] for row in rows) for j in range(3)] == [5_000, 3_000, 2_000]
    assert all(t.category_id is None for t in result.transactions)


def test_items_given_with_the_installment_value_are_the_whole_purchase(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy_items(uow, card, monitor_items(uow), total=None, installment_cents=15_000)
    assert [-t.amount_cents for t in result.transactions] == [15_000] * 3
    assert sum(sum(c for _, c in items_of(uow, t)) for t in result.transactions) == 45_000


def test_a_running_purchase_only_itemizes_the_installments_it_creates(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy_items(
        uow,
        card,
        monitor_items(uow),
        installments=3,
        current_installment=2,
        statement_month=YearMonth(2026, 8),
    )
    assert [t.installment_number for t in result.transactions] == [2, 3]
    full = [[11_667, 3_333], [11_667, 3_333], [11_666, 3_334]]  # the plan's own distribution
    for entry, row in zip(result.transactions, full[1:], strict=True):
        assert [c for _, c in items_of(uow, entry)] == row
        assert entry.category_id is None


@pytest.mark.parametrize(
    ("items", "code_"),
    [
        ((("A", 30_000), ("B", 10_000)), "SPLIT_SUM_MISMATCH"),  # 400,00 of 450,00
        ((("A", 35_000),), "SPLIT_NEEDS_TWO_ITEMS"),
        ((("A", 50_000), ("B", -5_000)), "SPLIT_SUM_MISMATCH"),  # the sign is dropped: 550,00
    ],
)
def test_a_purchase_whose_items_do_not_add_up_creates_nothing(
    uow: MemoryUnitOfWork, card: Account, items: tuple[tuple[str, int], ...], code_: str
) -> None:
    split = tuple(SplitItem(text, cat(uow, "shopping"), cents) for text, cents in items)
    with pytest.raises(DomainError) as exc:
        buy_items(uow, card, split)
    assert code(exc) == code_
    assert uow.transactions.items == {} and uow.plans.items == {}


def test_a_purchase_with_items_takes_no_category_of_its_own(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        buy_items(uow, card, monitor_items(uow), category_id=cat(uow, "shopping"))
    assert code(exc) == "PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS"
    single = buy_items(uow, card, monitor_items(uow), installments=1, total=45_000)
    (entry,) = single.transactions  # an à vista purchase: one entry, its items attached
    assert entry.category_id is None and sum(c for _, c in items_of(uow, entry)) == 45_000
    assert single.plan is None


def test_category_spending_lands_in_the_month_of_each_installment(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    buy_items(uow, card, monitor_items(uow))  # statements 2026-07, 2026-08 and 2026-09
    shopping, home = cat(uow, "shopping"), cat(uow, "home")
    expected = {
        (2026, 7): {shopping: 11_667, home: 3_333},
        (2026, 8): {shopping: 11_667, home: 3_333},
        (2026, 9): {shopping: 11_666, home: 3_334},
    }
    for (year, month), per_category in expected.items():
        summary = GetSummary(uow).execute(Period.month(YearMonth(year, month)))
        assert {r.category_id: r.total_cents for r in summary.by_category} == per_category
        assert summary.expenses_cents == 15_000  # the installment, read once
    august = ListCards(uow, FixedClock(D(2026, 7, 20))).execute().cards[0]
    assert august.usage.committed_cents == 45_000  # the limit sees the whole purchase once


def test_an_itemized_installment_cannot_be_adjusted_without_its_items(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    from financas.application.use_cases.cards import AdjustInstallment

    result = buy_items(uow, card, monitor_items(uow))
    with pytest.raises(DomainError) as exc:
        AdjustInstallment(uow, FixedClock(D(2026, 7, 1))).execute(result.transactions[1].id, 14_000)
    assert code(exc) == "SPLIT_AMOUNT_NEEDS_ITEMS"
    assert uow.transactions.get(result.transactions[1].id).amount_cents == -15_000  # type: ignore[union-attr]


def edit_installment(
    uow: MemoryUnitOfWork, entry: Transaction, today: dt.date, **kw: object
) -> Transaction:
    values: dict[str, object] = {
        "transaction_id": entry.id,
        "posted_on": entry.posted_on,
        "amount_cents": -entry.amount_cents,
        "description": entry.description,
    }
    values.update(kw)
    return UpdateTransaction(uow, FixedClock(today)).execute(
        UpdateTransactionCommand(**values)  # type: ignore[arg-type]
    )


def test_plan_wide_items_cover_the_open_and_future_installments_and_skip_history(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    from financas.application.use_cases.cards import PayStatement, PayStatementCommand

    result = buy_items(uow, card, monitor_items(uow))  # installments on 2026-07, 08 and 09
    first, second, third = result.transactions
    today = D(2026, 8, 3)  # July's statement closed on 07-25 and is paid: history
    PayStatement(uow, FixedClock(today)).execute(
        PayStatementCommand(first.statement_id or "", checking.id, today)
    )
    before_first = (uow.transactions.get(first.id), items_of(uow, first))
    new_items = (  # totals of the covered installments: 2 x 150,00 = 300,00
        SplitItem("Monitor Gamer", cat(uow, "shopping"), 20_000),
        SplitItem("Suporte", cat(uow, "home"), 6_000),
        SplitItem("Cabo", cat(uow, "other"), 4_000),
    )
    edit_installment(uow, second, today, splits=new_items)  # plan-wide: propagate is on
    rows = [items_of(uow, t) for t in (second, third)]
    assert [sum(c for _, c in row) for row in rows] == [15_000, 15_000]
    assert [sum(row[j][1] for row in rows) for j in range(3)] == [20_000, 6_000, 4_000]
    assert all(uow.transactions.get(t.id).category_id is None for t in (second, third))  # type: ignore[union-attr]
    assert (uow.transactions.get(first.id), items_of(uow, first)) == before_first  # history kept
    # the items must add up to the covered installments, not to the whole plan
    with pytest.raises(DomainError) as exc:
        edit_installment(uow, second, today, splits=monitor_items(uow))  # 450,00 of 300,00
    assert code(exc) == "SPLIT_SUM_MISMATCH"


def test_single_scope_items_belong_to_that_installment_only(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy_items(uow, card, monitor_items(uow))
    first, second, third = result.transactions
    only_this = (
        SplitItem("Peça A", cat(uow, "shopping"), 9_000),
        SplitItem("Peça B", cat(uow, "home"), 6_000),
    )
    edit_installment(uow, second, D(2026, 7, 1), splits=only_this, propagate_plan_metadata=False)
    assert items_of(uow, second) == [("Peça A", 9_000), ("Peça B", 6_000)]
    assert [c for _, c in items_of(uow, first)] == [11_667, 3_333]  # the others do not change
    assert [c for _, c in items_of(uow, third)] == [11_666, 3_334]
    with pytest.raises(DomainError) as exc:  # one installment: its own 150,00
        edit_installment(
            uow,
            second,
            D(2026, 7, 1),
            splits=(
                SplitItem("X", cat(uow, "home"), 10_000),
                SplitItem("Y", cat(uow, "home"), 4_000),
            ),
            propagate_plan_metadata=False,
        )
    assert code(exc) == "SPLIT_SUM_MISMATCH"


def test_removing_plan_wide_items_gives_the_installments_a_category_again(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = buy_items(uow, card, monitor_items(uow))
    first = result.transactions[0]
    edit_installment(uow, first, D(2026, 7, 1), splits=(), category_id=cat(uow, "shopping"))
    for entry in result.transactions:
        assert uow.transactions.splits_for([entry.id]) == {}
        assert uow.transactions.get(entry.id).category_id == cat(uow, "shopping")  # type: ignore[union-attr]


def test_a_category_set_with_the_plan_never_lands_on_an_itemized_sibling(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    plain = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Fone", D(2026, 7, 10), installments=2, total_cents=2_000)
    )
    first, second = plain.transactions
    only_second = (SplitItem("A", cat(uow, "home"), 500), SplitItem("B", cat(uow, "health"), 500))
    edit_installment(uow, second, D(2026, 7, 1), splits=only_second, propagate_plan_metadata=False)
    edit_installment(uow, first, D(2026, 7, 1), category_id=cat(uow, "shopping"))  # plan-wide
    assert uow.transactions.get(first.id).category_id == cat(uow, "shopping")  # type: ignore[union-attr]
    assert uow.transactions.get(second.id).category_id is None  # type: ignore[union-attr]
