"""Applying a plan through the use cases (memory repositories) and verifying the result."""

import dataclasses

import pytest
from test_import_planner import ACCOUNTS, YM, D, card_row, config, inst, pix, row

from fakes import FixedClock, MemoryUnitOfWork
from financas.application.imports.apply import ApplyImport, compare_statement_totals, verify_import
from financas.application.imports.model import (
    ImportPlan,
    LegacyStatementTotal,
    RowKind,
    TransferHint,
)
from financas.application.imports.planner import build_plan
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind

CLOCK = FixedClock(D(2026, 10, 2))


def notes(action: object) -> str | None:
    return "nota" if getattr(action, "original_category", None) else None


def make_plan() -> ImportPlan:
    accounts = tuple(
        dataclasses.replace(a, opening_cents=118_367, opening_on=D(2026, 9, 8))
        if a.key == "inter_cc"
        else a
        for a in ACCOUNTS
    )
    rows = [
        row(D(2026, 1, 5), 400_000, kind=RowKind.INCOME, category="Salario", recurring=True),
        row(D(2026, 1, 6), -15_000, category="Alimentação", description="Café São João"),
        row(D(2026, 1, 7), 900, kind=RowKind.INCOME, category="Educacao"),  # mismatch -> note
        card_row(D(2026, 1, 10), -10_000, YM(2026, 1)),
        card_row(D(2026, 1, 11), 2_000, YM(2026, 1), kind=RowKind.REFUND, category="Estorno"),
        inst(3, 6, YM(2026, 1), 9_679),  # Nubank: installments 3..6 (4 entries)
        inst(4, 6, YM(2026, 2), 9_680),  # the sheet's amount differs by a cent
        pix(D(2026, 1, 9), -30_000, "fulano de tal", "Banco Inter"),
        pix(D(2026, 1, 10), 30_000, "fulano de tal", "Banco do Brasil"),
        row(
            D(2026, 1, 12),
            -5_000,
            kind=RowKind.TRANSFER,
            category="Transferencia",
            hint=TransferHint.SWEEP,
            description="Rende",
        ),
        card_row(
            D(2026, 1, 26),
            5_000,
            YM(2026, 2),
            kind=RowKind.TRANSFER,
            category="Transferencia",
            hint=TransferHint.STATEMENT_PAYMENT,
            description="Pagamento recebido",
        ),
        row(
            D(2026, 1, 26),
            -5_000,
            kind=RowKind.TRANSFER,
            institution="Banco Inter",
            category="Transferencia",
            hint=TransferHint.STATEMENT_PAYMENT,
            description="Pagto",
        ),
    ]
    return build_plan(rows, config(accounts=accounts))


def apply(uow: MemoryUnitOfWork, plan: ImportPlan):  # type: ignore[no-untyped-def]
    return ApplyImport(uow, CLOCK).execute(plan, notes, "Pagamento de fatura")


def test_apply_creates_accounts_entries_plans_transfers_and_payments(
    uow: MemoryUnitOfWork,
) -> None:
    plan = make_plan()
    assert plan.errors == ()
    result = apply(uow, plan)
    assert (result.entries, result.transfers, result.plans, result.payments) == (5, 2, 1, 1)
    assert len(uow.accounts.list_all()) == len(ACCOUNTS)
    assert len(uow.institutions.list_all()) == 3  # BB, Inter, Nubank: shared by name
    inter = uow.accounts.get(result.account_ids["inter_cc"])
    assert inter is not None
    (anchor,) = uow.anchors.list_for_account(inter.id)
    assert (anchor.balance_cents, anchor.on_date) == (118_367, D(2026, 9, 8))
    nubank = uow.transactions.list_by_account(result.account_ids["nu_card"])
    installments = sorted(
        (t for t in nubank if t.installment_number), key=lambda t: t.installment_number or 0
    )
    assert [t.installment_number for t in installments] == [3, 4, 5, 6]
    assert [-t.amount_cents for t in installments] == [9_679, 9_680, 9_680, 9_680]
    income = [
        t
        for t in uow.transactions.list_by_account(result.account_ids["bb_cc"])
        if t.amount_cents == 900
    ]
    assert income and income[0].notes == "nota"  # the adapter's note for the replaced category
    verification = verify_import(uow, plan, result.account_ids)
    assert verification.ok, [c for c in verification.checks if not c.ok]


def test_the_plan_must_have_no_errors_and_the_database_must_be_empty(
    uow: MemoryUnitOfWork,
) -> None:
    broken = build_plan([row(D(2026, 1, 1), -1, category="Nova")], config())
    with pytest.raises(DomainError) as exc:
        apply(uow, broken)
    assert exc.value.code == "IMPORT_PLAN_HAS_ERRORS"
    plan = make_plan()
    apply(uow, plan)
    with pytest.raises(DomainError) as exc:
        apply(uow, plan)
    assert exc.value.code == "IMPORT_DATABASE_NOT_EMPTY"


def test_existing_accounts_are_reused_by_nickname(uow: MemoryUnitOfWork) -> None:
    bank = CreateInstitution(uow).execute(CreateInstitutionCommand(name="Banco do Brasil"))
    existing = CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, bank.id, "BB c/c")
    )
    result = apply(uow, make_plan())
    assert result.account_ids["bb_cc"] == existing.id
    assert len([i for i in uow.institutions.list_all() if i.name == "Banco do Brasil"]) == 1


def test_a_nickname_of_another_kind_is_a_conflict(uow: MemoryUnitOfWork) -> None:
    bank = CreateInstitution(uow).execute(CreateInstitutionCommand(name="Banco do Brasil"))
    CreateAccount(uow).execute(CreateAccountCommand(AccountKind.INVESTMENT, bank.id, "BB c/c"))
    with pytest.raises(DomainError) as exc:
        apply(uow, make_plan())
    assert exc.value.code == "IMPORT_ACCOUNT_KIND_CONFLICT"


def test_verification_catches_a_missing_entry(uow: MemoryUnitOfWork) -> None:
    plan = make_plan()
    result = apply(uow, plan)
    victim = uow.transactions.list_by_account(result.account_ids["bb_cc"])[0]
    uow.transactions.delete(victim.id)
    verification = verify_import(uow, plan, result.account_ids)
    assert not verification.ok
    assert {"COUNT_BY_ACCOUNT", "SUM_BY_ACCOUNT"} <= {
        c.code for c in verification.checks if not c.ok
    }


def test_verification_catches_a_half_deleted_transfer(uow: MemoryUnitOfWork) -> None:
    plan = make_plan()
    result = apply(uow, plan)
    transfers = [
        t for t in uow.transactions.list_by_account(result.account_ids["rende"]) if t.transfer_id
    ]
    uow.transactions.update_amount(transfers[0].id, transfers[0].amount_cents + 1)
    assert not verify_import(uow, plan, result.account_ids).ok


def test_statement_totals_are_compared_with_the_sheet_net_of_refunds(
    uow: MemoryUnitOfWork,
) -> None:
    plan = make_plan()
    result = apply(uow, plan)
    # Nubank 2026-01: purchase 10,000 + installment 3 of 9,679, minus a 2,000 refund
    sheet = [
        LegacyStatementTotal("Nubank", YM(2026, 1), 19_679, 2_000),  # gross 19,679, refunds apart
        LegacyStatementTotal("Nubank", YM(2026, 2), 1, 0),  # different
        LegacyStatementTotal("Nubank", YM(2025, 12), 5, 0),  # another year: ignored
    ]
    equal, different = compare_statement_totals(uow, plan, result.account_ids, sheet)
    assert (equal, different) == (1, ["nu_card 2026-02"])
