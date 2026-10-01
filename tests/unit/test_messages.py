import pytest

from financas.domain.errors import DomainError
from financas.domain.models import CategoryKind, TransactionKind
from financas.interfaces.messages import (
    CATEGORY_KIND_LABELS,
    TRANSACTION_KIND_LABELS,
    render_error,
)


def test_every_enum_value_has_a_label() -> None:
    assert set(TRANSACTION_KIND_LABELS) == set(TransactionKind)
    assert set(CATEGORY_KIND_LABELS) == set(CategoryKind)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (DomainError("INVALID_AMOUNT"), "Valor inválido. Use o formato 1.234,56."),
        (
            DomainError("SIGN_KIND_MISMATCH", kind="expense"),
            "O sinal do valor não combina com o tipo de lançamento (Despesa).",
        ),
        (
            DomainError("CATEGORY_KIND_MISMATCH", kind="transfer", category_kind="expense"),
            "A categoria (Despesa) não combina com o tipo de lançamento (Transferência).",
        ),
        (DomainError("SOMETHING_NEW"), "Erro inesperado (SOMETHING_NEW)."),
    ],
)
def test_render_error(error: DomainError, expected: str) -> None:
    assert render_error(error) == expected
