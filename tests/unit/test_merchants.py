"""Merchants: stored clean, suggested once each, and ranked (queries/merchants.py)."""

import datetime as dt
import unicodedata

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.merchants import GetTopMerchants, ListMerchants
from financas.application.queries.summary import Period
from financas.application.use_cases._common import UNSET
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
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
from financas.domain.services.text import clean_merchant

D = dt.date
K = TransactionKind
JULY = Period.month(YearMonth(2026, 7))


def cat(uow: MemoryUnitOfWork, slug: str) -> str:
    found = uow.categories.get_by_slug(slug)
    assert found
    return found.id


def spend(
    uow: MemoryUnitOfWork,
    account: Account,
    cents: int,
    merchant: str | None,
    slug: str = "groceries",
    day: int = 10,
    **kw: object,
) -> Transaction:
    values: dict[str, object] = {
        "account_id": account.id,
        "posted_on": D(2026, 7, day),
        "kind": K.EXPENSE,
        "amount_cents": cents,
        "description": f"compra {cents}",
        "category_id": cat(uow, slug),
        "merchant": merchant,
    }
    values.update(kw)
    return RegisterTransaction(uow).execute(RegisterTransactionCommand(**values))  # type: ignore[arg-type]


# --- storing ---


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("  Amazon  ", "Amazon"),
        ("Pão   de\tAçúcar", "Pão de Açúcar"),
        (unicodedata.normalize("NFD", "Café São João"), "Café São João"),  # NFD -> NFC
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_merchant_is_nfc_trimmed_and_empty_means_none(
    typed: str | None, stored: str | None
) -> None:
    assert clean_merchant(typed) == stored


def test_a_merchant_over_120_characters_is_refused() -> None:
    assert clean_merchant("x" * 120) == "x" * 120
    with pytest.raises(DomainError) as exc:
        clean_merchant("x" * 121)
    assert exc.value.code == "MERCHANT_TOO_LONG"


def test_register_stores_the_clean_merchant(uow: MemoryUnitOfWork, checking: Account) -> None:
    entry = spend(uow, checking, 1_000, "  Posto   Ipiranga ")
    assert uow.transactions.get(entry.id).merchant == "Posto Ipiranga"  # type: ignore[union-attr]
    assert spend(uow, checking, 500, None).merchant is None


def test_card_purchase_gives_every_installment_the_merchant(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id, "Fone", D(2026, 7, 10), installments=3, total_cents=3_000, merchant=" Amazon "
        )
    )
    assert [t.merchant for t in result.transactions] == ["Amazon"] * 3


def test_update_sets_keeps_and_clears_the_merchant(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 1_000, "Amazon")
    update = UpdateTransaction(uow, FixedClock(D(2026, 7, 20)))

    def run(merchant: object) -> Transaction:
        return update.execute(
            UpdateTransactionCommand(
                entry.id,
                entry.posted_on,
                1_000,
                entry.description,
                merchant=merchant,  # type: ignore[arg-type]
            )
        )

    assert run(UNSET).merchant == "Amazon"  # not said: kept
    assert run(" Mercado Livre ").merchant == "Mercado Livre"
    assert run("").merchant is None  # cleared
    assert run("Amazon").merchant == "Amazon"
    assert run(None).merchant is None


def test_a_new_merchant_spreads_over_the_plan_with_the_other_metadata(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Fone", D(2026, 7, 10), installments=3, total_cents=3_000)
    )
    first = result.transactions[0]
    UpdateTransaction(uow, FixedClock(D(2026, 7, 1))).execute(
        UpdateTransactionCommand(
            first.id, first.posted_on, 1_000, first.description, merchant="Amazon"
        )
    )
    assert [t.merchant for t in uow.transactions.list_by_plan(first.plan_id or "")] == [
        "Amazon"
    ] * 3


