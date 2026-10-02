"""card closing as days before due

The closing date is defined relative to the due date (CLAUDE.md 9.3): ``closing_day`` becomes
``closing_days_before_due``. Stored statements keep their dates.

Revision ID: c3a9d5f7e2b1
Revises: b7d2c4e1a9f3
Create Date: 2026-10-02 12:00:00.000000
"""

import datetime as dt
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3a9d5f7e2b1"
down_revision: str | None = "b7d2c4e1a9f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_CHECK = (
    "(kind = 'credit_card' AND closing_days_before_due IS NOT NULL"
    " AND closing_days_before_due BETWEEN 1 AND 27"
    " AND due_day IS NOT NULL AND due_day BETWEEN 1 AND 31)"
    " OR (kind <> 'credit_card' AND closing_days_before_due IS NULL AND due_day IS NULL"
    " AND credit_limit_cents IS NULL)"
)
OLD_CHECK = (
    "(kind = 'credit_card' AND closing_day IS NOT NULL AND closing_day BETWEEN 1 AND 31"
    " AND due_day IS NOT NULL AND due_day BETWEEN 1 AND 31)"
    " OR (kind <> 'credit_card' AND closing_day IS NULL AND due_day IS NULL"
    " AND credit_limit_cents IS NULL)"
)


def _clamp(days: int) -> int:
    return max(1, min(27, days))


def _days_from_old_settings(closing_day: int, due_day: int) -> int:
    """Days between closing and due in a 31-day month (July), the way the old rule worked."""
    closing = dt.date(2026, 7, min(closing_day, 31))
    due = dt.date(2026, 7, due_day) if due_day > closing_day else dt.date(2026, 8, min(due_day, 31))
    return (due - closing).days


def upgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_accounts_card_fields"), type_="check")
        batch_op.add_column(sa.Column("closing_days_before_due", sa.Integer(), nullable=True))
    bind = op.get_bind()
    cards = bind.execute(
        sa.text("SELECT id, closing_day, due_day FROM accounts WHERE kind = 'credit_card'")
    ).fetchall()
    for card_id, closing_day, due_day in cards:
        latest = bind.execute(
            sa.text(
                "SELECT closing_date, due_date FROM statements WHERE account_id = :id"
                " ORDER BY month DESC LIMIT 1"
            ),
            {"id": card_id},
        ).fetchone()
        if latest is not None:  # the most recent statement shows the card's real gap
            days = (dt.date.fromisoformat(latest[1]) - dt.date.fromisoformat(latest[0])).days
        else:
            days = _days_from_old_settings(closing_day, due_day)
        bind.execute(
            sa.text("UPDATE accounts SET closing_days_before_due = :n WHERE id = :id"),
            {"n": _clamp(days), "id": card_id},
        )
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_column("closing_day")
        batch_op.create_check_constraint(batch_op.f("ck_accounts_card_fields"), NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_accounts_card_fields"), type_="check")
        batch_op.add_column(sa.Column("closing_day", sa.Integer(), nullable=True))
    bind = op.get_bind()
    cards = bind.execute(
        sa.text(
            "SELECT id, closing_days_before_due, due_day FROM accounts WHERE kind = 'credit_card'"
        )
    ).fetchall()
    for card_id, days, due_day in cards:
        closing_day = (min(due_day, 31) - days - 1) % 31 + 1  # an approximation of the old day
        bind.execute(
            sa.text("UPDATE accounts SET closing_day = :d WHERE id = :id"),
            {"d": closing_day, "id": card_id},
        )
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_column("closing_days_before_due")
        batch_op.create_check_constraint(batch_op.f("ck_accounts_card_fields"), OLD_CHECK)
