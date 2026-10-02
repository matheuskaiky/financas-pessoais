"""Monthly budget goals per expense category (CLAUDE.md 11.8)."""

from dataclasses import replace

from financas.application.use_cases._common import found
from financas.domain.errors import DomainError
from financas.domain.models import Category, CategoryKind
from financas.domain.ports import UnitOfWork

MAX_BUDGET_CENTS = 10**12  # a sanity cap (R$ 10 billion): keeps typos out of the database


def _validate(cents: int | None) -> None:
    if cents is not None and cents <= 0:
        raise DomainError("AMOUNT_NOT_POSITIVE")
    if cents is not None and cents > MAX_BUDGET_CENTS:
        raise DomainError("AMOUNT_TOO_LARGE")


class SetCategoryBudgets:
    """Several goals at once, all or nothing (the budget screen saves them together)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, goals: dict[str, int | None]) -> None:
        for cents in goals.values():
            _validate(cents)
        with self._uow as uow:
            for category_id, cents in goals.items():
                category = found(uow.categories.get(category_id), "category")
                if category.kind is not CategoryKind.EXPENSE:
                    raise DomainError("BUDGET_ONLY_FOR_EXPENSES")
                if category.monthly_budget_cents != cents:
                    uow.categories.update(replace(category, monthly_budget_cents=cents))
            uow.commit()


class SetCategoryBudget:
    """The monthly goal of an expense category; ``None`` removes it ("sem meta")."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, category_id: str, monthly_budget_cents: int | None) -> Category:
        _validate(monthly_budget_cents)
        with self._uow as uow:
            category = found(uow.categories.get(category_id), "category")
            if category.kind is not CategoryKind.EXPENSE:
                raise DomainError("BUDGET_ONLY_FOR_EXPENSES")
            category = replace(category, monthly_budget_cents=monthly_budget_cents)
            uow.categories.update(category)
            uow.commit()
        return category