def test_merging_keeps_a_merchant_only_when_they_share_it(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    a = spend(uow, checking, 1_000, "Amazon")
    b = spend(uow, checking, 2_000, "AMAZON")  # the same merchant, another spelling
    c = spend(uow, checking, 3_000, "Uber")
    merge = MergeTransactions(uow, FixedClock(D(2026, 7, 20)))
    assert merge.execute(MergeCommand((a.id, b.id), "Compras", D(2026, 7, 12))).merchant == "Amazon"
    d = spend(uow, checking, 500, "Amazon")
    assert merge.execute(MergeCommand((c.id, d.id), "Mistas", D(2026, 7, 12))).merchant is None


# --- suggestions ---


def test_distinct_merchants_are_unique_trimmed_and_sorted_ignoring_accents(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    for name in ("Zé do Pão", "amazon", "Amazon", "Amazon", "Ação Digital", "Uber"):
        spend(uow, checking, 100, name)
    spend(uow, card, 100, "Posto Ipiranga")
    spend(uow, checking, 100, None)
    names = ListMerchants(uow).execute()
    assert names == ["Ação Digital", "Amazon", "Posto Ipiranga", "Uber", "Zé do Pão"]
    # "Amazon" won over "amazon": the spelling used most often
    assert ListMerchants(uow).execute(card.id) == ["Posto Ipiranga"]
    assert ListMerchants(uow).execute("nope") == []


# --- the ranking ---


def test_top_merchants_totals_shares_counts_and_average_ticket(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    spend(uow, checking, 10_000, "Amazon", "shopping")
    spend(uow, checking, 5_001, "amazon", "shopping")  # same merchant, another spelling
    spend(uow, checking, 9_000, "Amazon", "other")
    spend(uow, checking, 6_000, "Uber", "transport")
    spend(uow, checking, 4_000, None, "food")  # no merchant: the unidentified bucket
    spend(uow, checking, 1_000, "Fora do mês", "food", day=10)
    spend(uow, checking, 99_000, "Agosto", "food", day=10, posted_on=D(2026, 8, 10))
    result = GetTopMerchants(uow).execute(JULY)
    assert result.total_expenses_cents == 10_000 + 5_001 + 9_000 + 6_000 + 4_000 + 1_000
    amazon, uber, *rest = result.rows
    assert (amazon.merchant, amazon.total_spent_cents, amazon.transaction_count) == (
        "Amazon",
        24_001,
        3,
    )
    assert amazon.average_ticket_cents == 8_000  # 24 001 / 3 = 8 000,33: to the nearest cent
    assert amazon.percentage_of_total == pytest.approx(24_001 / 35_001)
    assert amazon.top_category_name == "Compras"  # two of the three entries
    assert (uber.merchant, uber.total_spent_cents, uber.average_ticket_cents) == (
        "Uber",
        6_000,
        6_000,
    )
    assert [r.merchant for r in rest] == ["Fora do mês"]
    assert result.merchant_count == 3
    other = result.unidentified
    assert other and other.merchant is None and other.total_spent_cents == 4_000
    assert other.transaction_count == 1 and other.top_category_name == "Alimentação"
    shares = sum(r.percentage_of_total for r in result.rows) + other.percentage_of_total
    assert shares == pytest.approx(1.0)


def test_average_ticket_rounds_half_up_to_the_cent(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    spend(uow, checking, 101, "Padaria")
    spend(uow, checking, 100, "Padaria")  # 201 / 2 = 100,5 -> 101
    spend(uow, checking, 100, "Banca")
    spend(uow, checking, 100, "Banca")
    spend(uow, checking, 100, "Banca")
    spend(uow, checking, 101, "Banca")  # 401 / 4 = 100,25 -> 100
    rows = {r.merchant: r for r in GetTopMerchants(uow).execute(JULY).rows}
    assert rows["Padaria"].average_ticket_cents == 101
    assert rows["Banca"].average_ticket_cents == 100


def test_refunds_transfers_income_and_refunded_purchases_are_left_out(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    from financas.application.use_cases.transactions import (
        RegisterTransfer,
        RegisterTransferCommand,
    )

    keep = spend(uow, checking, 5_000, "Amazon")
    gone = spend(uow, checking, 7_000, "Amazon")
    UpdateTransaction(uow, FixedClock(D(2026, 7, 20))).execute(
        UpdateTransactionCommand(gone.id, gone.posted_on, 7_000, gone.description, is_refunded=True)
    )
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 7, 11), K.REFUND, 1_000, "Estorno", merchant="Amazon"
        )
    )
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id,
            D(2026, 7, 11),
            K.INCOME,
            9_000,
            "Salário",
            cat(uow, "salary"),
            merchant="Empresa",
        )
    )
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 12), 3_000)
    )
    result = GetTopMerchants(uow).execute(JULY)
    assert result.total_expenses_cents == keep.amount_cents * -1 == 5_000
    assert [(r.merchant, r.total_spent_cents) for r in result.rows] == [("Amazon", 5_000)]
    assert result.unidentified is None


