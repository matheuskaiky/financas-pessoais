"""Carta do mês: facts from the read queries and the structured letter (no wording here)."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.letter import (
    Letter,
    LetterFacts,
    Section,
    Sentence,
    SlotKind,
    TodoTarget,
    build_letter,
)
from financas.application.queries.letter import GetLetterFacts
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudgets
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
)
from financas.domain.models import Account, TransactionKind
from financas.domain.money import YearMonth

D = dt.date
CLOCK = FixedClock(D(2026, 10, 2))
SEPTEMBER = YearMonth(2026, 9)


def slug_id(uow: MemoryUnitOfWork, slug: str) -> str:
    category = uow.categories.get_by_slug(slug)
    assert category is not None
    return category.id


def add(
    uow: MemoryUnitOfWork,
    account: Account,
    day: dt.date,
    kind: TransactionKind,
    cents: int,
    slug: str,
    *,
    recurring: bool = False,
    description: str = "x",
) -> None:
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            account_id=account.id,
            posted_on=day,
            kind=kind,
            amount_cents=cents,
            description=description,
            category_id=slug_id(uow, slug),
            is_recurring=recurring,
        )
    )


def expense(
    uow: MemoryUnitOfWork, account: Account, day: dt.date, cents: int, slug: str, **kw: object
) -> None:
    add(uow, account, day, TransactionKind.EXPENSE, cents, slug, **kw)  # type: ignore[arg-type]


def letter_for(uow: MemoryUnitOfWork, month: YearMonth = SEPTEMBER, **kw: object) -> Letter:
    facts = GetLetterFacts(uow, CLOCK, 35).execute(month, **kw)  # type: ignore[arg-type]
    return build_letter(facts)


def section(letter: Letter, section_id: str) -> Section:
    return next(s for s in letter.sections if s.id == section_id)


def codes(sec: Section) -> list[str]:
    return [s.code for s in sec.sentences]


def sentence(sec: Section, code: str) -> Sentence:
    return next(s for s in sec.sentences if s.code == code)


@pytest.fixture
def busy(uow: MemoryUnitOfWork, checking: Account) -> MemoryUnitOfWork:
    """June to September: income 9,850.00 in September; food above its goal."""
    for month, total in ((6, 6_720_00), (7, 6_480_00), (8, 6_150_00)):
        expense(uow, checking, D(2026, month, 10), total, "home")
    add(uow, checking, D(2026, 9, 5), TransactionKind.INCOME, 9_850_00, "salary")
    expense(uow, checking, D(2026, 9, 6), 1_650_00, "home", recurring=True)
    expense(uow, checking, D(2026, 9, 8), 1_186_40, "groceries")
    expense(uow, checking, D(2026, 9, 9), 842_30, "food")
    expense(uow, checking, D(2026, 9, 10), 2_733_88, "shopping")
    SetCategoryBudgets(uow).execute({slug_id(uow, "food"): 700_00})
    return uow


def test_panorama_numbers_and_note(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy)
    panorama = section(letter, "panorama")
    opening = panorama.sentences[0]
    assert opening.code == "panorama.surplus"
    spent = 1_650_00 + 1_186_40 + 842_30 + 2_733_88
    assert opening.slots["despesas"].value == spent
    assert opening.slots["receitas"].value == 9_850_00
    assert opening.slots["saldo"].value == 9_850_00 - spent
    assert opening.slots["poupanca"].value == pytest.approx((9_850_00 - spent) / 9_850_00)
    assert opening.note is not None and opening.note.key == "balance"
    assert opening.note.slots["saldo"].kind is SlotKind.SIGNED_MONEY
    assert letter.balance_cents == 9_850_00 - spent
    assert letter.savings_rate == pytest.approx((9_850_00 - spent) / 9_850_00)
    assert letter.entry_count == 5


def test_average_of_the_three_previous_months_and_the_signal(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy)
    average = sentence(section(letter, "panorama"), "panorama.average")
    # (6,720.00 + 6,480.00 + 6,150.00) / 3 = 6,450.00 ; September spent 6,412.58
    assert average.slots["comparacao"].value == "below"
    assert average.slots["diferenca"].value == 6_450_00 - 6_412_58
    assert average.note is not None
    assert average.note.slots["media"].value == 6_450_00
    assert [average.note.slots[f"m_{i}"].value for i in (1, 2, 3)] == [6_720_00, 6_480_00, 6_150_00]
    assert average.slots["meses"].value == (
        YearMonth(2026, 6),
        YearMonth(2026, 7),
        YearMonth(2026, 8),
    )
    assert letter.average_signal == "below" and letter.spark_average_cents == 6_450_00


def test_above_the_average_and_months_without_entries_do_not_count(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    expense(uow, checking, D(2026, 7, 10), 1_000_00, "home")  # only July has entries
    expense(uow, checking, D(2026, 9, 10), 1_500_00, "home")
    letter = letter_for(uow)
    average = sentence(section(letter, "panorama"), "panorama.average")
    assert (
        average.slots["comparacao"].value == "above" and average.slots["diferenca"].value == 500_00
    )
    assert average.note is not None and average.note.slots["n"].value == 1


def test_deficit_and_no_income_openings(uow: MemoryUnitOfWork, checking: Account) -> None:
    expense(uow, checking, D(2026, 9, 10), 100_00, "home")
    assert section(letter_for(uow), "panorama").sentences[0].code == "panorama.no_income"
    add(uow, checking, D(2026, 9, 11), TransactionKind.INCOME, 50_00, "salary")
    opening = section(letter_for(uow), "panorama").sentences[0]
    assert opening.code == "panorama.deficit" and opening.slots["saldo"].value == 50_00


def test_empty_month_is_an_empty_letter(uow: MemoryUnitOfWork) -> None:
    letter = letter_for(uow)
    assert not letter.has_entries
    assert codes(section(letter, "panorama")) == ["panorama.empty"]
    assert codes(section(letter, "where")) == ["where.none"]
    assert codes(section(letter, "agreed")) == ["agreed.no_goals"]
    assert letter.top_categories == () and letter.goal_chart is None
    assert letter.average_signal is None


def test_where_the_money_went_and_recurring_share(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy)
    where = section(letter, "where")
    top = where.sentences[0]
    assert top.code == "where.top2"
    assert top.slots["categoria_1"].value == "Compras"  # 2,733.88 is the biggest
    assert top.slots["categoria_2"].value == "Casa"
    total = 6_412_58
    assert top.slots["soma_top"].value == 2_733_88 + 1_650_00
    assert top.slots["pct_top"].value == pytest.approx((2_733_88 + 1_650_00) / total)
    rec = sentence(where, "where.recurring_one")
    assert rec.slots["n_rec"].value == 1
    assert rec.slots["recorrentes"].value == 1_650_00
    assert [b.name for b in letter.top_categories] == ["Compras", "Casa", "Supermercado"]


def test_over_the_goal_with_severity_and_chart(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy)
    agreed = section(letter, "agreed")
    first = agreed.sentences[0]
    assert first.code == "agreed.first_over"
    assert first.slots["categoria"].value == "Alimentação"
    assert first.slots["gasto"].value == 842_30 and first.slots["meta"].value == 700_00
    assert first.slots["excesso"].value == 142_30
    assert first.slots["severidade"].value == "over"  # 20% above: not slight, not far
    chart = letter.goal_chart
    assert chart is not None and chart.goal_cents == 700_00
    assert chart.spending_cents == (0, 0, 842_30)
    assert [str(m) for m in chart.months] == ["2026-07", "2026-08", "2026-09"]


def test_exactly_at_the_goal_is_not_over(busy: MemoryUnitOfWork) -> None:
    SetCategoryBudgets(busy).execute({slug_id(busy, "food"): 842_30})
    agreed = section(letter_for(busy), "agreed")
    assert codes(agreed) == ["agreed.within"] and agreed.sentences[0].slots["n_meta"].value == 1


@pytest.mark.parametrize(
    ("spent", "severity"), [(770_00, "slight"), (771_00, "over"), (1_050_00, "far")]
)
def test_severity_thresholds(
    uow: MemoryUnitOfWork, checking: Account, spent: int, severity: str
) -> None:
    expense(uow, checking, D(2026, 9, 9), spent, "food")
    SetCategoryBudgets(uow).execute({slug_id(uow, "food"): 700_00})
    first = section(letter_for(uow), "agreed").sentences[0]
    assert first.slots["severidade"].value == severity


def test_other_categories_over_the_goal_are_summarised(busy: MemoryUnitOfWork) -> None:
    SetCategoryBudgets(busy).execute({slug_id(busy, "shopping"): 1_000_00})
    agreed = section(letter_for(busy), "agreed")
    assert agreed.sentences[0].slots["categoria"].value == "Compras"  # biggest excess first
    others = sentence(agreed, "agreed.others")
    assert others.slots["n_outras"].value == 1
    assert others.slots["soma_excessos"].value == 142_30


def test_ahead_closed_statements_cash_and_installments(
    busy: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    RecordBalance(busy).execute(RecordBalanceCommand(checking.id, D(2026, 9, 30), 7_600_45))
    RegisterCardPurchase(busy).execute(
        CardPurchaseCommand(
            account_id=card.id,
            description="Geladeira",
            purchased_on=D(2026, 9, 10),
            total_cents=3_000_00,
            installments=3,
        )
    )
    letter = letter_for(busy)
    ahead = section(letter, "ahead")
    # card: closes 11 days before the 5th. September purchase -> September statement (closes 25/09)
    assert ahead.sentences[0].code == "ahead.statements"
    assert ahead.sentences[0].slots["n_faturas"].value == 1
    assert ahead.sentences[0].slots["faturas_total"].value == 1_000_00
    due = sentence(ahead, "ahead.due")
    assert due.slots["valor"].value == 1_000_00 and due.slots["vence"].value == D(2026, 10, 5)
    covers = sentence(ahead, "ahead.cash_covers")
    assert covers.slots["caixa"].value == 7_600_45
    assert covers.slots["sobra"].value == 7_600_45 - 1_000_00
    inst = sentence(ahead, "ahead.installments")
    assert inst.slots["parcelas_futuras"].value == 1_000_00  # November; October is open
    assert [s.code for s in letter.cash_steps] == ["today", "after_statement"]
    assert letter.cash_steps[-1].cents == letter.cash_steps[0].cents - 1_000_00


def test_cash_unknown_is_not_zero(busy: MemoryUnitOfWork) -> None:
    ahead = section(letter_for(busy), "ahead")
    assert "ahead.cash_unknown" in codes(ahead) and "ahead.no_statements" in codes(ahead)
    assert letter_for(busy).cash_steps == ()


def test_short_cash(busy: MemoryUnitOfWork, checking: Account, card: Account) -> None:
    RecordBalance(busy).execute(RecordBalanceCommand(checking.id, D(2026, 10, 1), 200_00))
    RegisterCardPurchase(busy).execute(
        CardPurchaseCommand(
            account_id=card.id, description="TV", purchased_on=D(2026, 9, 10), total_cents=900_00
        )
    )
    short = sentence(section(letter_for(busy), "ahead"), "ahead.cash_short")
    assert short.slots["sobra"].value == 700_00


def test_todos_in_priority_order(
    busy: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    expense(busy, checking, D(2026, 9, 12), 10_00, "uncategorized")
    expense(busy, checking, D(2026, 9, 13), 11_00, "uncategorized")
    letter = letter_for(busy, last_backup=D(2026, 9, 20), backup_warn_days=7)
    kinds = [t.code for t in letter.todos]
    assert kinds == ["uncategorized", "no_balance", "no_valuation", "backup_old"]
    uncategorized = letter.todos[0]
    assert uncategorized.slots["n"].value == 2
    assert uncategorized.target is TodoTarget.UNCATEGORIZED_ENTRIES
    assert uncategorized.target_id == slug_id(busy, "uncategorized")
    before = section(letter, "before")
    assert before.sentences[0].code == "before.some"
    assert before.sentences[0].slots["n_itens"].value == 4
    assert "before.item.backup_old" in codes(before)
    backup = next(t for t in letter.todos if t.code == "backup_old")
    assert backup.slots["dias"].value == 12


def test_no_backup_at_all_is_a_todo_and_a_recent_one_is_not(busy: MemoryUnitOfWork) -> None:
    assert "backup_none" in [t.code for t in letter_for(busy, last_backup=None).todos]
    assert "backup_old" not in [t.code for t in letter_for(busy, last_backup=D(2026, 10, 1)).todos]


def test_nothing_pending_says_so(uow: MemoryUnitOfWork) -> None:
    letter = letter_for(uow, last_backup=D(2026, 10, 1))
    assert codes(section(letter, "before")) == ["before.none"]


def test_statement_difference_becomes_a_todo(busy: MemoryUnitOfWork, card: Account) -> None:
    from financas.application.use_cases.cards import InformStatementTotal

    RegisterCardPurchase(busy).execute(
        CardPurchaseCommand(
            account_id=card.id, description="TV", purchased_on=D(2026, 9, 10), total_cents=900_00
        )
    )
    statement = next(iter(busy.statements.items.values()))
    InformStatementTotal(busy).execute(statement.id, 938_00)
    todo = next(t for t in letter_for(busy).todos if t.code == "difference")
    assert todo.slots["valor"].value == 38_00 and todo.slots["sentido"].value == "informed_higher"
    assert todo.target is TodoTarget.STATEMENT and todo.target_id == statement.id


def test_fingerprint_follows_the_totals(busy: MemoryUnitOfWork, checking: Account) -> None:
    first = letter_for(busy).fingerprint
    assert first == letter_for(busy).fingerprint and len(first) == 8
    expense(busy, checking, D(2026, 9, 20), 1_00, "food")
    assert letter_for(busy).fingerprint != first


def test_spark_covers_january_to_the_month(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy)
    assert str(letter.spark[0].month) == "2026-01"
    assert letter.spark[-1].is_current and letter.spark[-1].cents == 6_412_58
    assert len(letter.spark) == 9


def test_every_slot_value_is_typed_and_money_is_int_cents(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy)
    for sec in letter.sections:
        for sen in sec.sentences:
            for slot in sen.slots.values():
                if slot.kind in (SlotKind.MONEY, SlotKind.SIGNED_MONEY, SlotKind.COUNT):
                    assert isinstance(slot.value, int) and not isinstance(slot.value, bool)
                if slot.kind is SlotKind.MONEY:
                    assert isinstance(slot.value, int) and slot.value >= 0


def test_a_letter_for_an_old_month_still_builds(busy: MemoryUnitOfWork) -> None:
    letter = letter_for(busy, YearMonth(2026, 7))
    assert letter.has_entries and letter.month == YearMonth(2026, 7)
    assert isinstance(LetterFacts, type)
