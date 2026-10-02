"""tighten constraints

Revision ID: b7d2c4e1a9f3
Revises: 9654607ebb61
Create Date: 2026-10-02 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d2c4e1a9f3"
down_revision: str | None = "9654607ebb61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLOR = (
    "color IS NULL OR (length(color) = 7"
    " AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]')"
)
GROUP_KIND = (
    "(\"group\" IN ('essential', 'non_essential', 'charges', 'review') AND kind = 'expense')"
    " OR (\"group\" = 'income' AND kind = 'income')"
    " OR (\"group\" = 'movement' AND kind = 'neutral')"
)
MONTH_OLD = "month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'"
MONTH_NEW = MONTH_OLD + " AND substr(month, 6, 2) BETWEEN '01' AND '12'"


def upgrade() -> None:
    op.execute("UPDATE categories SET monthly_budget_cents = NULL WHERE monthly_budget_cents <= 0")
    with op.batch_alter_table("institutions", schema=None) as batch_op:
        batch_op.create_check_constraint(batch_op.f("ck_institutions_color_format"), COLOR)
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_check_constraint(batch_op.f("ck_accounts_color_format"), COLOR)
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.create_check_constraint(batch_op.f("ck_categories_color_format"), COLOR)
        batch_op.create_check_constraint(
            batch_op.f("ck_categories_budget_positive"),
            "monthly_budget_cents IS NULL OR monthly_budget_cents > 0",
        )
        batch_op.create_check_constraint(batch_op.f("ck_categories_group_matches_kind"), GROUP_KIND)
    with op.batch_alter_table("statements", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_statements_month_format"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_statements_month_format"), MONTH_NEW)
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_transactions_transfer_id_only_on_transfers"),
            "transfer_id IS NULL OR kind = 'transfer'",
        )
    with op.batch_alter_table("investment_holdings", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_investment_holdings_grace_after_application"),
            "liquid_from IS NULL OR liquid_from >= applied_on",
        )


def downgrade() -> None:
    with op.batch_alter_table("investment_holdings", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_investment_holdings_grace_after_application"), type_="check"
        )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_transactions_transfer_id_only_on_transfers"), type_="check"
        )
    with op.batch_alter_table("statements", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_statements_month_format"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_statements_month_format"), MONTH_OLD)
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_categories_group_matches_kind"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_categories_budget_positive"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_categories_color_format"), type_="check")
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_accounts_color_format"), type_="check")
    with op.batch_alter_table("institutions", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_institutions_color_format"), type_="check")
