"""Reads what the planner needs from the database (accounts, categories, statements, anchors)."""

from financas.application.csvfeed.model import (
    AccountInfo,
    CategoryInfo,
    FeedContext,
    StatementInfo,
)
from financas.application.queries.cards import is_locked, statement_view
from financas.domain.models import AccountKind
from financas.domain.ports import Clock, UnitOfWork


def load_context(uow: UnitOfWork, clock: Clock) -> FeedContext:
    today = clock.today()
    with uow as work:
        accounts = work.accounts.list_all()
        statements: list[StatementInfo] = []
        for statement in work.statements.list_all():
            view = statement_view(work, statement, today)
            statements.append(
                StatementInfo(
                    statement.account_id,
                    statement.month,
                    statement.closing_date,
                    statement.due_date,
                    is_locked(view),
                    view.total_cents,
                    view.paid_cents,
                )
            )
        anchors = frozenset(
            (a.id, anchor.on_date)
            for a in accounts
            if a.kind is not AccountKind.CREDIT_CARD
            for anchor in work.anchors.list_for_account(a.id)
        )
        return FeedContext(
            today=today,
            accounts=tuple(
                AccountInfo(
                    a.id,
                    a.nickname,
                    a.kind,
                    a.is_active,
                    a.due_day,
                    a.closing_days_before_due,
                    a.tracking,
                )
                for a in accounts
            ),
            categories=tuple(
                CategoryInfo(c.id, c.slug, c.name, c.kind) for c in work.categories.list_all()
            ),
            statements=tuple(statements),
            anchors=anchors,
        )
