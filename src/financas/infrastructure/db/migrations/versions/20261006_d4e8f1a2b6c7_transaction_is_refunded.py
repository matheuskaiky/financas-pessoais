"""refunded purchases

``transactions.is_refunded``: an expense that was given back stays on record, struck through,
but is counted nowhere (statement totals, limit, summaries, budget, balances). Only expenses can
be refunded.

Revision ID: d4e8f1a2b6c7
Revises: c3a9d5f7e2b1
Create Date: 2026-10-06 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8f1a2b6c7"
down_revision: str | None = "c3a9d5f7e2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_refunded", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_transactions_refunded_only_on_expenses"),
            "is_refunded = 0 OR kind = 'expense'",
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_transactions_refunded_only_on_expenses"), type_="check"
        )
        batch_op.drop_column("is_refunded")
