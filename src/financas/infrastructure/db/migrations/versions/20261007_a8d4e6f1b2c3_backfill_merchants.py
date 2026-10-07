"""backfill merchants

A data migration: every existing expense and refund gets a canonical ``merchant`` when one can be
told (a trailing " - Merchant" in the description, a known alias, what the user's own entries
already say) and an alias spelling of a typed merchant becomes its canonical name. A stripped
suffix leaves the description (and ``description_search``, and the installment plan's text).
It runs once, when a database crosses this revision, after ``Container.migrate()`` has taken its
backup. The rules are ``domain/services/merchants.plan_backfill`` (the same ones as
``financas merchants backfill``). It cannot be undone by ``downgrade``: the backup is the way back.

Revision ID: a8d4e6f1b2c3
Revises: f6a1b3c9d2e4
Create Date: 2026-10-07 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from financas.domain.models import TransactionKind
from financas.domain.services.merchants import BackfillRow, plan_backfill
from financas.domain.services.text import normalize_search

revision: str = "a8d4e6f1b2c3"
down_revision: str | None = "f6a1b3c9d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    found = bind.execute(
        sa.text("SELECT id, kind, description, merchant, plan_id FROM transactions")
    ).fetchall()
    if not found:
        return
    plans = {row[0]: row[4] for row in found}
    texts = {row[0]: row[2] for row in found}
    rows = [BackfillRow(r[0], TransactionKind(r[1]), r[2], r[3]) for r in found]
    changes = plan_backfill(rows)
    if not changes:
        return
    bind.execute(
        sa.text(
            "UPDATE transactions SET merchant = :merchant, description = :description,"
            " description_search = :search WHERE id = :id"
        ),
        [
            {
                "id": c.id,
                "merchant": c.merchant,
                "description": c.description or texts[c.id],
                "search": normalize_search(c.description or texts[c.id]),
            }
            for c in changes
        ],
    )
    cleaned = [
        {"plan": plans[c.id], "old": texts[c.id], "new": c.description}
        for c in changes
        if c.description is not None and plans[c.id]
    ]
    if cleaned:  # an installment plan shows the description of its installments
        bind.execute(
            sa.text(
                "UPDATE installment_plans SET description = :new WHERE id = :plan AND description = :old"
            ),
            cleaned,
        )


def downgrade() -> None:
    """Nothing to undo in the schema; the data change is restored from the backup."""
