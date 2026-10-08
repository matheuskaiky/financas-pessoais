"""Internal transfers: two linked legs, edited and deleted together."""

import datetime as dt

import pytest

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.queries.transfer_links import GetTransfer, ListTransferLinks
from financas.application.use_cases.catalog import CreateAccount, CreateAccountCommand
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
)
from financas.application.use_cases.transfers import UpdateTransfer, UpdateTransferCommand
from financas.domain.errors import DomainError
from financas.domain.models import Account, AccountKind, Institution, TransactionKind

D = dt.date


@pytest.fixture
def nubank(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Nubank")
    )


def transfer(uow: MemoryUnitOfWork, a: Account, b: Account, cents: int = 50_000):
    return RegisterTransfer(uow).execute(
        RegisterTransferCommand(a.id, b.id, D(2026, 7, 10), cents, "PIX")
    )


def update(uow: MemoryUnitOfWork, leg_id: str, **overrides: object):
    values: dict[str, object] = {
        "transaction_id": leg_id,
        "from_account_id": None,
        "to_account_id": None,
        "posted_on": D(2026, 7, 10),
        "amount_cents": 50_000,
    }
    values.update(overrides)
    return UpdateTransfer(uow).execute(UpdateTransferCommand(**values))  # type: ignore[arg-type]


