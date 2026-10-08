"""CSV feed, v1.1.0 columns: merchant, refunded purchases and itemized purchases (one parent row
plus item rows sharing a group id), with the legacy flat files untouched."""

from dataclasses import replace

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.csvfeed import (
    ApplyFeed,
    FeedAnalysis,
    analyze_feed,
    load_context,
    snapshot,
    verify_feed,
)
from financas.domain.models import Account, Transaction
from financas.domain.services.splits import allocations
from financas.interfaces import messages
from financas.interfaces.web.routes.importar import issue_text

LEGACY_HEADER = (
    "date;kind;account;to_account;amount;description;category;recurring;notes;statement;"
    "installments;installment_number;amount_type;gross_amount\n"
)
HEADER = LEGACY_HEADER.rstrip("\n") + ";merchant;refunded;group\n"


def analyze(uow: MemoryUnitOfWork, clock: FixedClock, text: str) -> FeedAnalysis:
    return analyze_feed(text.encode(), load_context(uow, clock))


def apply(uow: MemoryUnitOfWork, clock: FixedClock, text: str) -> FeedAnalysis:
    analysis = analyze(uow, clock, text)
    assert analysis.ok, analysis.issues
    assert analysis.plan is not None
    before = snapshot(uow, analysis.plan)
    ApplyFeed(uow, clock).execute(analysis.plan, "Pagamento de fatura")
    verification = verify_feed(uow, analysis.plan, before)
    assert verification.ok, [c for c in verification.checks if not c.ok]
    return analysis


def entries_of(uow: MemoryUnitOfWork, account: Account) -> list[Transaction]:
    with uow as work:
        return sorted(
            work.transactions.list_by_account(account.id, include_refunded=True),
            key=lambda t: (t.posted_on, t.description),
        )


def codes(analysis: FeedAnalysis) -> list[tuple[str, int | None, str | None]]:
    return [(i.code, i.line, i.column) for i in analysis.issues]


# --- legacy files keep working -------------------------------------------------------------------


def test_a_legacy_file_without_the_new_columns_parses_and_applies_as_before(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, card: Account
) -> None:
    text = LEGACY_HEADER + "\n".join(
        [
            "2026-06-01;expense;Conta Corrente;;-100,00;Aluguel;Casa;yes;;;;;;",
            "2026-06-02;income;Conta Corrente;;2.000,00;Salário;;;;;;;;",
            "2026-06-10;expense;Cartão;;301,00;Fone;Compras;;;;3;;total;",
            "2026-06-11;expense;Cartão;;50,00;Mercado;Supermercado;;;;;;;",
        ]
    )
    analysis = apply(uow, clock, text + "\n")
    plan = analysis.plan
    assert plan is not None and (plan.itemized, plan.refunded) == (0, 0)
    rent, salary = entries_of(uow, checking)
    assert (rent.amount_cents, rent.merchant, rent.is_refunded) == (-10_000, None, False)
    assert salary.category_id is not None  # the default category of the kind
    assert len(entries_of(uow, card)) == 4  # three installments and the market


def test_every_alias_of_the_new_columns_is_understood(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, card: Account
) -> None:
    text = (
        "data;tipo;cartao;valor;descricao;categoria;estabelecimento;estornado;id_agrupamento\n"
        "2026-06-10;despesa;Cartão;89,90;Fone;Compras;kabum;sim;\n"
        "2026-06-11;despesa;Cartão;100,00;Compra grande;;Kabum;;pedido-7\n"
        ";;;60,00;Mouse;Compras;;;pedido-7\n"
        ";;;40,00;Cabo;Casa;;;pedido-7\n"
    )
    analysis = apply(uow, clock, text)
    assert analysis.plan is not None
    assert (analysis.plan.itemized, analysis.plan.refunded) == (1, 1)
    fone, big = entries_of(uow, card)
    assert (fone.merchant, fone.is_refunded) == ("Kabum", True)
    assert big.category_id is None and big.amount_cents == -10_000


