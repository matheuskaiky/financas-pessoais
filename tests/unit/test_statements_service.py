import datetime as dt

import pytest

from financas.domain.errors import DomainError
from financas.domain.models import StatementStatus
from financas.domain.rules import validate_card_settings
from financas.domain.services.statements import (
    LimitAlert,
    limit_usage,
    reconcile,
    statement_status,
)

D = dt.date
S = StatementStatus


@pytest.mark.parametrize(
    ("today", "paid", "expected"),
    [
        (D(2026, 7, 25), 0, S.FUTURE),
        (D(2026, 7, 26), 0, S.OPEN),
        (D(2026, 8, 20), 0, S.OPEN),
        (D(2026, 8, 25), 0, S.OPEN),
        (D(2026, 8, 26), 0, S.CLOSED),
        (D(2026, 9, 6), 500, S.CLOSED),  # partial payment
        (D(2026, 9, 6), 1_000, S.PAID),
        (D(2026, 9, 6), 1_200, S.PAID),
    ],
)
def test_statement_status(today: dt.date, paid: int, expected: StatementStatus) -> None:
    # August statement, closing on the 25th, total R$ 10,00
    status = statement_status(
        today=today,
        closing_date=D(2026, 8, 25),
        previous_closing_date=D(2026, 7, 25),
        total_cents=1_000,
        paid_cents=paid,
    )
    assert status is expected


def test_an_open_statement_is_never_paid_yet() -> None:
    status = statement_status(D(2026, 8, 10), D(2026, 8, 25), D(2026, 7, 25), 1_000, 1_000)
    assert status is S.OPEN


def test_closed_empty_statement_counts_as_paid() -> None:
    status = statement_status(D(2026, 9, 1), D(2026, 8, 25), D(2026, 7, 25), 0, 0)
    assert status is S.PAID


def test_reconciliation() -> None:
    rec = reconcile(entered_cents=231_846, informed_cents=235_646)
    assert (rec.entered_cents, rec.informed_cents, rec.difference_cents) == (
        231_846,
        235_646,
        3_800,
    )
    assert reconcile(100, None).difference_cents is None


@pytest.mark.parametrize(
    ("committed", "limit", "alert", "pct"),
    [
        (615_292, 1_200_000, LimitAlert.NONE, 51.3),
        (491_230, 600_000, LimitAlert.WARNING, 81.9),
        (480_000, 600_000, LimitAlert.WARNING, 80.0),
        (479_999, 600_000, LimitAlert.NONE, 80.0),
        (600_000, 600_000, LimitAlert.EXCEEDED, 100.0),
        (700_000, 600_000, LimitAlert.EXCEEDED, 116.7),
        (0, 600_000, LimitAlert.NONE, 0.0),
    ],
)
def test_limit_usage(committed: int, limit: int, alert: LimitAlert, pct: float) -> None:
    usage = limit_usage(committed, limit)
    assert usage.alert is alert
    assert usage.available_cents == limit - committed
    assert usage.percent is not None and round(usage.percent, 1) == pct


def test_no_limit_means_not_informed_never_zero_percent() -> None:
    usage = limit_usage(123_456, None)
    assert usage.alert is LimitAlert.NOT_INFORMED
    assert usage.percent is None and usage.available_cents is None
    assert usage.committed_cents == 123_456


def test_a_credit_balance_commits_nothing() -> None:
    usage = limit_usage(-5_000, 100_000)
    assert usage.committed_cents == 0 and usage.available_cents == 100_000


@pytest.mark.parametrize(
    ("closing", "due", "limit"),
    [(11, 5, None), (1, 31, 0), (27, 1, 1_200_000)],
)
def test_valid_card_settings(closing: int, due: int, limit: int | None) -> None:
    validate_card_settings(closing, due, limit)


@pytest.mark.parametrize(
    ("closing", "due", "limit", "code"),
    [
        (None, 5, None, "CARD_DAYS_REQUIRED"),
        (7, None, None, "CARD_DAYS_REQUIRED"),
        (0, 5, None, "INVALID_DAYS_BEFORE_DUE"),
        (28, 5, None, "INVALID_DAYS_BEFORE_DUE"),
        (7, 32, None, "INVALID_CARD_DAY"),
        (7, 0, None, "INVALID_CARD_DAY"),
        (7, 5, -1, "AMOUNT_NOT_POSITIVE"),
    ],
)
def test_invalid_card_settings(
    closing: int | None, due: int | None, limit: int | None, code: str
) -> None:
    with pytest.raises(DomainError) as exc:
        validate_card_settings(closing, due, limit)
    assert exc.value.code == code
