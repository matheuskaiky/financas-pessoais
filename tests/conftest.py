from pathlib import Path

import pytest

from fakes import FixedClock, MemoryImageStore, MemoryUnitOfWork
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
)
from financas.domain.models import Account, AccountKind, Institution
from financas.infrastructure.db.engine import make_engine, make_session_factory
from financas.infrastructure.db.migrate import upgrade_to_head
from financas.infrastructure.db.repositories import SqlUnitOfWork
from financas.infrastructure.db.seed import seed_categories


@pytest.fixture
def uow() -> MemoryUnitOfWork:
    unit = MemoryUnitOfWork()
    seed_categories(unit)
    return unit


@pytest.fixture
def images() -> MemoryImageStore:
    return MemoryImageStore()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def institution(uow: MemoryUnitOfWork) -> Institution:
    return CreateInstitution(uow).execute(CreateInstitutionCommand(name="Banco do Brasil"))


@pytest.fixture
def checking(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.CHECKING, institution.id, "Conta Corrente")
    )


@pytest.fixture
def savings(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(AccountKind.INVESTMENT, institution.id, "Caixinha")
    )


@pytest.fixture
def card(uow: MemoryUnitOfWork, institution: Institution) -> Account:
    return CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.CREDIT_CARD,
            institution.id,
            "Cartão",
            closing_day=25,
            due_day=5,
            credit_limit_cents=1_200_000,
        )
    )


@pytest.fixture
def sql_uow(tmp_path: Path) -> SqlUnitOfWork:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    upgrade_to_head(url)
    return SqlUnitOfWork(make_session_factory(make_engine(url)))
