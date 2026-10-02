"""investment holdings

Revision ID: 9654607ebb61
Revises: 8cd33775afe5
Create Date: 2026-10-01 22:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9654607ebb61"
down_revision: str | None = "8cd33775afe5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investment_holdings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("instrument_type", sa.String(length=20), nullable=False),
        sa.Column("issuer_id", sa.String(length=32), nullable=False),
        sa.Column("indexer", sa.String(length=20), nullable=True),
        sa.Column("rate_mode", sa.String(length=20), nullable=True),
        sa.Column("rate_bps", sa.Integer(), nullable=True),
        sa.Column("applied_on", sa.Date(), nullable=False),
        sa.Column("principal_cents", sa.BigInteger(), nullable=False),
        sa.Column("maturity_on", sa.Date(), nullable=True),
        sa.Column("liquidity", sa.String(length=20), nullable=False),
        sa.Column("liquid_from", sa.Date(), nullable=True),
        sa.Column("fgc_covered", sa.Boolean(), nullable=False),
        sa.Column("is_emergency_fund", sa.Boolean(), nullable=False),
        sa.Column("asset_class", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "instrument_type IN ('cdb', 'lc', 'lci', 'lca', 'cri', 'cra', 'debenture',"
            " 'treasury_selic', 'treasury_ipca', 'treasury_prefixed', 'savings_account', 'fund',"
            " 'pension', 'stock', 'reit', 'etf', 'crypto', 'other')",
            name=op.f("ck_investment_holdings_instrument_type"),
        ),
        sa.CheckConstraint(
            "indexer IS NULL OR indexer IN ('cdi', 'selic', 'ipca', 'prefixed', 'other')",
            name=op.f("ck_investment_holdings_indexer"),
        ),
        sa.CheckConstraint(
            "rate_mode IS NULL OR rate_mode IN"
            " ('percent_of_index', 'spread_over_index', 'fixed_annual')",
            name=op.f("ck_investment_holdings_rate_mode"),
        ),
        sa.CheckConstraint(
            "liquidity IN ('daily', 'at_maturity')", name=op.f("ck_investment_holdings_liquidity")
        ),
        sa.CheckConstraint(
            "asset_class IN ('fixed_income', 'equities', 'real_estate_funds', 'crypto', 'other')",
            name=op.f("ck_investment_holdings_holding_asset_class"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'redeemed')", name=op.f("ck_investment_holdings_holding_status")
        ),
        sa.CheckConstraint(
            "principal_cents > 0", name=op.f("ck_investment_holdings_principal_positive")
        ),
        sa.CheckConstraint(
            "(rate_mode IS NULL AND rate_bps IS NULL)"
            " OR (rate_mode IS NOT NULL AND rate_bps IS NOT NULL AND rate_bps >= 0)",
            name=op.f("ck_investment_holdings_rate_fields"),
        ),
        sa.CheckConstraint(
            "(liquidity = 'at_maturity' AND maturity_on IS NOT NULL AND liquid_from IS NULL)"
            " OR liquidity = 'daily'",
            name=op.f("ck_investment_holdings_liquidity_dates"),
        ),
        sa.CheckConstraint(
            "maturity_on IS NULL OR maturity_on > applied_on",
            name=op.f("ck_investment_holdings_maturity_after_application"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_investment_holdings_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["issuer_id"],
            ["institutions.id"],
            name=op.f("fk_investment_holdings_issuer_id_institutions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investment_holdings")),
    )
    op.create_index(
        op.f("ix_investment_holdings_account_id"),
        "investment_holdings",
        ["account_id"],
        unique=False,
    )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("holding_id", sa.String(length=32), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_transactions_holding_id"), ["holding_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_transactions_holding_id_investment_holdings"),
            "investment_holdings",
            ["holding_id"],
            ["id"],
        )
    with op.batch_alter_table("balance_anchors", schema=None) as batch_op:
        batch_op.drop_constraint("uq_balance_anchors_account_id", type_="unique")
        batch_op.add_column(sa.Column("holding_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_balance_anchors_holding_id_investment_holdings"),
            "investment_holdings",
            ["holding_id"],
            ["id"],
        )
    # one valuation per account and date (whole account), one per holding and date
    op.create_index(
        "uq_balance_anchors_account_date",
        "balance_anchors",
        ["account_id", "on_date"],
        unique=True,
        sqlite_where=sa.text("holding_id IS NULL"),
    )
    op.create_index(
        "uq_balance_anchors_holding_date",
        "balance_anchors",
        ["holding_id", "on_date"],
        unique=True,
        sqlite_where=sa.text("holding_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_balance_anchors_holding_date", table_name="balance_anchors")
    op.drop_index("uq_balance_anchors_account_date", table_name="balance_anchors")
    op.execute("DELETE FROM balance_anchors WHERE holding_id IS NOT NULL")
    op.execute("UPDATE transactions SET holding_id = NULL")
    with op.batch_alter_table("balance_anchors", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_balance_anchors_holding_id_investment_holdings"), type_="foreignkey"
        )
        batch_op.drop_column("holding_id")
        batch_op.create_unique_constraint(
            "uq_balance_anchors_account_id", ["account_id", "on_date"]
        )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_transactions_holding_id_investment_holdings"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_transactions_holding_id"))
        batch_op.drop_column("holding_id")
    op.drop_index(op.f("ix_investment_holdings_account_id"), table_name="investment_holdings")
    op.drop_table("investment_holdings")
