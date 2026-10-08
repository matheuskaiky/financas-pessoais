"""payment method on transactions

``transactions.payment_method``: how an entry was paid (``pix``, ``debito``, ``boleto``,
``transferencia``, ``dinheiro``, ``outro`` on bank accounts; ``cartao_credito`` on cards). Optional
and indexed for the "Forma de pagamento" filter of the entries list. Entries already on a card are
``cartao_credito`` (that is a fact, not a guess); bank entries stay blank until the user says.

Revision ID: c0f6a8b3d5e7
Revises: b9e5f7a2c4d6
Create Date: 2026-10-07 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0f6a8b3d5e7"
down_revision: str | None = "b9e5f7a2c4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METHODS = "'pix', 'debito', 'boleto', 'transferencia', 'dinheiro', 'outro', 'cartao_credito'"


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("payment_method", sa.String(length=20), nullable=True))
    op.execute(
        "UPDATE transactions SET payment_method = 'cartao_credito' WHERE kind <> 'transfer'"
        " AND account_id IN (SELECT id FROM accounts WHERE kind = 'credit_card')"
    )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_transactions_payment_method"),
            f"payment_method IN ({METHODS})",
        )
        batch_op.create_index(
            batch_op.f("ix_transactions_payment_method"), ["payment_method"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transactions_payment_method"))
        batch_op.drop_constraint(batch_op.f("ck_transactions_payment_method"), type_="check")
        batch_op.drop_column("payment_method")
