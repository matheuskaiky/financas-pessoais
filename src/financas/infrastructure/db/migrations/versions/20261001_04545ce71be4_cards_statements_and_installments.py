"""cards statements and installments

Revision ID: 04545ce71be4
Revises: ba63e5e4254f
Create Date: 2026-10-01 20:49:39.313800
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "04545ce71be4"
down_revision: str | None = "ba63e5e4254f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installment_plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("category_id", sa.String(length=32), nullable=False),
        sa.Column("installment_total", sa.Integer(), nullable=False),
        sa.Column("purchased_on", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "installment_total > 1", name=op.f("ck_installment_plans_more_than_one")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_installment_plans_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_installment_plans_category_id_categories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installment_plans")),
    )
    op.create_table(
        "statements",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("closing_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("informed_total_cents", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'", name=op.f("ck_statements_month_format")
        ),
        sa.CheckConstraint("due_date > closing_date", name=op.f("ck_statements_due_after_closing")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_statements_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statements")),
        sa.UniqueConstraint("account_id", "month", name=op.f("uq_statements_account_id")),
    )
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("closing_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("due_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("credit_limit_cents", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f("ck_accounts_card_fields"),
            "(kind = 'credit_card' AND closing_day IS NOT NULL AND closing_day BETWEEN 1 AND 31"
            " AND due_day IS NOT NULL AND due_day BETWEEN 1 AND 31)"
            " OR (kind <> 'credit_card' AND closing_day IS NULL AND due_day IS NULL"
            " AND credit_limit_cents IS NULL)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_accounts_limit_not_negative"),
            "credit_limit_cents IS NULL OR credit_limit_cents >= 0",
        )

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("statement_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("plan_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("installment_number", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_transactions_plan_id"), ["plan_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_transactions_statement_id"), ["statement_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_transactions_statement_id_statements"),
            "statements",
            ["statement_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_transactions_plan_id_installment_plans"),
            "installment_plans",
            ["plan_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_transactions_plan_fields"),
            "(plan_id IS NULL AND installment_number IS NULL)"
            " OR (plan_id IS NOT NULL AND installment_number IS NOT NULL AND installment_number >= 1)",
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_transactions_plan_fields"), type_="check")
        batch_op.drop_constraint(
            batch_op.f("fk_transactions_plan_id_installment_plans"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_transactions_statement_id_statements"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_transactions_statement_id"))
        batch_op.drop_index(batch_op.f("ix_transactions_plan_id"))
        batch_op.drop_column("installment_number")
        batch_op.drop_column("plan_id")
        batch_op.drop_column("statement_id")

    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_accounts_limit_not_negative"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_accounts_card_fields"), type_="check")
        batch_op.drop_column("credit_limit_cents")
        batch_op.drop_column("due_day")
        batch_op.drop_column("closing_day")

    op.drop_table("statements")
    op.drop_table("installment_plans")
