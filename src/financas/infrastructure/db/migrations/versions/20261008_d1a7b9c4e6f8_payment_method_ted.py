"""payment method TED

``transactions.payment_method`` gains ``ted`` (money an employer, a client or a person sends by
TED: the income form offers PIX | TED | Outro). ``transferencia`` stays for DOC and other transfers.

Revision ID: d1a7b9c4e6f8
Revises: c0f6a8b3d5e7
Create Date: 2026-10-08 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d1a7b9c4e6f8"
down_revision: str | None = "c0f6a8b3d5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "'pix', 'debito', 'boleto', 'transferencia', 'dinheiro', 'outro', 'cartao_credito'"
NEW = "'pix', 'debito', 'boleto', 'ted', 'transferencia', 'dinheiro', 'outro', 'cartao_credito'"


def _recreate(allowed: str) -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_transactions_payment_method"), type_="check")
        batch_op.create_check_constraint(
            batch_op.f("ck_transactions_payment_method"), f"payment_method IN ({allowed})"
        )


def upgrade() -> None:
    _recreate(NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE transactions SET payment_method = 'transferencia' WHERE payment_method = 'ted'"
    )
    _recreate(OLD)
