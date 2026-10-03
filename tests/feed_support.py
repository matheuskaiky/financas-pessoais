"""Shared builders of the CSV feed tests: a synthetic context and a scratch database."""

import datetime as dt
from pathlib import Path

from financas.application.csvfeed.model import (
    AccountInfo,
    CategoryInfo,
    FeedContext,
    StatementInfo,
)
from financas.application.use_cases.catalog import (
    CreateAccount,
    CreateAccountCommand,
    CreateInstitution,
    CreateInstitutionCommand,
)
from financas.container import Container
from financas.domain.models import AccountKind, CategoryKind, InvestmentTracking
from financas.domain.money import YearMonth
from financas.infrastructure.db.seed import INITIAL_CATEGORIES
from financas.infrastructure.settings import Settings

TODAY = dt.date(2026, 10, 2)


def synthetic_context(**overrides: object) -> FeedContext:
    """Accounts: Conta Corrente, Conta Reserva, Caixinha Exemplo, Cartão Exemplo (due 5, 11)."""
    accounts = (
        AccountInfo("chk", "Conta Corrente", AccountKind.CHECKING, True),
        AccountInfo("res", "Conta Reserva", AccountKind.CHECKING, True),
        AccountInfo(
            "inv", "Caixinha Exemplo", AccountKind.INVESTMENT, True,
            tracking=InvestmentTracking.ACCOUNT,
        ),
        AccountInfo(
            "card", "Cartão Exemplo", AccountKind.CREDIT_CARD, True,
            due_day=5, closing_days_before_due=11,
        ),
        AccountInfo("old", "Conta Antiga", AccountKind.CHECKING, False),
        AccountInfo(
            "hold", "Corretora Por Aplicação", AccountKind.INVESTMENT, True,
            tracking=InvestmentTracking.HOLDINGS,
        ),
    )  # fmt: skip
    categories = tuple(
        CategoryInfo(f"cat-{slug}", slug, name, kind) for slug, name, _, kind in INITIAL_CATEGORIES
    )
    base: dict[str, object] = {
        "today": TODAY,
        "accounts": accounts,
        "categories": categories,
    }
    base.update(overrides)
    return FeedContext(**base)  # type: ignore[arg-type]


def paid_statement(month: YearMonth) -> StatementInfo:
    from financas.domain.services.card_cycle import statement_dates

    closing, due = statement_dates(month, 5, 11)
    return StatementInfo("card", month, closing, due, True, 10_000, 10_000)


def scratch_container(root: Path) -> Container:
    """A migrated, seeded database under ``root`` (never the real one) with the example accounts."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    settings = Settings(db_url=f"sqlite:///{data / 'f.db'}", data_dir=data)
    c = Container(settings)
    c.migrate()
    c.seed()
    bank = CreateInstitution(c.uow).execute(CreateInstitutionCommand(name="Banco Exemplo"))
    create = CreateAccount(c.uow)
    for kind, nickname in (
        (AccountKind.CHECKING, "Conta Corrente"),
        (AccountKind.CHECKING, "Conta Reserva"),
        (AccountKind.INVESTMENT, "Caixinha Exemplo"),
    ):
        create.execute(CreateAccountCommand(kind, bank.id, nickname))
    create.execute(
        CreateAccountCommand(
            AccountKind.CREDIT_CARD, bank.id, "Cartão Exemplo",
            closing_days_before_due=11, due_day=5, credit_limit_cents=500_000,
        )
    )  # fmt: skip
    c.engine.dispose()
    return c


__all__ = ["TODAY", "CategoryKind", "paid_statement", "scratch_container", "synthetic_context"]
