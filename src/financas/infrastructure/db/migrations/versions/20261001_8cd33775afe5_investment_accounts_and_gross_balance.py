"""investment accounts and gross balance

Revision ID: 8cd33775afe5
Revises: 04545ce71be4
Create Date: 2026-10-01 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8cd33775afe5"
down_revision: str | None = "04545ce71be4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tracking", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("asset_class", sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column("is_emergency_fund", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    # accounts that already exist: investment accounts start at the account level, class "other"
    op.execute(
        "UPDATE accounts SET tracking = 'account', asset_class = 'other' WHERE kind = 'investment'"
    )
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_accounts_investment_tracking"),
            "tracking IN ('account', 'holdings')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_accounts_asset_class"),
            "asset_class IN ('fixed_income', 'equities', 'real_estate_funds', 'crypto', 'other')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_accounts_investment_fields"),
            "(kind = 'investment' AND tracking IS NOT NULL AND asset_class IS NOT NULL)"
            " OR (kind <> 'investment' AND tracking IS NULL AND asset_class IS NULL"
            " AND is_emergency_fund = 0)",
        )
    with op.batch_alter_table("balance_anchors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("gross_balance_cents", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f("ck_balance_anchors_gross_not_below_net"),
            "gross_balance_cents IS NULL OR gross_balance_cents >= balance_cents",
        )


def downgrade() -> None:
    with op.batch_alter_table("balance_anchors", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_balance_anchors_gross_not_below_net"), type_="check"
        )
        batch_op.drop_column("gross_balance_cents")
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_accounts_investment_fields"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_accounts_asset_class"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_accounts_investment_tracking"), type_="check")
        batch_op.drop_column("is_emergency_fund")
        batch_op.drop_column("asset_class")
        batch_op.drop_column("tracking")
