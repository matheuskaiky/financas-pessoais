"""The deterministic phrase parser (⌘K "registrar por frase" and E se…)."""

import datetime as dt
import random
import string
from itertools import pairwise

import pytest

from financas.application.phrases import (
    AccountRef,
    AmbiguityCode,
    AmountRole,
    FragmentKind,
    PhraseDraft,
    Vocabulary,
    parse_phrase,
)

TODAY = dt.date(2026, 10, 1)
VOCAB = Vocabulary(
    (
        AccountRef("bb-cc", "Conta BB", False, ("Banco do Brasil",)),
        AccountRef("bb-card", "BB Ourocard", True, ("Banco do Brasil",)),
        AccountRef("nu", "Nubank", True),
        AccountRef("inter", "Inter", True),
        AccountRef("cx", "Caixinha", False),
    )
)


def parse(text: str, **kw: AmountRole) -> PhraseDraft:
    return parse_phrase(text, VOCAB, TODAY, **kw)


def understood(draft: PhraseDraft) -> dict[FragmentKind, str]:
    return {f.kind: f.text for f in draft.fragments}


def test_board_phrase_for_the_palette() -> None:
    draft = parse("café 12,50 hoje nubank")
    assert draft.description == "Café"
    assert draft.price_cents == 1250
    assert draft.on_date == TODAY
    assert draft.account_id == "nu"
    assert draft.installments is None
    assert understood(draft) == {
        FragmentKind.DESCRIPTION: "café",
        FragmentKind.AMOUNT: "12,50",
        FragmentKind.DATE: "hoje",
        FragmentKind.ACCOUNT: "nubank",
    }


def test_installments_with_a_lone_amount_ask_total_or_each() -> None:
    draft = parse("jantar 142 ontem no nubank em 2x")
    assert draft.description == "Jantar"
    assert draft.price_cents == 14200
    assert draft.installments == 2
    assert draft.on_date == dt.date(2026, 9, 30)
    assert draft.account_id == "nu"
    ask = [a for a in draft.ambiguities if a.code is AmbiguityCode.AMOUNT_ROLE]
    assert len(ask) == 1 and ask[0].amount_cents == 14200 and ask[0].installments == 2
    assert draft.amount_role is AmountRole.TOTAL  # the default reading


def test_the_user_answer_resolves_the_question() -> None:
    each = parse("jantar 142 ontem no nubank em 2x", amount_role=AmountRole.EACH)
    assert not each.ambiguities
    assert each.installment_cents == 14200 and each.price_cents is None
    assert each.amount_cents == 28400
    total = parse("jantar 142 ontem no nubank em 2x", amount_role=AmountRole.TOTAL)
    assert not total.ambiguities and total.price_cents == 14200 and total.installment_cents is None


def test_price_and_installment_value_are_read_apart() -> None:
    for phrase in ("geladeira de 4.200 em 10x de 450 no BB", "geladeira 4200 10x de 450 bb"):
        draft = parse(phrase)
        assert draft.description == "Geladeira"
        assert draft.price_cents == 420000
        assert draft.installment_cents == 45000
        assert draft.installments == 10
        assert not draft.has_ambiguity(AmbiguityCode.AMOUNT_ROLE)
    underlined = understood(parse("geladeira de 4.200 em 10x de 450 no BB"))
    assert underlined[FragmentKind.AMOUNT] == "4.200"
    assert underlined[FragmentKind.INSTALLMENT_AMOUNT] == "450"
    assert underlined[FragmentKind.INSTALLMENTS] == "10x"


def test_a_lone_installment_value_after_the_count() -> None:
    draft = parse("sofá 10x de 450")
    assert draft.installment_cents == 45000 and draft.price_cents is None
    assert draft.amount_cents == 450000
    assert not draft.ambiguities


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("pizza 1.234,56", 123456),
        ("pizza R$ 12,5", 1250),
        ("pizza R$12,50", 1250),
        ("pizza 12 reais", 1200),
        ("pizza 4.200", 420000),
        ("pizza 12.50", 1250),
        ("pizza 142", 14200),
        ("pizza 0,99", 99),
    ],
)
def test_amount_formats(text: str, cents: int) -> None:
    draft = parse(text)
    assert draft.price_cents == cents
    assert draft.description == "Pizza"


@pytest.mark.parametrize(
    "text",
    ["pizza 12 vezes", "pizza em 12 parcelas", "pizza em 12x", "pizza 12 x", "pizza 12X"],
)
def test_installment_count_words(text: str) -> None:
    draft = parse(text + " 600")
    assert draft.installments == 12
    assert draft.description == "Pizza"


def test_a_number_with_vezes_is_a_count_not_an_amount() -> None:
    draft = parse("tv 10 vezes de 300,00")
    assert draft.installments == 10 and draft.installment_cents == 30000
    assert draft.price_cents is None