def test_the_limit_and_the_order(uow: MemoryUnitOfWork, checking: Account) -> None:
    for index, name in enumerate(("Beta", "Alfa", "Gama", "Delta")):
        spend(uow, checking, 1_000 * (index + 1) if name != "Beta" else 4_000, name)
    full = GetTopMerchants(uow).execute(JULY)
    assert [r.merchant for r in full.rows] == ["Beta", "Delta", "Gama", "Alfa"]  # Beta ties Delta
    top2 = GetTopMerchants(uow).execute(JULY, limit=2)
    assert [r.merchant for r in top2.rows] == ["Beta", "Delta"] and top2.merchant_count == 4


def test_card_purchases_count_in_their_statement_month(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id, "Fone", D(2026, 7, 26), installments=2, total_cents=2_000, merchant="Amazon"
        )
    )  # July 26 is after the closing: statements 2026-08 and 2026-09
    august = GetTopMerchants(uow).execute(Period.month(YearMonth(2026, 8)))
    september = GetTopMerchants(uow).execute(Period.month(YearMonth(2026, 9)))
    assert [(r.merchant, r.total_spent_cents) for r in august.rows] == [("Amazon", 1_000)]
    assert [(r.merchant, r.total_spent_cents) for r in september.rows] == [("Amazon", 1_000)]
    assert GetTopMerchants(uow).execute(JULY).rows == []


def test_an_itemized_entry_counts_once_for_the_merchant_and_per_item_for_the_category(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    run = spend(uow, checking, 38_000, "Pão de Açúcar", "groceries")
    UpdateTransaction(uow, FixedClock(D(2026, 7, 20))).execute(
        UpdateTransactionCommand(
            run.id,
            run.posted_on,
            38_000,
            run.description,
            splits=(
                SplitItem("Feira", cat(uow, "groceries"), 22_000),
                SplitItem("Higiene", cat(uow, "health"), 9_000),
                SplitItem("Limpeza", cat(uow, "home"), 7_000),
            ),
        )
    )
    (row,) = GetTopMerchants(uow).execute(JULY).rows
    assert (row.total_spent_cents, row.transaction_count) == (38_000, 1)
    assert row.top_category_name is not None  # one of the three items' categories


# --- inference engine (domain/services/merchants.py) ---

from financas.application.use_cases.merchants import (  # noqa: E402
    BackfillMerchants,
    EnrichCommand,
    EnrichTransaction,
)
from financas.domain.services.merchants import (  # noqa: E402
    BackfillRow,
    ChangeReason,
    alias_of,
    build_lookup,
    canonical_merchant,
    infer_merchant,
    plan_backfill,
    settle_description,
    split_suffix,
    strip_gateway,
)


@pytest.mark.parametrize(
    ("raw", "stripped"),
    [
        ("PG *LOJA XYZ", "LOJA XYZ"),
        ("PAG*MERCADOLIVRE 123", "MERCADOLIVRE 123"),
        ("MP*UBER TRIP", "UBER TRIP"),
        ("cielo * Padaria", "Padaria"),
        ("STONE*Bar do Zé", "Bar do Zé"),
        ("SUMUP *Feira", "Feira"),
        ("Pagamento Shopee", "Pagamento Shopee"),  # "pag" without the star is not a gateway
        ("Wi-Fi", "Wi-Fi"),
    ],
)
def test_gateway_prefixes_are_stripped(raw: str, stripped: str) -> None:
    assert strip_gateway(raw) == stripped


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("mercadolivre", "Mercado Livre"),
        ("MERCADO LIVRE", "Mercado Livre"),
        ("PAG*MERCADOLIVRE 123", "Mercado Livre"),
        ("Mercado Livre SP", "Mercado Livre"),
        ("SHOPEE *BR", "Shopee"),
        ("shopee.com.br", "Shopee"),
        ("UBER *TRIP 8842", "Uber"),
        ("uber", "Uber"),
        ("UBER EATS", "Uber Eats"),  # not folded into Uber
        ("IFOOD *RESTAURANTE", "iFood"),
        ("ifood.com.br", "iFood"),
        ("AMAZON.COM.BR", "Amazon"),
        ("Amazon Prime", "Amazon"),
        ("MP*KABUM", "Kabum"),
    ],
)
def test_aliases_become_the_canonical_name(raw: str, canonical: str) -> None:
    assert alias_of(raw) == canonical and canonical_merchant(raw) == canonical


