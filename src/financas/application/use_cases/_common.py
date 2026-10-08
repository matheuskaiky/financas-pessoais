"""Small helpers shared by the use cases."""

import uuid

from financas.domain.errors import DomainError
from financas.domain.models import Category

# The title of an entry typed with no description and no useful category. Stored as data, like
# the name of a seeded category (the user sees it as typed text): the one Portuguese literal here.
NO_DESCRIPTION = "Sem descrição"


def entry_title(description: str, category: Category | None) -> str:
    """The description, or (when blank) the category's name, or "Sem descrição".

    A self-descriptive category ("Salário", "Farmácia") is its own title; no category, an
    itemized entry (its items carry the categories) or "Não categorizado" give no title.
    """
    if description:
        return description
    if category is not None and category.slug != "uncategorized":
        return category.name
    return NO_DESCRIPTION


def new_id() -> str:
    return uuid.uuid4().hex


class Unset:
    """The type of :data:`UNSET`: "the caller did not say", as opposed to ``None`` (clear it)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()


def found[T](value: T | None, entity: str) -> T:
    """Return ``value`` or raise ``NOT_FOUND`` for ``entity``."""
    if value is None:
        raise DomainError("NOT_FOUND", entity=entity)
    return value
