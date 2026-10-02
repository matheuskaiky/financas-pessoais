import datetime as dt

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


def test_assignment_explanations() -> None:
    from financas.domain.money import YearMonth
    from financas.domain.services.card_cycle import assign_statement, explicit_assignment
    from financas.interfaces.messages import best_day_hint, explain_assignment

    D = dt.date
    # card due on day 5, closing 11 days before the due date
    assert explain_assignment(assign_statement(D(2026, 7, 26), 5, 11)) == (
        "Compra em 26/07/2026, depois do fechamento em 25/07 → "
        "fatura de ago/2026 (fecha 25/08 · vence 05/09)."
    )
    assert "antes do fechamento em 25/07" in explain_assignment(
        assign_statement(D(2026, 7, 20), 5, 11)
    )
    assert "no dia do fechamento (25/07), ainda entra" in explain_assignment(
        assign_statement(D(2026, 7, 25), 5, 11)
    )
    assert explain_assignment(explicit_assignment(YearMonth(2026, 9), 5, 11)).startswith(
        "Fatura escolhida por você: fatura de set/2026 (fecha 24/09 · vence 05/10)"
    )
    assert best_day_hint(assign_statement(D(2026, 7, 1), 5, 11)) == (
        "Melhor dia de compra neste cartão: 26/07/2026 (logo depois do fechamento)."
    )


def test_statement_label_shows_closing_and_due_dates_together() -> None:
    from financas.domain.models import Statement
    from financas.domain.money import YearMonth
    from financas.interfaces.messages import statement_label

    st = Statement("s", "a", YearMonth(2026, 7), dt.date(2026, 7, 25), dt.date(2026, 8, 5))
    assert statement_label(st) == "Fatura jul/2026 · fecha 25/07 · vence 05/08"
