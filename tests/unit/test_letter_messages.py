"""pt-BR wording of the letter: every code has text and every figure is a marked slot."""

import datetime as dt
import re
from pathlib import Path

import pytest
from test_letter import add, expense, letter_for, slug_id

from fakes import MemoryUnitOfWork
from financas.application.letter import Slot, SlotKind
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudgets
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.domain.models import Account, TransactionKind
from financas.domain.money import YearMonth
from financas.interfaces.messages import letter as words

D = dt.date


def test_every_code_the_builder_can_emit_has_wording() -> None:
    source = Path("src/financas/application/letter/build.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'"((?:panorama|where|agreed|ahead|before)\.[a-z0-9_.]+)"', source))
    assert emitted
    assert emitted <= set(words.SENTENCES), emitted - set(words.SENTENCES)
    for todo in (
        "difference",
        "uncategorized",
        "no_balance",
        "no_valuation",
        "stale_valuation",
        "backup_old",
        "backup_none",
    ):
        assert todo in words.TODOS
        assert f"before.item.{todo}" in words.SENTENCES
    for signal in re.findall(r'_signal\("([a-z_]+)"\)|"(slight|over|far)"', source):
        for code in signal:
            if code:
                assert code in words.SIGNAL_WORDS


def test_every_placeholder_in_a_template_is_a_documented_slot_name() -> None:
    for code, template in words.SENTENCES.items():
        for name in re.findall(r"\{(\w+)", template):
            assert name.islower() or name.startswith("n"), (code, name)


def test_formatting_of_each_slot_kind() -> None:
    f = words.format_slot
    assert f(Slot(SlotKind.MONEY, 985000)) == "R$ 9.850,00"
    assert f(Slot(SlotKind.SIGNED_MONEY, -3800)) == "−R$ 38,00"
    assert f(Slot(SlotKind.PERCENT, 0.349)) == "34,9%"
    assert f(Slot(SlotKind.PERCENT, -0.1)) == "−10,0%"
    assert f(Slot(SlotKind.COUNT, 14)) == "14"
    assert f(Slot(SlotKind.DATE, D(2026, 10, 5))) == "05/10"
    assert f(Slot(SlotKind.MONTH, YearMonth(2026, 9))) == "setembro"
    months = (YearMonth(2026, 6), YearMonth(2026, 7), YearMonth(2026, 8))
    assert f(Slot(SlotKind.MONTHS, months)) == "jun, jul e ago"
    assert f(Slot(SlotKind.NAMES, ("Casa",))) == "Casa"
    assert f(Slot(SlotKind.NAMES, ("Casa", "Compras"))) == "Casa e Compras"
    assert f(Slot(SlotKind.SIGNAL, "below")) == "abaixo"


@pytest.fixture
def rich(uow: MemoryUnitOfWork, checking: Account, card: Account) -> MemoryUnitOfWork:
    for month, total in ((6, 6_720_00), (7, 6_480_00), (8, 6_150_00)):
        expense(uow, checking, D(2026, month, 10), total, "home")
    add(uow, checking, D(2026, 9, 5), TransactionKind.INCOME, 9_850_00, "salary")
    expense(uow, checking, D(2026, 9, 6), 1_650_00, "home", recurring=True)
    expense(uow, checking, D(2026, 9, 8), 1_186_40, "groceries")
    expense(uow, checking, D(2026, 9, 9), 842_30, "food")
    expense(uow, checking, D(2026, 9, 10), 2_733_88, "shopping")
    expense(uow, checking, D(2026, 9, 12), 10_00, "uncategorized")
    SetCategoryBudgets(uow).execute(
        {slug_id(uow, "food"): 700_00, slug_id(uow, "shopping"): 2_000_00}
    )
    RecordBalance(uow).execute(RecordBalanceCommand(checking.id, D(2026, 9, 30), 7_600_45))
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            account_id=card.id,
            description="Geladeira",
            purchased_on=D(2026, 9, 10),
            total_cents=3_000_00,
            installments=3,
        )
    )
    return uow