def test_a_transfer_is_two_linked_legs_with_equal_magnitudes(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    assert out.transfer_id == inc.transfer_id and out.transfer_id
    assert (out.amount_cents, inc.amount_cents) == (-50_000, 50_000)
    assert (out.account_id, inc.account_id) == (checking.id, nubank.id)
    assert out.kind is inc.kind is TransactionKind.TRANSFER
    assert out.posted_on == inc.posted_on
    assert {t.id for t in uow.transactions.list_by_transfer(out.transfer_id)} == {out.id, inc.id}


def test_the_same_account_on_both_sides_is_rejected(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        transfer(uow, checking, checking)
    assert exc.value.code == "TRANSFER_SAME_ACCOUNT"
    assert not uow.transactions.list_by_account(checking.id)  # nothing was written


def test_a_card_cannot_take_part_in_an_internal_transfer(
    uow: MemoryUnitOfWork, checking: Account, card: Account
) -> None:
    with pytest.raises(DomainError) as exc:
        transfer(uow, checking, card)
    assert exc.value.code == "ACCOUNT_KIND_NOT_ALLOWED"


def test_each_leg_links_to_the_other_from_either_side(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    links = ListTransferLinks(uow).execute([out, inc])
    assert (links[out.id].other_id, links[out.id].outgoing) == (inc.id, True)
    assert (links[inc.id].other_id, links[inc.id].outgoing) == (out.id, False)
    assert links[out.id].other_account_id == nubank.id


def test_editing_one_leg_moves_both(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account, savings: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    update(
        uow,
        inc.id,  # edited from the inflow leg
        from_account_id=checking.id,
        to_account_id=savings.id,
        posted_on=D(2026, 7, 12),
        amount_cents=70_000,
        description="Reserva",
        notes="mês cheio",
    )
    first, second = uow.transactions.get(out.id), uow.transactions.get(inc.id)
    assert first and second
    assert (first.amount_cents, second.amount_cents) == (-70_000, 70_000)
    assert (first.account_id, second.account_id) == (checking.id, savings.id)
    assert first.posted_on == second.posted_on == D(2026, 7, 12)
    assert first.description == second.description == "Reserva"
    assert first.description_search == "reserva" and second.notes == "mês cheio"
    assert first.transfer_id == second.transfer_id == out.transfer_id  # still one transfer


def test_an_invalid_edit_changes_neither_leg(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account, card: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    for bad in (
        {"from_account_id": checking.id, "to_account_id": checking.id},  # same account
        {"from_account_id": checking.id, "to_account_id": card.id},  # a card
        {"from_account_id": checking.id, "to_account_id": nubank.id, "amount_cents": 0},
    ):
        with pytest.raises(DomainError):
            update(uow, out.id, **bad)
    assert uow.transactions.get(out.id) == out and uow.transactions.get(inc.id) == inc


def test_the_tracked_sides_of_a_transfer_cannot_change(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    (lone,) = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, None, D(2026, 7, 10), 1_000)
    )
    with pytest.raises(DomainError) as exc:
        update(uow, lone.id, from_account_id=checking.id, to_account_id=nubank.id)
    assert exc.value.code == "TRANSFER_SIDES_CHANGED"
    assert (
        update(uow, lone.id, from_account_id=checking.id, amount_cents=2_000)[0].amount_cents
        == -2_000
    )


def test_a_plain_entry_is_not_a_transfer_to_edit(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    expense = RegisterTransaction(uow).execute(
        RegisterTransactionCommand(checking.id, D(2026, 7, 10), TransactionKind.EXPENSE, 1_000, "x")
    )
    with pytest.raises(DomainError) as exc:
        update(uow, expense.id, from_account_id=checking.id, to_account_id=nubank.id)
    assert exc.value.code == "TRANSFER_NOT_EDITABLE"


def test_get_transfer_says_which_legs_are_editable(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    state = GetTransfer(uow).execute(inc.id)
    assert state.editable and state.outgoing and state.outgoing.id == out.id
    assert state.incoming and state.incoming.id == inc.id


def test_deleting_one_leg_removes_the_other_too(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    removed = DeleteTransaction(uow, FixedClock()).execute(out.id)
    assert removed == 2
    assert uow.transactions.get(out.id) is None and uow.transactions.get(inc.id) is None
    assert uow.transactions.list_by_transfer(out.transfer_id or "") == []


def test_raising_the_amount_moves_both_legs_in_opposite_directions(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account
) -> None:
    out, inc = transfer(uow, checking, nubank, cents=10_000)
    update(
        uow,
        out.id,
        from_account_id=checking.id,
        to_account_id=nubank.id,
        amount_cents=15_000,
    )
    first, second = uow.transactions.get(out.id), uow.transactions.get(inc.id)
    assert first and second
    assert (first.amount_cents, second.amount_cents) == (-15_000, 15_000)


def test_changing_the_destination_keeps_the_outflow_account(
    uow: MemoryUnitOfWork, checking: Account, nubank: Account, savings: Account
) -> None:
    out, inc = transfer(uow, checking, nubank)
    update(uow, out.id, from_account_id=checking.id, to_account_id=savings.id)
    first, second = uow.transactions.get(out.id), uow.transactions.get(inc.id)
    assert first and second
    assert (first.account_id, second.account_id) == (checking.id, savings.id)
    assert first.transfer_id == second.transfer_id


@pytest.fixture
def broker(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    from financas.application.use_cases.catalog import SetInvestmentSettings
    from financas.domain.models import AssetClass, InvestmentTracking

    account = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, institution.id, "Corretora")
    )
    return SetInvestmentSettings(uow).execute(
        account.id, AssetClass.FIXED_INCOME, False, tracking=InvestmentTracking.HOLDINGS
    )


def note(uow: MemoryUnitOfWork, broker: Account, institution: Institution, name: str):
    from financas.application.use_cases.holdings import RegisterHolding, RegisterHoldingCommand
    from financas.domain.models import InstrumentType, Liquidity

    return RegisterHolding(uow).execute(
        RegisterHoldingCommand(
            account_id=broker.id,
            name=name,
            instrument_type=InstrumentType.CDB,
            issuer_id=institution.id,
            applied_on=D(2026, 1, 1),
            principal_cents=100_000,
            liquidity=Liquidity.DAILY,
        )
    )


def test_the_note_of_an_investment_leg_can_be_reassigned_and_is_tagged_on_that_leg_only(
    uow: MemoryUnitOfWork, checking: Account, broker: Account, institution: Institution
) -> None:
    first, second = note(uow, broker, institution, "CDB A"), note(uow, broker, institution, "CDB B")
    out, inc = RegisterTransfer(uow).execute(
        RegisterTransferCommand(
            checking.id, broker.id, D(2026, 3, 1), 50_000, "Aporte", holding_id=first.id
        )
    )
    update(uow, out.id, from_account_id=checking.id, to_account_id=broker.id, holding_id=second.id)
    moved_out, moved_in = uow.transactions.get(out.id), uow.transactions.get(inc.id)
    assert moved_out and moved_in
    assert moved_out.holding_id is None and moved_in.holding_id == second.id


def test_clearing_the_note_sends_the_money_to_free_cash_and_a_stray_note_is_refused(
    uow: MemoryUnitOfWork,
    checking: Account,
    savings: Account,
    broker: Account,
    institution: Institution,
) -> None:
    held = note(uow, broker, institution, "CDB A")
    out, inc = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, broker.id, D(2026, 3, 1), 50_000, holding_id=held.id)
    )
    update(uow, out.id, from_account_id=checking.id, to_account_id=broker.id, holding_id=None)
    cleared = uow.transactions.get(inc.id)
    assert cleared and cleared.holding_id is None  # now free cash on the account
    with pytest.raises(DomainError) as exc:  # a note whose account is not on either side
        update(
            uow, out.id, from_account_id=checking.id, to_account_id=savings.id, holding_id=held.id
        )
    assert exc.value.code == "HOLDING_NOT_IN_ACCOUNT"
    kept = uow.transactions.get(out.id)
    assert kept and kept.amount_cents == -50_000


def test_a_transfer_with_a_note_is_editable(
    uow: MemoryUnitOfWork, checking: Account, broker: Account, institution: Institution
) -> None:
    held = note(uow, broker, institution, "CDB A")
    _, inc = RegisterTransfer(uow).execute(
        RegisterTransferCommand(checking.id, broker.id, D(2026, 3, 1), 50_000, holding_id=held.id)
    )
    state = GetTransfer(uow).execute(inc.id)
    assert state.editable and state.holding_id == held.id
    update(uow, inc.id, from_account_id=checking.id, to_account_id=broker.id, amount_cents=60_000)
    changed = uow.transactions.get(inc.id)
    assert changed and changed.amount_cents == 60_000 and changed.holding_id == held.id