def test_unknown_names_keep_the_users_casing_and_only_lose_the_gateway() -> None:
    assert alias_of("Padaria do Zé") is None
    assert canonical_merchant("PG *Padaria do Zé") == "Padaria do Zé"
    assert canonical_merchant("  Pão   de Açúcar ") == "Pão de Açúcar"
    assert canonical_merchant("   ") is None


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Mouse Gamer - Kabum", ("Mouse Gamer", "Kabum")),
        ("Mouse Gamer  -  kabum", ("Mouse Gamer", "Kabum")),  # alias: canonical spelling
        (
            "Compra do mês - Supermercado Pão de Açúcar",
            ("Compra do mês", "Supermercado Pão de Açúcar"),
        ),
        ("Fone - Amazon.com.br", ("Fone", "Amazon")),
        ("Jantar - fim - Outback", ("Jantar - fim", "Outback")),  # only the last part
    ],
)
def test_a_trailing_merchant_is_split_from_the_description(
    description: str, expected: tuple[str, str]
) -> None:
    split = split_suffix(description)
    assert split and (split.description, split.merchant) == expected


@pytest.mark.parametrize(
    "description",
    [
        "Wi-Fi",  # no spaces around the hyphen
        "Pré-pago",
        "Aluguel - Outubro",  # a month
        "Fatura - set",
        "Parcela - 3/10",  # an installment
        "Boleto - 2026",  # a number
        "Compra - X",  # too short to be a name
        "Compra - " + "x" * 41,  # too long
        "- Kabum",  # nothing left of it
        "Aluguel",
        "",
    ],
)
def test_clear_suffixes_only(description: str) -> None:
    assert split_suffix(description) is None


def test_settle_description_only_acts_when_no_merchant_was_typed() -> None:
    assert settle_description("Mouse - Kabum", None) == ("Mouse", "Kabum")
    assert settle_description("Mouse - Kabum", "Amazon") == (
        "Mouse - Kabum",
        "Amazon",
    )  # typed wins
    assert settle_description("Mouse", None) == ("Mouse", None)


def test_the_lookup_learns_from_what_the_user_already_filled_in() -> None:
    lookup = build_lookup(
        [
            ("Remédio", "Droga Raia"),
            ("Remédio", "Drogasil"),
            ("Remédio", "Drogasil"),
            ("Café São João", "Padaria Estrela"),
            ("Compra", ""),  # empty merchants teach nothing
        ]
    )
    assert infer_merchant("remedio", lookup) == "Drogasil"  # the most frequent, accents ignored
    assert infer_merchant("COMPRA DROGASIL 0451", lookup) == "Drogasil"  # a known name inside
    assert infer_merchant("pix padaria estrela sp", lookup) == "Padaria Estrela"
    assert infer_merchant("Alguma coisa sem pista", lookup) is None
    assert infer_merchant("Mouse - Kabum", lookup) == "Kabum"  # the suffix beats everything
    assert infer_merchant("SHOPEE *BR", lookup) == "Shopee"  # then the alias
    assert infer_merchant("Compra Raia", build_lookup([("x", "Ra")])) is None  # too short to match


def test_plan_backfill_orders_its_evidence_and_leaves_typed_merchants_alone() -> None:
    expense, refund, income = K.EXPENSE, K.REFUND, K.INCOME
    rows = [
        BackfillRow("a", expense, "Mouse - Kabum", None),
        BackfillRow("b", expense, "PAG*MERCADOLIVRE 123", None),
        BackfillRow("c", expense, "Remédio", None),
        BackfillRow("d", expense, "Remédio", "Drogasil"),  # what "c" learns from
        BackfillRow("e", expense, "Almoço", "mercadolivre"),  # an alias spelling: canonicalized
        BackfillRow("f", expense, "Almoço", "Restaurante do Zé"),  # typed: untouched
        BackfillRow("g", income, "Salário - Empresa", None),  # not an expense: untouched
        BackfillRow("h", refund, "Estorno SHOPEE", None),
        BackfillRow("i", expense, "Sem pista nenhuma", None),
    ]
    changes = {c.id: c for c in plan_backfill(rows)}
    assert set(changes) == {"a", "b", "c", "e", "h"}
    assert (changes["a"].merchant, changes["a"].description, changes["a"].reason) == (
        "Kabum",
        "Mouse",
        ChangeReason.SUFFIX,
    )
    assert (changes["b"].merchant, changes["b"].description) == ("Mercado Livre", None)
    assert changes["b"].reason is ChangeReason.ALIAS
    assert (changes["c"].merchant, changes["c"].reason) == ("Drogasil", ChangeReason.LEARNED)
    assert (changes["e"].merchant, changes["e"].reason) == (
        "Mercado Livre",
        ChangeReason.CANONICALIZED,
    )
    assert changes["h"].merchant == "Shopee"


