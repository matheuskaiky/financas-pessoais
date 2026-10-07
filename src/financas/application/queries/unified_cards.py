"""The unified cards view (``/cards?card=all``): one hero, one filter, one timeline.

* The hero is the sum of the "Fatura atual" of the **included** cards (the open statement of each,
  in integer cents, exactly what the card faces show). If one card has no readable statement the
  figure is unavailable: a partial sum would read as a total.
* The limit meter sums committed and limit over the included cards **that have a limit**; with none
  the meter is "not informed", never 0 %.
* The timeline's first page is the entries of each included card's open statement (the set that
  adds up to the hero); "older" pages walk the earlier (closed and paid) statements, newest first,
  keyset on ``(posted_on, id)`` so rows that share a date are never repeated or skipped.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from financas.application.queries.cards import CardView, is_locked, statement_view
from financas.domain.models import Account, StatementStatus, Transaction
from financas.domain.ports import Clock, UnitOfWork
from financas.domain.services.statements import LimitUsage, limit_usage

PAGE_SIZE = 50


@dataclass(frozen=True)
class UnifiedSummary:
    included: int
    total: int  # active cards
    hero_cents: int | None  # None: a statement is unavailable
    unavailable: int
    committed_cents: int  # over the included cards that have a limit
    limit_cents: int | None  # None: no included card has a limit
    with_limit: int
    without_limit: int
    usage: LimitUsage | None  # None when no included card has a limit


def parse_card_ids(text: str, known: Sequence[str]) -> list[str]:
    """``"a,b"`` -> the known ids among them, in face order; empty (or all unknown) = every card."""
    asked = {part.strip() for part in text.split(",") if part.strip()}
    chosen = [card_id for card_id in known if card_id in asked]
    return chosen or list(known)


def summarize(included: Sequence[CardView], total: int) -> UnifiedSummary:
    unavailable = sum(1 for v in included if v.telemetry is None)
    hero = (
        None
        if unavailable
        else sum(v.telemetry.open_balance_cents for v in included if v.telemetry)
    )
    limited = [v for v in included if v.usage.limit_cents]
    committed = sum(v.usage.committed_cents for v in limited)
    limit = sum(v.usage.limit_cents or 0 for v in limited) if limited else None
    return UnifiedSummary(
        included=len(included),
        total=total,
        hero_cents=hero,
        unavailable=unavailable,
        committed_cents=committed,
        limit_cents=limit,
        with_limit=len(limited),
        without_limit=len(included) - len(limited),
        usage=limit_usage(committed, limit) if limited else None,
    )


@dataclass(frozen=True)
class TimelinePage:
    entries: tuple[Transaction, ...]
    cards: dict[str, Account]  # by id: the tag of each row
    paid_statement_ids: frozenset[str]  # rows on them are history
    older_cursor: str | None  # the ``after`` of the next "Ver lançamentos anteriores", if any
    older_available: bool  # there are earlier statements to walk (first page only)


def cursor_of(entry: Transaction) -> str:
    return f"{entry.posted_on.isoformat()}_{entry.id}"


def parse_cursor(text: str) -> tuple[dt.date, str] | None:
    day, _, entry_id = text.partition("_")
    try:
        return dt.date.fromisoformat(day), entry_id
    except ValueError:
        return None


class ListUnifiedTimeline:
    """Page 1 (``older=False``) or an older page (``older=True``, with an optional cursor)."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(
        self, views: Sequence[CardView], *, older: bool = False, after: str = ""
    ) -> TimelinePage:
        today = self._clock.today()
        cards = {v.account.id: v.account for v in views}
        wanted: list[str] = []  # statement ids to read
        for view in views:
            if older:
                wanted += [
                    s.statement.id
                    for s in view.statements
                    if s.status in (StatementStatus.CLOSED, StatementStatus.PAID)
                ]
            elif view.open_statement is not None:
                wanted.append(view.open_statement.statement.id)
        older_exists = any(
            s.status in (StatementStatus.CLOSED, StatementStatus.PAID)
            for v in views
            for s in v.statements
        )
        with self._uow as uow:
            entries: list[Transaction] = []
            for statement_id in wanted:
                entries += uow.transactions.list_by_statement(statement_id, include_refunded=True)
            entries.sort(key=lambda t: (t.posted_on, t.id), reverse=True)
            if older and (cursor := parse_cursor(after)):
                day, entry_id = cursor
                entries = [t for t in entries if (t.posted_on, t.id) < (day, entry_id)]
            # the first page is everything that adds up to the hero: never cut
            page = entries[:PAGE_SIZE] if older else entries
            more = older and len(entries) > PAGE_SIZE
            paid: set[str] = set()
            for statement_id in {t.statement_id for t in page if t.statement_id}:
                statement = uow.statements.get(statement_id)
                if statement and is_locked(statement_view(uow, statement, today)):
                    paid.add(statement_id)
        return TimelinePage(
            tuple(page),
            cards,
            frozenset(paid),
            older_cursor=cursor_of(page[-1]) if page and more else None,
            older_available=older_exists if not older else more,
        )