# --- merchant ------------------------------------------------------------------------------------


def test_a_typed_merchant_gets_its_canonical_spelling(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = HEADER + "\n".join(
        [
            "2026-06-01;expense;Conta Corrente;;-30,00;Livro;Compras;;;;;;;;mercadolivre;;",
            "2026-06-02;expense;Conta Corrente;;-12,00;Padaria;Supermercado;;;;;;;;Padaria do Zé;;",
            "2026-06-03;income;Conta Corrente;;50,00;Venda;;;;;;;;;Mercado Pago;;",
        ]
    )
    apply(uow, clock, text + "\n")
    livro, padaria, venda = entries_of(uow, checking)
    assert (livro.merchant, padaria.merchant, venda.merchant) == (
        "Mercado Livre",  # an alias becomes its canonical name
        "Padaria do Zé",  # any other name keeps the user's spelling
        "Mercado Pago",
    )


def test_a_blank_merchant_is_inferred_from_the_description_and_its_suffix(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = HEADER + "\n".join(
        [
            "2026-06-01;expense;Conta Corrente;;-30,00;PAG*MERCADOLIVRE 123;Compras;;;;;;;;;;",
            "2026-06-02;expense;Conta Corrente;;-80,00;Mouse Gamer - Kabum;Compras;;;;;;;;;;",
            "2026-06-03;expense;Conta Corrente;;-9,00;Cafezinho;Supermercado;;;;;;;;;;",
            "2026-06-04;income;Conta Corrente;;99,00;Mercado Livre repasse;;;;;;;;;;;",
        ]
    )
    apply(uow, clock, text + "\n")
    by_description = {t.description: t for t in entries_of(uow, checking)}
    assert by_description["PAG*MERCADOLIVRE 123"].merchant == "Mercado Livre"
    assert by_description["Mouse Gamer"].merchant == "Kabum"  # the suffix left the description
    assert by_description["Cafezinho"].merchant is None  # nothing to go on: left blank
    assert by_description["Mercado Livre repasse"].merchant is None  # incomes: typed only


def test_a_merchant_that_is_too_long_is_a_line_error(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = HEADER + f"2026-06-01;expense;Conta Corrente;;-30,00;Livro;;;;;;;;;{'x' * 121};;\n"
    assert codes(analyze(uow, clock, text)) == [("FIELD_TOO_LONG", 2, "merchant")]


# --- refunded purchases --------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["sim", "true", "1", "s", "yes", "x"])
def test_truthy_values_mark_a_purchase_as_refunded(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, flag: str
) -> None:
    text = HEADER + f"2026-06-01;expense;Conta Corrente;;-30,00;Livro;Compras;;;;;;;;;{flag};\n"
    apply(uow, clock, text)
    (entry,) = entries_of(uow, checking)
    assert entry.is_refunded and entry.amount_cents == -3_000


def test_a_refunded_purchase_counts_nowhere_but_stays_on_record(
    uow: MemoryUnitOfWork, clock: FixedClock, card: Account
) -> None:
    from financas.application.queries.summary import GetSummary, Period
    from financas.domain.money import YearMonth

    text = HEADER + "\n".join(
        [
            "2026-06-10;expense;Cartão;;-100,00;Fone;Compras;;;;;;;;;sim;",
            "2026-06-11;expense;Cartão;;-50,00;Mercado;Supermercado;;;;;;;;;não;",
        ]
    )
    analysis = apply(uow, clock, text + "\n")
    assert analysis.plan is not None and analysis.plan.refunded == 1
    (touch,) = analysis.plan.statements
    assert (touch.entries, touch.owed_cents) == (2, 5_000)  # owed: only the market
    assert len(entries_of(uow, card)) == 2  # both are on record
    june = GetSummary(uow).execute(Period.month(YearMonth(2026, 6)))
    assert june.expenses_cents == 5_000


@pytest.mark.parametrize(
    ("row", "code", "column"),
    [
        ("2026-06-01;expense;Conta Corrente;;-30,00;Livro;;;;;;;;;;talvez;", "INVALID_BOOLEAN", "refunded"),
        ("2026-06-01;income;Conta Corrente;;30,00;Venda;;;;;;;;;;sim;", "NOT_ALLOWED_FOR_KIND", "refunded"),
        ("2026-06-01;expense;Cartão;;-90,00;Notebook;;;;;3;;total;;;sim;", "REFUNDED_NOT_FOR_INSTALLMENTS", "refunded"),
    ],
)  # fmt: skip
def test_refunded_is_only_for_plain_expenses(
    uow: MemoryUnitOfWork,
    clock: FixedClock,
    checking: Account,
    card: Account,
    row: str,
    code: str,
    column: str,
) -> None:
    assert codes(analyze(uow, clock, HEADER + row + "\n")) == [(code, 2, column)]


# --- itemized purchases --------------------------------------------------------------------------

ITEMIZED = HEADER + "\n".join(
    [
        "2026-06-05;expense;Conta Corrente;;450,00;Setup gamer;;;;;;;;;Kabum;;pedido-1",
        ";;;;350,00;Monitor Gamer;Compras;;;;;;;;;;pedido-1",
        ";;;;100,00;Cabo HDMI e Suporte;Casa;;;;;;;;;;pedido-1",
    ]
)


def test_a_group_becomes_one_parent_without_category_and_its_items(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    analysis = apply(uow, clock, ITEMIZED + "\n")
    assert analysis.plan is not None
    assert (analysis.plan.itemized, analysis.plan.rows_by_kind) == (1, {_expense(): 1})
    (parent,) = entries_of(uow, checking)
    assert parent.category_id is None  # the items carry the categories (9.10)
    assert (parent.amount_cents, parent.merchant, parent.description) == (
        -45_000,
        "Kabum",
        "Setup gamer",
    )
    with uow as work:
        items = work.transactions.splits_for([parent.id])[parent.id]
        names = {c.id: c.slug for c in work.categories.list_all()}
    assert [(i.description, i.amount_cents, names[i.category_id]) for i in items] == [
        ("Monitor Gamer", 35_000, "shopping"),
        ("Cabo HDMI e Suporte", 10_000, "home"),
    ]
    spending = allocations(parent, {parent.id: items})  # analytics see the items, never the parent
    assert sorted((names[c], cents) for c, cents in spending) == [
        ("home", -10_000),
        ("shopping", -35_000),
    ]


def _expense():
    from financas.application.csvfeed import FeedAnalysis  # noqa: F401  (type only)
    from financas.application.csvfeed.model import FeedKind

    return FeedKind.EXPENSE


def test_an_itemized_card_purchase_in_installments_is_split_to_the_cent(
    uow: MemoryUnitOfWork, clock: FixedClock, card: Account
) -> None:
    text = HEADER + "\n".join(
        [
            "2026-06-05;expense;Cartão;;450,00;Setup gamer;;;;;3;;total;;Kabum;;p-1",
            ";;;;350,00;Monitor Gamer;Compras;;;;;;;;;;p-1",
            ";;;;100,00;Cabo HDMI e Suporte;Casa;;;;;;;;;;p-1",
        ]
    )
    analysis = apply(uow, clock, text + "\n")
    assert analysis.plan is not None and (analysis.plan.itemized, analysis.plan.purchases) == (1, 1)
    entries = entries_of(uow, card)
    assert [-t.amount_cents for t in entries] == [15_000] * 3
    assert all(t.category_id is None and t.merchant == "Kabum" for t in entries)
    with uow as work:
        splits = work.transactions.splits_for([t.id for t in entries])
    rows = [[(i.description, i.amount_cents) for i in splits[t.id]] for t in entries]
    assert rows == [  # the spec example: 117,00 + 33,00 · 117,00 + 33,00 · 116,00 + 34,00
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],
        [("Monitor Gamer", 11_667), ("Cabo HDMI e Suporte", 3_333)],
        [("Monitor Gamer", 11_666), ("Cabo HDMI e Suporte", 3_334)],
    ]


def test_an_itemized_card_purchase_goes_to_one_statement_with_only_the_parent_counted(
    uow: MemoryUnitOfWork, clock: FixedClock, card: Account
) -> None:
    text = ITEMIZED.replace("Conta Corrente", "Cartão") + "\n"
    analysis = apply(uow, clock, text)
    assert analysis.plan is not None
    (touch,) = analysis.plan.statements
    assert (touch.entries, touch.owed_cents) == (1, 45_000)


def test_the_sum_of_the_items_must_match_the_total_of_the_purchase(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = ITEMIZED.replace("100,00;Cabo", "90,00;Cabo") + "\n"
    analysis = analyze(uow, clock, text)
    assert analysis.plan is None
    assert codes(analysis) == [("SPLIT_GROUP_SUM_MISMATCH", 2, None)]  # the line of the purchase
    (issue,) = analysis.issues
    assert issue.params == {"sum_cents": 44_000, "total_cents": 45_000}
    assert messages.render_error(messages.DomainError(issue.code, **issue.params)) == (
        "A soma dos subitens (R$ 440,00) não confere com o total da compra (R$ 450,00)."
    )
    assert issue_text(issue) == (
        "Linha 2: A soma dos subitens (R$ 440,00) não confere com o total da compra (R$ 450,00)."
    )
    assert entries_of(uow, checking) == []  # nothing was written


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (  # the purchase row carries a category
            lambda t: t.replace("Setup gamer;;", "Setup gamer;Compras;"),
            [("PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS", 2, "category")],
        ),
        (  # one item only
            lambda t: t.rstrip("\n").rsplit("\n", 1)[0] + "\n",
            [("SPLIT_NEEDS_TWO_ITEMS", 2, "group")],
        ),
        (  # an item without category
            lambda t: t.replace("Monitor Gamer;Compras", "Monitor Gamer;"),
            [("REQUIRED_FIELD", 3, "category")],
        ),
        (  # an item with a category that does not exist
            lambda t: t.replace("Monitor Gamer;Compras", "Monitor Gamer;Inexistente"),
            [("UNKNOWN_CATEGORY", 3, "category")],
        ),
        (  # an item with a column of its own that only the purchase may fill
            lambda t: t.replace(";350,00;Monitor Gamer;Compras;;", ";350,00;Monitor Gamer;Compras;;obs"),
            [("NOT_ALLOWED_FOR_SPLIT_ITEM", 3, "notes")],
        ),
        (  # an item on another day
            lambda t: t.replace("\n;;;;350,00", "\n2026-06-06;;;;350,00"),
            [("SPLIT_ITEM_MISMATCH", 3, "date")],
        ),
        (  # an item with a sign that contradicts an expense
            lambda t: t.replace("350,00;Monitor", "+350,00;Monitor"),
            [("SIGN_KIND_MISMATCH", 3, "amount")],
        ),
        (  # the group id used again further down
            lambda t: t
            + "2026-06-06;expense;Conta Corrente;;-9,00;Café;Supermercado;;;;;;;;;;\n"
            + "2026-06-07;expense;Conta Corrente;;-10,00;Outra;Casa;;;;;;;;;;pedido-1\n",
            [("SPLIT_GROUP_NOT_CONSECUTIVE", 6, "group")],
        ),
        (  # installment value instead of the total: the items are totals of the purchase
            lambda t: t.replace("Setup gamer;;;;;;;;;Kabum", "Setup gamer;;;;;3;;installment;;Kabum"),
            [("SPLIT_REQUIRES_TOTAL_AMOUNT", 2, "amount_type")],
        ),
    ],
)  # fmt: skip
def test_malformed_groups_are_refused_with_the_line_and_column(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, edit, expected
) -> None:
    assert codes(analyze(uow, clock, edit(ITEMIZED + "\n"))) == expected


def test_only_expenses_can_be_itemized(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = ITEMIZED.replace("expense", "income") + "\n"
    found = codes(analyze(uow, clock, text))
    assert ("NOT_ALLOWED_FOR_KIND", 2, "group") in found


def test_a_group_with_a_single_row_is_an_error_not_a_flat_entry(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = HEADER + "2026-06-05;expense;Conta Corrente;;45,00;Setup;;;;;;;;;;;solo\n"
    assert codes(analyze(uow, clock, text)) == [("SPLIT_NEEDS_TWO_ITEMS", 2, "group")]


def test_flat_rows_and_groups_can_share_a_file_and_the_plan_counts_each_purchase_once(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = (
        ITEMIZED + "\n" + "2026-06-06;expense;Conta Corrente;;-9,00;Café;Supermercado;;;;;;;;;;\n"
    )
    analysis = apply(uow, clock, text)
    assert analysis.plan is not None
    assert analysis.plan.transactions == 2  # the purchase (items are not transactions) and the café
    assert replace(analysis).data_rows == 4  # lines of data in the file, items included


# --- payment method (metodo_pagamento) and splits on a bank account --------------------------------

METHOD_HEADER = (
    "data;tipo;conta;valor;descricao;categoria;metodo_pagamento;estabelecimento;estornado;"
    "id_agrupamento\n"
)


def method_of(uow: MemoryUnitOfWork, account: Account) -> dict[str, str | None]:
    return {
        t.description: t.payment_method.value if t.payment_method else None
        for t in entries_of(uow, account)
    }


def test_a_typed_payment_method_is_stored_with_every_alias_of_the_column(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    for header in ("metodo_pagamento", "metodo", "forma_pagamento", "payment_method"):
        uow.transactions.items.clear()  # type: ignore[attr-defined]
        text = (
            f"data;tipo;conta;valor;descricao;categoria;{header}\n"
            "2026-06-01;despesa;Conta Corrente;-30,00;Feira;Supermercado;pix\n"
            "2026-06-02;despesa;Conta Corrente;-650,00;Condomínio;Casa;Boleto\n"
            "2026-06-03;despesa;Conta Corrente;-32,50;Padaria Pão Quente;Alimentação;Débito\n"
            "2026-06-04;despesa;Conta Corrente;-10,00;Taxa;Encargos;TED\n"
        )
        apply(uow, clock, text)
        assert method_of(uow, checking) == {
            "Feira": "pix",
            "Condomínio": "boleto",
            "Padaria Pão Quente": "debito",
            "Taxa": "ted",
        }, header


def test_a_blank_method_comes_from_the_wording_then_from_the_account_then_pix(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, card: Account
) -> None:
    text = METHOD_HEADER + "\n".join(
        [
            "2026-06-01;despesa;Conta Corrente;-10,00;PIX TRANSF MARIA;Outros;;;;",
            "2026-06-02;despesa;Conta Corrente;-20,00;PAGTO ELETRON COBRANCA LUZ;Casa;;;;",
            "2026-06-03;despesa;Conta Corrente;-30,00;COMPRA DEBITO MERCADO;Supermercado;;;;",
            "2026-06-04;despesa;Conta Corrente;-40,00;Livro;Compras;;;;",
            "2026-06-05;despesa;Conta Corrente;-50,00;Pix no cartão?;Outros;boleto;;;",
            "2026-06-05;despesa;Cartão;-60,00;PIX do cartão;Compras;;;;",
            "2026-06-06;receita;Conta Corrente;100,00;Salário;Salário;;;;",
        ]
    )
    apply(uow, clock, text + "\n")
    assert method_of(uow, checking) == {
        "PIX TRANSF MARIA": "pix",
        "PAGTO ELETRON COBRANCA LUZ": "boleto",
        "COMPRA DEBITO MERCADO": "debito",
        "Livro": "pix",  # the fallback
        "Pix no cartão?": "boleto",  # typed wins over the wording
        "Salário": "pix",
    }
    assert method_of(uow, card) == {"PIX do cartão": "cartao_credito"}  # a card is a card


def test_a_method_that_does_not_fit_the_account_is_refused_with_line_and_column(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account, card: Account
) -> None:
    text = METHOD_HEADER + "\n".join(
        [
            "2026-06-01;despesa;Conta Corrente;-10,00;A;Outros;cartao_credito;;;",
            "2026-06-02;despesa;Cartão;-10,00;B;Outros;pix;;;",
        ]
    )
    assert codes(analyze(uow, clock, text + "\n")) == [
        ("INVALID_PAYMENT_METHOD", 2, "payment_method"),  # a card method on a bank account
        ("INVALID_PAYMENT_METHOD", 3, "payment_method"),  # a bank method on a card
    ]
    unknown = METHOD_HEADER + "2026-06-03;despesa;Conta Corrente;-10,00;C;Outros;cheque;;;\n"
    assert codes(analyze(uow, clock, unknown)) == [("INVALID_PAYMENT_METHOD", 2, "payment_method")]
    assert entries_of(uow, checking) == []


def test_only_entries_take_a_method_not_transfers_or_balances(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = (
        "data;tipo;conta;conta_destino;valor;descricao;metodo_pagamento\n"
        "2026-06-01;transferencia;Conta Corrente;;10,00;Aporte;pix\n"
    )
    assert ("NOT_ALLOWED_FOR_KIND", 2, "payment_method") in codes(analyze(uow, clock, text))


def test_a_pix_split_on_a_checking_account_debits_the_total_once(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = METHOD_HEADER + "\n".join(
        [
            "2026-06-04;despesa;Conta Corrente;-180,00;Feira de sábado;;pix;;;sab-1",
            ";;;110,00;Hortifruti;Supermercado;;;;sab-1",
            ";;;70,00;Açougue;Supermercado;;;;sab-1",
        ]
    )
    apply(uow, clock, text + "\n")
    (parent,) = entries_of(uow, checking)
    assert (parent.amount_cents, parent.category_id) == (-18_000, None)
    assert parent.payment_method is not None and parent.payment_method.value == "pix"
    with uow as work:
        items = work.transactions.splits_for([parent.id])[parent.id]
        balance = sum(m[1] for m in work.transactions.movements(checking.id))
    assert [(i.description, i.amount_cents) for i in items] == [
        ("Hortifruti", 11_000),
        ("Açougue", 7_000),
    ]
    assert balance == -18_000  # one debit: the items move no balance
    assert allocations(parent, {parent.id: items}) is not None


def test_a_pix_split_that_does_not_add_up_is_refused(
    uow: MemoryUnitOfWork, clock: FixedClock, checking: Account
) -> None:
    text = METHOD_HEADER + "\n".join(
        [
            "2026-06-04;despesa;Conta Corrente;-180,00;Feira;;pix;;;sab-1",
            ";;;110,00;Hortifruti;Supermercado;;;;sab-1",
            ";;;60,00;Açougue;Supermercado;;;;sab-1",
        ]
    )
    assert codes(analyze(uow, clock, text + "\n")) == [("SPLIT_GROUP_SUM_MISMATCH", 2, None)]


def test_the_template_explains_the_new_column_and_has_pix_split_boleto_and_installment_examples() -> (
    None
):
    from financas.application.csvfeed.template import template_text

    text = template_text()
    header = text.splitlines()[0].split(";")
    assert header.index("payment_method") < header.index("merchant")
    assert "debito" in text and "boleto" in text and "pix-1" in text and "installment" in text
