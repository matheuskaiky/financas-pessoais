"""Turn what the user typed (a name, a slug, an id) into an entity."""

from collections.abc import Callable, Sequence

from financas.domain.errors import DomainError
from financas.domain.models import Account, AccountKind, Category, Institution
from financas.domain.ports import UnitOfWork
from financas.domain.services.text import normalize_search


def _pick[T](reference: str, items: Sequence[T], entity: str, keys: Callable[[T], list[str]]) -> T:
    """Exact key match first, then a unique partial match; never guess between several."""
    wanted = normalize_search(reference)
    exact = [i for i in items if wanted in keys(i)]
    if len(exact) == 1:
        return exact[0]
    partial = [i for i in items if any(wanted and wanted in k for k in keys(i))]
    candidates = exact or partial
    if len(candidates) > 1:
        raise DomainError("AMBIGUOUS_REFERENCE", reference=reference)
    if not candidates:
        raise DomainError("NOT_FOUND", entity=entity)
    return candidates[0]


def find_institution(uow: UnitOfWork, reference: str) -> Institution:
    with uow as work:
        items = work.institutions.list_all()
    return _pick(
        reference,
        items,
        "institution",
        lambda i: [i.id, normalize_search(i.name), normalize_search(i.slug)],
    )


def find_account(uow: UnitOfWork, reference: str) -> Account:
    with uow as work:
        items = work.accounts.list_all()
    return _pick(reference, items, "account", lambda a: [a.id, normalize_search(a.nickname)])


def find_category(uow: UnitOfWork, reference: str) -> Category:
    with uow as work:
        items = work.categories.list_all()
    return _pick(
        reference,
        items,
        "category",
        lambda c: [c.id, normalize_search(c.name), normalize_search(c.slug)],
    )


def default_checking_account(uow: UnitOfWork) -> Account | None:
    """The only active checking account, if there is exactly one (quick entry default)."""
    with uow as work:
        options = [
            a for a in work.accounts.list_all() if a.kind is AccountKind.CHECKING and a.is_active
        ]
    return options[0] if len(options) == 1 else None