# --- manual entry: the suffix rule in the use cases ---


def test_register_extracts_the_suffix_into_the_merchant(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 15_000, None, description="Mouse Gamer - Kabum")
    assert (entry.description, entry.merchant) == ("Mouse Gamer", "Kabum")
    assert entry.description_search == "mouse gamer"
    typed = spend(uow, checking, 15_000, "Amazon", description="Mouse Gamer - Kabum")
    assert (typed.description, typed.merchant) == ("Mouse Gamer - Kabum", "Amazon")  # typed wins
    income = spend(
        uow, checking, 9_000, None, kind=K.INCOME, slug="salary", description="Salário - Empresa"
    )
    assert (income.description, income.merchant) == ("Salário - Empresa", None)  # expenses only
    month = spend(uow, checking, 8_000, None, description="Aluguel - Outubro")
    assert (month.description, month.merchant) == ("Aluguel - Outubro", None)


def test_card_purchase_and_edit_extract_the_suffix_too(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id, "Teclado - Kabum", D(2026, 7, 10), installments=2, total_cents=2_000
        )
    )
    assert [(t.description, t.merchant) for t in result.transactions] == [("Teclado", "Kabum")] * 2
    assert result.plan and result.plan.description == "Teclado"
    first = result.transactions[0]
    edited = UpdateTransaction(uow, FixedClock(D(2026, 7, 1))).execute(
        UpdateTransactionCommand(
            first.id, first.posted_on, 1_000, "Teclado Mecânico - Amazon", merchant=""
        )
    )
    assert (edited.description, edited.merchant) == ("Teclado Mecânico", "Amazon")
    # an edit that does not name a merchant keeps the one it has
    kept = UpdateTransaction(uow, FixedClock(D(2026, 7, 1))).execute(
        UpdateTransactionCommand(first.id, first.posted_on, 1_000, "Teclado Mecânico")
    )
    assert kept.merchant == "Amazon"


# --- the backfill use case ---


def legacy(uow: MemoryUnitOfWork, account: Account, description: str, merchant: str | None):
    """An entry as the old data has it: written straight to the repository (no suffix rule)."""
    entry = spend(uow, account, 1_000, None, description="x")
    legacy_entry = Transaction(**{**vars(entry), "description": description, "merchant": merchant})
    uow.transactions.update(legacy_entry)
    return legacy_entry


def test_backfill_fills_in_every_legacy_entry_and_reports_counts(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    a = legacy(uow, checking, "Mouse - Kabum", None)
    b = legacy(uow, checking, "PAG*MERCADOLIVRE 123", None)
    c = legacy(uow, checking, "Remédio", None)
    d = legacy(uow, checking, "Remédio", "Drogasil")
    e = legacy(uow, checking, "Almoço", "mercadolivre")
    f = legacy(uow, checking, "Sem pista", None)
    income = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(
            checking.id, D(2026, 7, 1), K.INCOME, 5_000, "Salário - Empresa", cat(uow, "salary")
        )
    )
    dry = BackfillMerchants(uow).execute(dry_run=True)
    assert dry.dry_run and (dry.scanned, dry.changed) == (6, 4)
    assert uow.transactions.get(a.id) == a  # a dry run writes nothing
    result = BackfillMerchants(uow).execute()
    assert (result.changed, result.descriptions_cleaned) == (4, 1) and not result.dry_run
    assert result.by_reason == {
        ChangeReason.SUFFIX: 1,
        ChangeReason.ALIAS: 1,
        ChangeReason.LEARNED: 1,
        ChangeReason.CANONICALIZED: 1,
    }
    got = {t.id: t for t in uow.transactions.items.values()}
    assert (got[a.id].description, got[a.id].merchant) == ("Mouse", "Kabum")
    assert got[a.id].description_search == "mouse"
    assert (got[b.id].description, got[b.id].merchant) == ("PAG*MERCADOLIVRE 123", "Mercado Livre")
    assert got[c.id].merchant == "Drogasil" and got[d.id].merchant == "Drogasil"
    assert got[e.id].merchant == "Mercado Livre"
    assert got[f.id].merchant is None
    assert (got[income.id].description, got[income.id].merchant) == ("Salário - Empresa", None)
    # running it again finds nothing more to do
    assert BackfillMerchants(uow).execute().changed == 0


