"""The historical transfer scanner: pairs clear matches, ignores look-alikes, never guesses."""

import datetime as dt

import pytest

from fakes import MemoryUnitOfWork
from financas.application.services.reconcile_transfers import (
    MIN_APPLY_CONFIDENCE,
    Candidate,
    find_pairs,
    score,
)
from financas.application.use_cases.cards import CardPurchaseCommand, RegisterCardPurchase
from financas.application.use_cases.catalog import CreateAccount, CreateAccountCommand
from financas.application.use_cases.reconcile_transfers import ApplyTransferPairs, ScanTransfers
from financas.application.use_cases.transactions import (
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.domain.models import Account, AccountKind, Institution, Transaction, TransactionKind

D = dt.date
K = TransactionKind


def cand(i: str, account: str, cents: int, day: int = 10, text: str = "") -> Candidate:
    return Candidate(i, account, D(2026, 7, day), cents, text)


# --- the pure scoring and pairing ---


def test_a_same_day_pair_with_a_keyword_is_very_confident() -> None:
    value = score(cand("a", "inter", -50_000, text="pix enviado"), cand("b", "nu", 50_000))
    assert value == 95


def test_one_day_apart_needs_a_keyword_to_be_applicable() -> None:
    plain = score(cand("a", "inter", -50_000), cand("b", "nu", 50_000, day=11))
    keyword = score(cand("a", "inter", -50_000, text="ted"), cand("b", "nu", 50_000, day=11))
    assert plain is not None and plain < MIN_APPLY_CONFIDENCE
    assert keyword is not None and keyword >= MIN_APPLY_CONFIDENCE


@pytest.mark.parametrize(
    ("out", "inc"),
    [
        (cand("a", "inter", -50_000), cand("b", "nu", 49_999)),  # another amount
        (cand("a", "inter", -50_000), cand("b", "nu", 50_000, day=13)),  # too far apart
        (cand("a", "inter", -50_000), cand("b", "inter", 50_000)),  # the same account
        (cand("a", "inter", 50_000), cand("b", "nu", 50_000)),  # both inflows
    ],
)
def test_look_alikes_are_not_pairs(out: Candidate, inc: Candidate) -> None:
    assert score(out, inc) is None
    assert find_pairs([out, inc]).pairs == ()


def test_a_tie_is_ambiguous_and_never_paired() -> None:
    result = find_pairs(
        [
            cand("out", "inter", -50_000, text="pix"),
            cand("in1", "nu", 50_000),
            cand("in2", "itau", 50_000),
        ]
    )
    assert result.pairs == () and set(result.ambiguous) == {"out", "in1", "in2"}


def test_each_side_must_prefer_the_other() -> None:
    result = find_pairs(
        [
            cand("out1", "inter", -50_000, text="pix"),  # same day, keyword: 95 with in1
            cand("out2", "c6", -50_000, day=11),  # one day, no keyword: 65 with in1
            cand("in1", "nu", 50_000),
        ]
    )
    assert [(p.outgoing.id, p.incoming.id) for p in result.pairs] == [("out1", "in1")]
    assert "out2" in result.ambiguous


# --- over the ledger ---


@pytest.fixture
def inter(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Inter")
    )


@pytest.fixture
def nubank(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Nubank")
    )


def enter(
    uow: MemoryUnitOfWork, account: Account, kind: K, cents: int, text: str, day: int = 10
) -> Transaction:
    return RegisterTransaction(uow).execute(
        RegisterTransactionCommand(account.id, D(2026, 7, day), kind, cents, text)
    )


def seeded(uow: MemoryUnitOfWork, inter: Account, nubank: Account, card: Account):
    out = enter(uow, inter, K.EXPENSE, 50_000, "PIX para Nubank")
    inc = enter(uow, nubank, K.INCOME, 50_000, "PIX recebido")
    other = enter(uow, inter, K.EXPENSE, 12_000, "Transferência")  # nobody received it
    purchase = RegisterCardPurchase(uow).execute(  # the same amount, on a card: not a transfer
        CardPurchaseCommand(card.id, "Geladeira", D(2026, 7, 10), total_cents=50_000)
    )
    return out, inc, other, purchase


def test_the_scan_finds_the_pair_and_ignores_a_card_purchase_and_a_lone_amount(
    uow: MemoryUnitOfWork, inter: Account, nubank: Account, card: Account
) -> None:
    out, inc, _, _ = seeded(uow, inter, nubank, card)
    report = ScanTransfers(uow).execute()
    (view,) = report.pairs
    assert (view.proposal.outgoing.id, view.proposal.incoming.id) == (out.id, inc.id)
    assert view.proposal.confidence >= MIN_APPLY_CONFIDENCE and report.applicable == (view,)
    assert (view.outgoing_account, view.incoming_account) == ("Inter", "Nubank")
    assert report.ambiguous == 0


def test_a_dry_run_never_writes(
    uow: MemoryUnitOfWork, inter: Account, nubank: Account, card: Account
) -> None:
    out, inc, _, _ = seeded(uow, inter, nubank, card)
    ScanTransfers(uow).execute()
    assert uow.transactions.get(out.id) == out and uow.transactions.get(inc.id) == inc


def test_apply_turns_the_pair_into_one_linked_transfer(
    uow: MemoryUnitOfWork, inter: Account, nubank: Account, card: Account
) -> None:
    out, inc, other, _ = seeded(uow, inter, nubank, card)
    assert ApplyTransferPairs(uow).execute().linked == 1
    first, second = uow.transactions.get(out.id), uow.transactions.get(inc.id)
    assert first and second
    assert first.kind is second.kind is K.TRANSFER
    assert first.transfer_id and first.transfer_id == second.transfer_id
    assert (first.amount_cents, second.amount_cents) == (-50_000, 50_000)
    assert first.category_id == second.category_id == uow.categories.get_by_slug("transfer").id  # type: ignore[union-attr]
    untouched = uow.transactions.get(other.id)
    assert untouched and untouched.kind is K.EXPENSE
    assert ApplyTransferPairs(uow).execute().linked == 0  # nothing left: idempotent
    assert ScanTransfers(uow).execute().pairs == ()


def test_two_lone_transfer_legs_are_merged_into_one_transfer(
    uow: MemoryUnitOfWork, inter: Account, nubank: Account
) -> None:
    (a,) = RegisterTransfer(uow).execute(
        RegisterTransferCommand(inter.id, None, D(2026, 7, 10), 30_000, "TED")
    )
    (b,) = RegisterTransfer(uow).execute(
        RegisterTransferCommand(None, nubank.id, D(2026, 7, 10), 30_000, "TED")
    )
    assert a.transfer_id != b.transfer_id
    assert ApplyTransferPairs(uow).execute().linked == 1
    first, second = uow.transactions.get(a.id), uow.transactions.get(b.id)
    assert first and second and first.transfer_id == second.transfer_id
    assert len(uow.transactions.list_by_transfer(first.transfer_id or "")) == 2


def test_a_refunded_itemized_or_already_linked_entry_is_never_a_candidate(
    uow: MemoryUnitOfWork, inter: Account, nubank: Account
) -> None:
    RegisterTransfer(uow).execute(
        RegisterTransferCommand(inter.id, nubank.id, D(2026, 7, 10), 50_000, "PIX")
    )
    enter(uow, inter, K.EXPENSE, 50_000, "PIX")  # would match the inflow leg above
    report = ScanTransfers(uow).execute()
    assert report.pairs == ()  # a complete transfer's legs are not offered again


def test_a_statement_payment_never_pairs_with_an_equal_inflow(
    uow: MemoryUnitOfWork, inter: Account, nubank: Account, card: Account
) -> None:
    from fakes import FixedClock
    from financas.application.use_cases.cards import PayStatement, PayStatementCommand

    result = RegisterCardPurchase(uow).execute(
        CardPurchaseCommand(card.id, "Geladeira", D(2026, 7, 10), total_cents=50_000)
    )
    statement_id = result.transactions[0].statement_id
    assert statement_id
    PayStatement(uow, FixedClock(D(2026, 10, 1))).execute(
        PayStatementCommand(statement_id, inter.id, D(2026, 9, 28), 50_000)
    )
    RegisterTransaction(uow).execute(
        RegisterTransactionCommand(nubank.id, D(2026, 9, 28), K.INCOME, 50_000, "PIX recebido")
    )  # same amount, other account
    report = ScanTransfers(uow).execute()
    assert report.pairs == () and report.ambiguous == 0
    assert ApplyTransferPairs(uow).execute().linked == 0
