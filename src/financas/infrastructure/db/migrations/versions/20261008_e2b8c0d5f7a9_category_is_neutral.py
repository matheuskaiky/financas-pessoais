"""neutral (pass-through) categories

``categories.is_neutral`` marks a category whose entries pass through the user's account
(reimbursements, a bill paid for someone else): the bank balance moves as always, but the entries are
left out of spending, income and budget totals. The two seeded pass-through categories are created
by ``Container.seed()`` (it adds the categories that are missing), so no data is written here.

Revision ID: e2b8c0d5f7a9
Revises: d1a7b9c4e6f8
Create Date: 2026-10-08 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b8c0d5f7a9"
down_revision: str | None = "d1a7b9c4e6f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_neutral", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_column("is_neutral")
