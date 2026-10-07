"""What the payment date fields of a statement need: the allowed range and how to word it."""

import datetime as dt

from financas.application.queries.cards import payment_date_boundaries
from financas.domain.models import Statement
from financas.domain.ports import UnitOfWork


def payment_window(uow: UnitOfWork, statement: Statement, today: dt.date) -> dict[str, object]:
    with uow as work:
        bounds = payment_date_boundaries(work, statement, today)
    return {
        "min": bounds.min_date,
        "max": bounds.max_date,
        "min_label": "vencimento da fatura anterior"
        if bounds.from_previous_due
        else "abertura da fatura",
        # "anterior <phrase>": the contraction depends on the noun
        "min_before": "ao vencimento da fatura anterior"
        if bounds.from_previous_due
        else "à abertura da fatura",
    }