def test_backfill_keeps_a_plan_and_its_installments_in_step(
    uow: MemoryUnitOfWork, card: Account
) -> None:
    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Fone", D(2026, 7, 10), installments=2, total_cents=2_000)
    )
    assert result.plan
    for entry in result.transactions:
        uow.transactions.update(Transaction(**{**vars(entry), "description": "Fone - Amazon"}))
    uow.plans.update(type(result.plan)(**{**vars(result.plan), "description": "Fone - Amazon"}))
    BackfillMerchants(uow).execute()
    assert [(t.description, t.merchant) for t in uow.transactions.items.values()] == [
        ("Fone", "Amazon")
    ] * 2
    assert uow.plans.items[result.plan.id].description == "Fone"


def test_backfill_is_all_or_nothing(uow: MemoryUnitOfWork, checking: Account) -> None:
    legacy(uow, checking, "Mouse - Kabum", None)
    legacy(uow, checking, "Teclado - Kabum", None)
    original = dict(uow.transactions.items)
    broken = uow.transactions

    real_update = broken.update
    calls = {"n": 0}

    def flaky(transaction: Transaction) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        real_update(transaction)

    broken.update = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        BackfillMerchants(uow).execute()
    assert dict(uow.transactions.items) == original  # the first write rolled back too


# --- enriching one review card ---


def test_enrich_saves_merchant_and_category_and_normalizes_the_alias(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 1_000, None, slug="uncategorized", description="Compra")
    saved = EnrichTransaction(uow).execute(
        EnrichCommand(entry.id, "mercadolivre", cat(uow, "shopping"))
    )
    assert (saved.merchant, saved.category_id) == ("Mercado Livre", cat(uow, "shopping"))
    assert uow.transactions.items[entry.id].merchant == "Mercado Livre"
    assert uow.transactions.items[entry.id].amount_cents == entry.amount_cents  # money untouched


def test_enrich_needs_something_to_save_and_a_matching_category(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    entry = spend(uow, checking, 1_000, None, slug="uncategorized")
    with pytest.raises(DomainError) as nothing:
        EnrichTransaction(uow).execute(EnrichCommand(entry.id))
    assert nothing.value.code == "NOTHING_TO_SAVE"
    with pytest.raises(DomainError) as wrong:
        EnrichTransaction(uow).execute(EnrichCommand(entry.id, None, cat(uow, "salary")))
    assert wrong.value.code == "CATEGORY_KIND_MISMATCH"
    income = spend(uow, checking, 500, None, kind=K.INCOME, slug="salary", description="Salário")
    with pytest.raises(DomainError) as only:
        EnrichTransaction(uow).execute(EnrichCommand(income.id, "Empresa"))
    assert only.value.code == "REVIEW_ONLY_FOR_EXPENSES"
    category_only = EnrichTransaction(uow).execute(
        EnrichCommand(entry.id, None, cat(uow, "shopping"))
    )
    assert category_only.category_id == cat(uow, "shopping") and category_only.merchant is None


def test_enriching_one_installment_fills_the_whole_plan_even_on_a_paid_statement(
    uow: MemoryUnitOfWork, card: Account, checking: Account
) -> None:
    from financas.application.use_cases.cards import PayStatement, PayStatementCommand

    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(
            card.id,
            "Fone",
            D(2026, 7, 10),
            cat(uow, "uncategorized"),
            installments=3,
            total_cents=3_000,
        )
    )
    first = result.transactions[0]
    paid_on = D(2026, 7, 28)
    PayStatement(uow, FixedClock(paid_on)).execute(
        PayStatementCommand(first.statement_id or "", checking.id, paid_on)
    )  # the first installment's statement is paid: history for money, not for metadata
    EnrichTransaction(uow).execute(
        EnrichCommand(result.transactions[2].id, "Amazon", cat(uow, "shopping"))
    )
    rows = uow.transactions.list_by_plan(first.plan_id or "")
    assert {(t.merchant, t.category_id) for t in rows} == {("Amazon", cat(uow, "shopping"))}
    assert uow.plans.items[first.plan_id or ""].category_id == cat(uow, "shopping")


