"""itemized expenses

``transaction_splits``: the items of an expense, each with its own description, category and
amount. They add up to the parent entry's amount (checked by the use cases); deleting the entry
deletes its items. Statements, balances and limits keep seeing only the parent.

Revision ID: e5f9a2b3c8d1
Revises: d4e8f1a2b6c7
Create Date: 2026-10-06 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f9a2b3c8d1"
down_revision: str | None = "d4e8f1a2b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction_splits",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("transaction_id", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("category_id", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_transaction_splits_amount_positive")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_transaction_splits_category_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_transaction_splits_transaction_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction_splits")),
    )
    with op.batch_alter_table("transaction_splits", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_transaction_splits_transaction_id"), ["transaction_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("transaction_splits", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transaction_splits_transaction_id"))
    op.drop_table("transaction_splits")
