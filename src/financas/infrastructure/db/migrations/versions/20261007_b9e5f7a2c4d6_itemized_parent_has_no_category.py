"""an itemized expense has no category of its own

``transactions.category_id`` becomes nullable, but only for expenses (a CHECK), and every parent
entry that has items loses its category: its items carry them, so category spending can never
count the parent as well (CLAUDE.md 9.10). Downgrade gives each such parent the category of its
largest item again, so the column can be NOT NULL once more.

Revision ID: b9e5f7a2c4d6
Revises: a8d4e6f1b2c3
Create Date: 2026-10-07 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e5f7a2c4d6"
down_revision: str | None = "a8d4e6f1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "category_id IS NOT NULL OR kind = 'expense'"


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.alter_column("category_id", existing_type=sa.String(length=32), nullable=True)
        batch_op.create_check_constraint(
            batch_op.f("ck_transactions_category_required_unless_itemized"), CHECK
        )
    op.execute(
        "UPDATE transactions SET category_id = NULL"
        " WHERE id IN (SELECT DISTINCT transaction_id FROM transaction_splits)"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE transactions SET category_id = ("
        "  SELECT s.category_id FROM transaction_splits s WHERE s.transaction_id = transactions.id"
        "  ORDER BY s.amount_cents DESC, s.rowid LIMIT 1"
        ") WHERE category_id IS NULL"
    )
    op.execute(  # whatever is still without one (never created by the app) is "uncategorized"
        "UPDATE transactions SET category_id = (SELECT id FROM categories WHERE slug = 'uncategorized')"
        " WHERE category_id IS NULL"
    )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_transactions_category_required_unless_itemized"), type_="check"
        )
        batch_op.alter_column("category_id", existing_type=sa.String(length=32), nullable=False)