# --- the review deck query ---

from financas.application.queries.review import CountPendingReview, GetReviewDeck  # noqa: E402


def test_review_deck_lists_pending_expenses_newest_first_and_skips(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    old = spend(uow, checking, 1_000, None, slug="uncategorized", day=2, description="Antiga")
    new = spend(uow, checking, 2_000, None, slug="uncategorized", day=9, description="Nova")
    spend(uow, checking, 3_000, "Amazon", slug="shopping", description="Completa")  # done
    third = spend(
        uow, checking, 4_000, "Amazon", slug="uncategorized", day=1, description="Sem categoria"
    )  # pending
    spend(
        uow, checking, 500, None, kind=K.INCOME, slug="salary", description="Salário"
    )  # not an expense
    assert CountPendingReview(uow).execute() == 3
    deck = GetReviewDeck(uow).execute()
    assert deck.card and deck.card.entry.id in {new.id}  # July 10 is the newest of the pending ones
    assert (deck.pending, deck.remaining) == (3, 3)
    after_skip = GetReviewDeck(uow).execute([deck.card.entry.id])
    assert after_skip.card and after_skip.card.entry.id != deck.card.entry.id
    assert (after_skip.pending, after_skip.remaining) == (3, 2)
    everything = GetReviewDeck(uow).execute([old.id, new.id, third.id])
    assert everything.card is None and everything.pending == 3 and everything.remaining == 0


def test_review_suggestions_come_from_aliases_and_the_users_own_entries(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    spend(uow, checking, 900, "Drogasil", slug="health", day=1, description="Remédio")
    spend(uow, checking, 1_200, "Shopee", slug="shopping", day=2, description="Capinha")
    pending = spend(
        uow, checking, 5_000, None, slug="uncategorized", day=9, description="COMPRA DROGASIL 0451"
    )
    deck = GetReviewDeck(uow).execute()
    assert deck.card and deck.card.entry.id == pending.id
    assert deck.card.suggested_merchant == "Drogasil"  # a merchant the user already used
    assert deck.card.suggested_category_id == cat(uow, "health")  # that merchant's usual category
    uow.transactions.update(Transaction(**{**vars(pending), "description": "SHOPEE *BR"}))
    deck = GetReviewDeck(uow).execute()
    assert deck.card and deck.card.suggested_merchant == "Shopee"  # the alias
    assert deck.card.suggested_category_id == cat(uow, "shopping")
    uow.transactions.update(Transaction(**{**vars(pending), "description": "Coisa estranha"}))
    deck = GetReviewDeck(uow).execute()
    assert deck.card and (deck.card.suggested_merchant, deck.card.suggested_category_id) == (
        None,
        None,
    )
    # an entry that already has a category keeps it preselected
    categorized = spend(uow, checking, 700, None, slug="food", day=10, description="Padaria")
    deck = GetReviewDeck(uow).execute()
    assert deck.card and deck.card.entry.id == categorized.id
    assert deck.card.suggested_category_id == cat(uow, "food")


def test_refunded_and_transfer_entries_are_never_pending(
    uow: MemoryUnitOfWork, checking: Account, savings: Account
) -> None:
    from financas.application.use_cases.transactions import (
        RegisterTransfer,
        RegisterTransferCommand,
    )

    gone = spend(uow, checking, 1_000, None, slug="uncategorized", description="Devolvida")
    UpdateTransaction(uow, FixedClock(D(2026, 7, 20))).execute(
        UpdateTransactionCommand(gone.id, gone.posted_on, 1_000, gone.description, is_refunded=True)
    )
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, savings.id, D(2026, 7, 5), 100)
    )
    assert CountPendingReview(uow).execute() == 0
    assert GetReviewDeck(uow).execute().card is None
