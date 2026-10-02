"""SQLAlchemy tables. Enums are stored as ASCII strings with check constraints."""

import datetime as dt
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from financas.domain.models import AccountKind, CategoryGroup, CategoryKind, TransactionKind

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING)


def enum_column[E: StrEnum](enum: type[E], name: str) -> sa.Enum:
    return sa.Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


class InstitutionRow(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True)
    name: Mapped[str] = mapped_column(sa.String(120))
    group_slug: Mapped[str | None] = mapped_column(sa.String(64))
    color: Mapped[str | None] = mapped_column(sa.String(7))
    image_id: Mapped[str | None] = mapped_column(sa.String(32))


class AccountRow(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        sa.CheckConstraint(
            "(kind = 'credit_card' AND closing_day IS NOT NULL AND closing_day BETWEEN 1 AND 31"
            " AND due_day IS NOT NULL AND due_day BETWEEN 1 AND 31)"
            " OR (kind <> 'credit_card' AND closing_day IS NULL AND due_day IS NULL"
            " AND credit_limit_cents IS NULL)",
            name="card_fields",
        ),
        sa.CheckConstraint(
            "credit_limit_cents IS NULL OR credit_limit_cents >= 0", name="limit_not_negative"
        ),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    kind: Mapped[AccountKind] = mapped_column(enum_column(AccountKind, "account_kind"))
    institution_id: Mapped[str] = mapped_column(sa.ForeignKey("institutions.id"))
    nickname: Mapped[str] = mapped_column(sa.String(120))
    is_active: Mapped[bool] = mapped_column(default=True)
    color: Mapped[str | None] = mapped_column(sa.String(7))
    image_id: Mapped[str | None] = mapped_column(sa.String(32))
    closing_day: Mapped[int | None] = mapped_column(sa.Integer)
    due_day: Mapped[int | None] = mapped_column(sa.Integer)
    credit_limit_cents: Mapped[int | None] = mapped_column(sa.BigInteger)


class CategoryRow(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True)
    name: Mapped[str] = mapped_column(sa.String(120))
    group: Mapped[CategoryGroup] = mapped_column(enum_column(CategoryGroup, "category_group"))
    kind: Mapped[CategoryKind] = mapped_column(enum_column(CategoryKind, "category_kind"))
    monthly_budget_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    color: Mapped[str | None] = mapped_column(sa.String(7))


class StatementRow(Base):
    __tablename__ = "statements"
    __table_args__ = (
        sa.UniqueConstraint("account_id", "month"),
        sa.CheckConstraint("month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'", name="month_format"),
        sa.CheckConstraint("due_date > closing_date", name="due_after_closing"),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(sa.ForeignKey("accounts.id"))
    month: Mapped[str] = mapped_column(sa.String(7))  # closing month, YYYY-MM
    closing_date: Mapped[dt.date] = mapped_column(sa.Date)
    due_date: Mapped[dt.date] = mapped_column(sa.Date)
    informed_total_cents: Mapped[int | None] = mapped_column(sa.BigInteger)


class InstallmentPlanRow(Base):
    __tablename__ = "installment_plans"
    __table_args__ = (sa.CheckConstraint("installment_total > 1", name="more_than_one"),)

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(sa.ForeignKey("accounts.id"))
    description: Mapped[str] = mapped_column(sa.String(300))
    category_id: Mapped[str] = mapped_column(sa.ForeignKey("categories.id"))
    installment_total: Mapped[int] = mapped_column(sa.Integer)
    purchased_on: Mapped[dt.date | None] = mapped_column(sa.Date)


class TransactionRow(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        sa.CheckConstraint(
            "(plan_id IS NULL AND installment_number IS NULL)"
            " OR (plan_id IS NOT NULL AND installment_number IS NOT NULL"
            " AND installment_number >= 1)",
            name="plan_fields",
        ),
        sa.CheckConstraint(
            "(kind = 'expense' AND amount_cents < 0)"
            " OR (kind IN ('income', 'refund') AND amount_cents > 0)"
            " OR (kind = 'transfer' AND amount_cents <> 0)",
            name="sign_matches_kind",
        ),
        sa.Index("ix_transactions_account_id_posted_on", "account_id", "posted_on"),
        sa.Index("ix_transactions_description_search_kind", "description_search", "kind"),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(sa.ForeignKey("accounts.id"))
    posted_on: Mapped[dt.date] = mapped_column(sa.Date, index=True)
    kind: Mapped[TransactionKind] = mapped_column(enum_column(TransactionKind, "transaction_kind"))
    category_id: Mapped[str] = mapped_column(sa.ForeignKey("categories.id"))
    amount_cents: Mapped[int] = mapped_column(sa.BigInteger)
    description: Mapped[str] = mapped_column(sa.String(300))
    description_search: Mapped[str] = mapped_column(sa.String(300))
    is_recurring: Mapped[bool] = mapped_column(default=False)
    transfer_id: Mapped[str | None] = mapped_column(sa.String(32), index=True)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    statement_id: Mapped[str | None] = mapped_column(sa.ForeignKey("statements.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(sa.ForeignKey("installment_plans.id"), index=True)
    installment_number: Mapped[int | None] = mapped_column(sa.Integer)


class BalanceAnchorRow(Base):
    __tablename__ = "balance_anchors"
    __table_args__ = (sa.UniqueConstraint("account_id", "on_date"),)

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(sa.ForeignKey("accounts.id"))
    on_date: Mapped[dt.date] = mapped_column(sa.Date)
    balance_cents: Mapped[int] = mapped_column(sa.BigInteger)
    note: Mapped[str | None] = mapped_column(sa.Text)