def test_the_rendered_letter_reads_like_the_board(rich: MemoryUnitOfWork) -> None:
    rendered = words.render_letter(letter_for(rich, last_backup=D(2026, 9, 1)))
    texts = {
        s.id: "".join(
            p.text if isinstance(p, words.TextPart | words.ValuePart) else f"[{p.number}]"
            for p in s.parts
        )
        for s in rendered.sections
    }
    panorama = texts["panorama"]
    assert panorama.startswith("Setembro fechou no azul. Entraram R$ 9.850,00 e saíram R$ 7.422,58")
    assert "um saldo de R$ 2.427,42, ou 24,6% da renda.[1]" in panorama
    assert "As despesas ficaram R$ 972,58 acima da média de jun, jul e ago.[2]" in panorama
    assert "Compras e Casa levaram R$ 4.383,88" in texts["where"]
    assert (
        "Compras passou da meta em R$ 733,88: foram R$ 2.733,88 para R$ 2.000,00 combinados."
        in (texts["agreed"])
    )
    assert (
        "1 outra categoria também passou da meta (Alimentação), somando R$ 142,30"
        in texts["agreed"]
    )
    assert "A fatura do Cartão, de R$ 1.000,00, vence em 05/10." in texts["ahead"]
    assert "ainda deixa R$ 6.600,45." in texts["ahead"]
    assert "Há 2 lançamentos sem categoria em setembro." in texts["before"]
    assert "O último backup foi há 31 dias." in texts["before"]


def test_notes_carry_the_calculation_and_are_numbered_in_order(rich: MemoryUnitOfWork) -> None:
    rendered = words.render_letter(letter_for(rich))
    numbers = [n.number for s in rendered.sections for n in s.notes]
    assert numbers == list(range(1, len(numbers) + 1))
    first = rendered.sections[0].notes[0]
    assert first.title == "Saldo e poupança"
    rows = {r.label: r.value for r in first.rows}
    assert rows["Receitas − despesas"] == "R$ 9.850,00 − R$ 7.422,58"
    assert rows["Saldo"] == "R$ 2.427,42"
    average = rendered.sections[0].notes[1]
    assert average.title == "Média de jun, jul e ago"
    assert average.rows[0].label == "R$ 6.720,00 + R$ 6.480,00 + R$ 6.150,00"
    assert average.rows[0].value == "÷ 3"
    assert average.rows[1].value == "R$ 6.450,00 − R$ 7.422,58 = −R$ 972,58"


def test_value_parts_point_at_their_note_and_counts_match(rich: MemoryUnitOfWork) -> None:
    rendered = words.render_letter(letter_for(rich))
    panorama = rendered.sections[0]
    values = [p for p in panorama.parts if isinstance(p, words.ValuePart)]
    assert {v.slot for v in values} >= {"receitas", "despesas", "saldo", "poupanca"}
    assert all(v.note in (1, 2) for v in values)
    assert rendered.slots_total == rendered.slots_filled > 10
    assert rendered.notes_total == sum(len(s.notes) for s in rendered.sections)


def test_the_empty_letter_renders_without_notes(uow: MemoryUnitOfWork) -> None:
    rendered = words.render_letter(letter_for(uow, last_backup=D(2026, 10, 1)))
    assert rendered.notes_total == 0
    first = "".join(p.text for p in rendered.sections[0].parts if isinstance(p, words.TextPart))
    assert "Não há lançamentos em setembro" in first
    assert rendered.todos == ()


def test_todos_have_title_sub_and_call_to_action(rich: MemoryUnitOfWork) -> None:
    rendered = words.render_letter(letter_for(rich, last_backup=None))
    by_code = {t.code: t for t in rendered.todos}
    assert by_code["uncategorized"].title == "Categorizar 2 lançamentos"
    assert by_code["uncategorized"].sub == "Lançamentos de setembro em “Não categorizado”"
    assert by_code["backup_none"].cta == "Fazer backup"