def test_cash_marker() -> None:
    draft = parse("bicicleta 1.800 à vista")
    assert draft.cash and draft.installments == 1 and draft.price_cents == 180000
    assert parse("bicicleta 1800 a vista").cash


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("café 5 hoje", dt.date(2026, 10, 1)),
        ("café 5 ontem", dt.date(2026, 9, 30)),
        ("café 5 anteontem", dt.date(2026, 9, 29)),
        ("café 5 dia 1", dt.date(2026, 10, 1)),
        ("café 5 dia 5", dt.date(2026, 9, 5)),  # day 5 of October is still ahead
        ("café 5 dia 31", dt.date(2026, 8, 31)),  # September has 30 days
        ("café 5 dia 05/08", dt.date(2026, 8, 5)),
        ("café 5 05/08", dt.date(2026, 8, 5)),
        ("café 5 05/08/2025", dt.date(2025, 8, 5)),
        ("café 5 05/08/25", dt.date(2025, 8, 5)),
        ("café 5 2026-08-05", dt.date(2026, 8, 5)),
    ],
)
def test_dates(text: str, expected: dt.date) -> None:
    draft = parse(text)
    assert draft.on_date == expected
    assert draft.price_cents == 500
    assert draft.description == "Café"


def test_day_five_is_not_an_amount_and_impossible_dates_are_ignored() -> None:
    draft = parse("mercado 80 dia 5")
    assert draft.price_cents == 8000 and draft.on_date == dt.date(2026, 9, 5)
    bad = parse("mercado 80 31/02")
    assert bad.on_date is None and bad.price_cents == 8000


def test_accounts_by_full_nickname_word_and_institution() -> None:
    assert parse("uber 20 inter").account_id == "inter"
    assert parse("uber 20 na CAIXINHA").account_id == "cx"
    assert parse("uber 20 bb ourocard").account_id == "bb-card"
    assert parse("uber 20 no cartão do nubank").account_id == "nu"


def test_an_ambiguous_nickname_asks_back_with_candidates() -> None:
    draft = parse("uber 20 bb")
    assert draft.account_id is None
    assert set(draft.account_candidates) == {"bb-cc", "bb-card"}
    assert draft.has_ambiguity(AmbiguityCode.ACCOUNT)
    assert understood(draft)[FragmentKind.ACCOUNT] == "bb"


def test_installments_prefer_the_card_when_the_nickname_is_shared() -> None:
    draft = parse("geladeira 4200 10x bb")
    assert draft.account_id == "bb-card"
    assert not draft.has_ambiguity(AmbiguityCode.ACCOUNT)


def test_what_if_phrases() -> None:
    draft = parse("e se eu comprar uma geladeira de 4.200 em 10x no BB")
    assert draft.what_if
    assert draft.description == "Uma geladeira"
    assert draft.price_cents == 420000 and draft.installments == 10


def test_accented_and_uppercase_words_still_match() -> None:
    assert parse("CAFÉ SÃO JOÃO 7,00 HOJE INTER").account_id == "inter"
    assert parse("Café São João 7,00").description == "Café São João"


def test_inner_linking_words_are_kept_and_edge_ones_dropped() -> None:
    assert parse("pão de queijo 8,50").description == "Pão de queijo"
    assert parse("de 30 pizza").description == "Pizza"


def test_no_kind_and_no_category_are_ever_invented() -> None:
    draft = parse("salário 5.000 hoje")
    assert not hasattr(draft, "kind") and not hasattr(draft, "category_id")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "!!!",
        "R$",
        "R$ R$ R$",
        "x",
        "10x 10x 10x",
        "0x",
        "999x 5",
        "dia",
        "dia 99",
        "dia 0",
        "///",
        "99999999999999999999999",
        "1,2,3",
        "1.2.3.4",
        "-5",
        "+5",
        "e se",
        "e",
        "à vista",
        "a vista a vista",
        "ontem hoje",
        "12/13/2026",
        "\x00\x01",
        "💸 12,50",
        "ç" * 500,
    ],
)
def test_garbage_never_raises(text: str) -> None:
    draft = parse(text)
    for f in draft.fragments:
        assert draft.text[f.start : f.end] == f.text and f.start < f.end
    assert draft.price_cents is None or draft.price_cents > 0


def test_random_garbage_never_raises_and_fragments_are_consistent() -> None:
    rng = random.Random(20261001)
    alphabet = string.ascii_letters + string.digits + " ,.-/x$çãé" * 3
    words = ["dia", "ontem", "hoje", "10x", "vezes", "de", "em", "R$", "nubank", "bb", "4.200"]
    for _ in range(3000):
        pieces = [
            rng.choice(words)
            if rng.random() < 0.4
            else "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
            for _ in range(rng.randint(0, 9))
        ]
        draft = parse(" ".join(pieces))
        spans = sorted((f.start, f.end) for f in draft.fragments)
        for (_, end), (start, _) in pairwise(spans):
            assert end <= start  # fragments never overlap
        for f in draft.fragments:
            assert draft.text[f.start : f.end] == f.text
        assert draft.price_cents is None or 0 < draft.price_cents <= 100_000_000_00
        assert draft.installments is None or 1 <= draft.installments <= 120


def test_amounts_round_trip_through_formatting() -> None:
    from financas.domain.money import format_brl

    rng = random.Random(7)
    for _ in range(300):
        cents = rng.randint(1, 99_999_999)
        text = format_brl(cents).removeprefix("R$ ")
        assert parse(f"item {text}").price_cents == cents
        assert parse(f"item R$ {text}").price_cents == cents
