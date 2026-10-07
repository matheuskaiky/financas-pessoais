"""merchant on transactions

``transactions.merchant``: where the money went (a shop, a station, a delivery app), optional,
typed by the user with suggestions from what was typed before. Indexed for the autocomplete and
the "Principais Estabelecimentos" ranking.

Revision ID: f6a1b3c9d2e4
Revises: e5f9a2b3c8d1
Create Date: 2026-10-06 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a1b3c9d2e4"
down_revision: str | None = "e5f9a2b3c8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merchant", sa.String(length=120), nullable=True))
        batch_op.create_index(batch_op.f("ix_transactions_merchant"), ["merchant"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transactions_merchant"))
        batch_op.drop_column("merchant")
