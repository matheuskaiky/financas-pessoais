"""Small helpers shared by the use cases."""

import uuid

from financas.domain.errors import DomainError


def new_id() -> str:
    return uuid.uuid4().hex


def found[T](value: T | None, entity: str) -> T:
    """Return ``value`` or raise ``NOT_FOUND`` for ``entity``."""
    if value is None:
        raise DomainError("NOT_FOUND", entity=entity)
    return value
