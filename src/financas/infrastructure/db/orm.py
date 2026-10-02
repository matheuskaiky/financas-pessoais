"""SQLAlchemy tables. Enums are stored as ASCII strings with check constraints."""

import datetime as dt
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from financas.domain.models import (
    AccountKind,
    AssetClass,
    CategoryGroup,
    CategoryKind,
    HoldingStatus,
    Indexer,
    InstrumentType,
    InvestmentTracking,
    Liquidity,
    RateMode,
    TransactionKind,
)

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


COLOR_CHECK = (
    "color IS NULL OR (length(color) = 7"
    " AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]')"
)


class InstitutionRow(Base):
    __tablename__ = "institutions"
    __table_args__ = (sa.CheckConstraint(COLOR_CHECK, name="color_format"),)

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
            "(kind = 'credit_card' AND closing_days_before_due IS NOT NULL"
            " AND closing_days_before_due BETWEEN 1 AND 27"
            " AND due_day IS NOT NULL AND due_day BETWEEN 1 AND 31)"
            " OR (kind <> 'credit_card' AND closing_days_before_due IS NULL AND due_day IS NULL"
            " AND credit_limit_cents IS NULL)",
            name="card_fields",
        ),
        sa.CheckConstraint(
            "credit_limit_cents IS NULL OR credit_limit_cents >= 0", name="limit_not_negative"
        ),
        sa.CheckConstraint(COLOR_CHECK, name="color_format"),
        sa.CheckConstraint(
            "(kind = 'investment' AND tracking IS NOT NULL AND asset_class IS NOT NULL)"
            " OR (kind <> 'investment' AND tracking IS NULL AND asset_class IS NULL"
            " AND is_emergency_fund = 0)",
            name="investment_fields",
        ),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    kind: Mapped[AccountKind] = mapped_column(enum_column(AccountKind, "account_kind"))
    institution_id: Mapped[str] = mapped_column(sa.ForeignKey("institutions.id"))
    nickname: Mapped[str] = mapped_column(sa.String(120))
    is_active: Mapped[bool] = mapped_column(default=True)
    color: Mapped[str | None] = mapped_column(sa.String(7))
    image_id: Mapped[str | None] = mapped_column(sa.String(32))
    closing_days_before_due: Mapped[int | None] = mapped_column(sa.Integer)
    due_day: Mapped[int | None] = mapped_column(sa.Integer)
    credit_limit_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    tracking: Mapped[InvestmentTracking | None] = mapped_column(
        enum_column(InvestmentTracking, "investment_tracking")
    )
    asset_class: Mapped[AssetClass | None] = mapped_column(enum_column(AssetClass, "asset_class"))
    is_emergency_fund: Mapped[bool] = mapped_column(default=False, server_default=sa.false())


class CategoryRow(Base):
    __tablename__ = "categories"
    __table_args__ = (
        sa.CheckConstraint(COLOR_CHECK, name="color_format"),
        sa.CheckConstraint(
            "monthly_budget_cents IS NULL OR monthly_budget_cents > 0", name="budget_positive"
        ),
        # the group must agree with the kind (the spreadsheet violated this)
        sa.CheckConstraint(
            "(\"group\" IN ('essential', 'non_essential', 'charges', 'review')"
            " AND kind = 'expense') OR (\"group\" = 'income' AND kind = 'income')"
            " OR (\"group\" = 'movement' AND kind = 'neutral')",
            name="group_matches_kind",
        ),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True)
    name: Mapped[str] = mapped_column(sa.String(120))
    group: Mapped[CategoryGroup] = mapped_column(enum_column(CategoryGroup, "category_group"))
    kind: Mapped[CategoryKind] = mapped_column(enum_column(CategoryKind, "category_kind"))
    monthly_budget_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    color: Mapped[str | None] = mapped_column(sa.String(7))


class InvestmentHoldingRow(Base):
    __tablename__ = "investment_holdings"
    __table_args__ = (
        sa.CheckConstraint("principal_cents > 0", name="principal_positive"),
        sa.CheckConstraint(
            "(rate_mode IS NULL AND rate_bps IS NULL)"
            " OR (rate_mode IS NOT NULL AND rate_bps IS NOT NULL AND rate_bps >= 0)",
            name="rate_fields",
        ),
        sa.CheckConstraint(
            "(liquidity = 'at_maturity' AND maturity_on IS NOT NULL AND liquid_from IS NULL)"
            " OR liquidity = 'daily'",
            name="liquidity_dates",
        ),
        sa.CheckConstraint(
            "maturity_on IS NULL OR maturity_on > applied_on", name="maturity_after_application"
        ),
        sa.CheckConstraint(
            "liquid_from IS NULL OR liquid_from >= applied_on", name="grace_after_application"
        ),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(sa.ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(sa.String(160))
    instrument_type: Mapped[InstrumentType] = mapped_column(
        enum_column(InstrumentType, "instrument_type")
    )
    issuer_id: Mapped[str] = mapped_column(sa.ForeignKey("institutions.id"))
    indexer: Mapped[Indexer | None] = mapped_column(enum_column(Indexer, "indexer"))
    rate_mode: Mapped[RateMode | None] = mapped_column(enum_column(RateMode, "rate_mode"))
    rate_bps: Mapped[int | None] = mapped_column(sa.Integer)
    applied_on: Mapped[dt.date] = mapped_column(sa.Date)
    principal_cents: Mapped[int] = mapped_column(sa.BigInteger)
    maturity_on: Mapped[dt.date | None] = mapped_column(sa.Date)
    liquidity: Mapped[Liquidity] = mapped_column(enum_column(Liquidity, "liquidity"))
    liquid_from: Mapped[dt.date | None] = mapped_column(sa.Date)
    fgc_covered: Mapped[bool]
    is_emergency_fund: Mapped[bool]
    asset_class: Mapped[AssetClass] = mapped_column(enum_column(AssetClass, "holding_asset_class"))
    status: Mapped[HoldingStatus] = mapped_column(enum_column(HoldingStatus, "holding_status"))


class StatementRow(Base):
    __tablename__ = "statements"
    __table_args__ = (
        sa.UniqueConstraint("account_id", "month"),
        sa.CheckConstraint(
            "month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'"
            " AND substr(month, 6, 2) BETWEEN '01' AND '12'",
            name="month_format",
        ),
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
            "transfer_id IS NULL OR kind = 'transfer'", name="transfer_id_only_on_transfers"
        ),
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
    holding_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("investment_holdings.id"), index=True
    )


class BalanceAnchorRow(Base):
    __tablename__ = "balance_anchors"
    __table_args__ = (
        # one valuation per account and date, and one per holding and date
        sa.Index(
            "uq_balance_anchors_account_date",
            "account_id",
            "on_date",
            unique=True,
            sqlite_where=sa.text("holding_id IS NULL"),
        ),
        sa.Index(
            "uq_balance_anchors_holding_date",
            "holding_id",
            "on_date",
            unique=True,
            sqlite_where=sa.text("holding_id IS NOT NULL"),
        ),
        sa.CheckConstraint(
            "gross_balance_cents IS NULL OR gross_balance_cents >= balance_cents",
            name="gross_not_below_net",
        ),
    )

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(sa.ForeignKey("accounts.id"))
    on_date: Mapped[dt.date] = mapped_column(sa.Date)
    balance_cents: Mapped[int] = mapped_column(sa.BigInteger)
    note: Mapped[str | None] = mapped_column(sa.Text)
    gross_balance_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
    holding_id: Mapped[str | None] = mapped_column(sa.ForeignKey("investment_holdings.id"))
