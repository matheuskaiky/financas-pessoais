"""The whole feed run behind ports: preview (dry run) or apply (CLAUDE.md 13.3).

One orchestration for every adapter (the script and the web page). It writes no text: it returns
a :class:`FeedOutcome` with a status code, the issues (codes with line and column), the analysis
and, after an apply, the counts. The adapters turn those into pt-BR.

Apply is all-or-nothing: backup, a *working copy* of the database, the use cases on the copy,
verification, and the swap only when every check passed. Any failure discards the copy and leaves
the real database untouched.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Protocol

from financas.application.csvfeed import parse_feed, plan_feed
from financas.application.csvfeed.apply import ApplyFeed, FeedApplyResult, snapshot, verify_feed
from financas.application.csvfeed.context import load_context
from financas.application.csvfeed.model import FeedAnalysis, FeedIssue
from financas.application.imports.apply import Verification
from financas.domain.errors import DomainError
from financas.domain.ports import Clock, UnitOfWork


class FeedStatus(StrEnum):
    PREVIEW = "preview"  # dry run succeeded: nothing written
    APPLIED = "applied"
    INVALID = "invalid"  # syntax or business problems: ``issues``
    NO_ROWS = "no_rows"
    NO_DATABASE = "no_database"
    NEEDS_MIGRATION = "needs_migration"  # a dry run needs an up-to-date database
    ALREADY_APPLIED = "already_applied"
    FAILED = "failed"  # the apply failed: ``error`` or ``failure_code``; database untouched
    VERIFY_FAILED = "verify_failed"  # the checks failed: ``verification``; database untouched


class FeedWork(Protocol):
    """A working copy of the database, thrown away unless the apply is adopted."""

    @property
    def uow(self) -> UnitOfWork: ...
    @property
    def clock(self) -> Clock: ...
    def migrate(self) -> None: ...
    def discard(self) -> None: ...


class FeedWorkspace(Protocol):
    """The database side of a run: implemented by the container."""

    @property
    def uow(self) -> UnitOfWork: ...
    @property
    def clock(self) -> Clock: ...
    def database_exists(self) -> bool: ...
    def needs_migration(self) -> bool: ...
    def backup(self) -> Path: ...
    def open_work_copy(self) -> FeedWork: ...
    def adopt(self, work: FeedWork) -> None: ...
    def record_failure(self, error: Exception, origin: str) -> str:
        """Log an unexpected failure (no values) and return its short code."""
        ...


class FeedLedger(Protocol):
    """Files already applied, by SHA-256 (no schema change)."""

    def find(self, sha256: str) -> Mapping[str, object] | None: ...
    def record(
        self, sha256: str, when: dt.datetime, counts: Mapping[str, int], forced: bool
    ) -> None: ...


@dataclass(frozen=True)
class FeedOutcome:
    status: FeedStatus
    sha256: str = ""
    data_rows: int = 0
    issues: tuple[FeedIssue, ...] = ()
    analysis: FeedAnalysis | None = None
    names: dict[str, str] = field(default_factory=lambda: {})  # account id -> nickname
    already: Mapping[str, object] | None = None  # ledger entry of an earlier apply
    backup: Path | None = None
    result: FeedApplyResult | None = None
    verification: Verification | None = None
    error: DomainError | None = None
    failure_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (FeedStatus.PREVIEW, FeedStatus.APPLIED)


def _analyze(
    ws: FeedWorkspace | FeedWork, data: bytes, delimiter: str | None
) -> tuple[FeedAnalysis, dict[str, str]]:
    context = load_context(ws.uow, ws.clock)
    parsed = parse_feed(data, context.today, delimiter)
    return plan_feed(parsed, context), {a.id: a.nickname for a in context.accounts}


def run_feed(
    ws: FeedWorkspace,
    ledger: FeedLedger,
    data: bytes,
    delimiter: str | None = None,
    *,
    apply: bool = False,
    force: bool = False,
    payment_description: str = "",
    origin: str = "csvfeed",
) -> FeedOutcome:
    """Dry run (default) or apply a file. ``payment_description`` is the data value stored on a
    statement payment that has no description (the adapter passes the pt-BR text)."""
    if not ws.database_exists():
        return FeedOutcome(FeedStatus.NO_DATABASE)

    # 1. syntax (no database needed): every problem with its line and column, nothing written
    parsed = parse_feed(data, ws.clock.today(), delimiter)
    make = partial(FeedOutcome, sha256=parsed.sha256, data_rows=parsed.data_rows)
    if parsed.issues:
        return make(FeedStatus.INVALID, issues=parsed.issues)
    if parsed.data_rows == 0:
        return make(FeedStatus.NO_ROWS)
    already = ledger.find(parsed.sha256)
    if apply and already is not None and not force:
        return make(FeedStatus.ALREADY_APPLIED, already=already)

    # 2. business rules against the database
    migrate_first = ws.needs_migration()
    if migrate_first and not apply:
        return make(FeedStatus.NEEDS_MIGRATION)
    analysis: FeedAnalysis | None = None
    names: dict[str, str] = {}
    if not migrate_first:
        analysis, names = _analyze(ws, data, delimiter)
        if not analysis.ok:
            return make(FeedStatus.INVALID, issues=analysis.issues, analysis=analysis, names=names)
        if not apply:
            return make(FeedStatus.PREVIEW, analysis=analysis, names=names, already=already)

    # 3. apply: backup, working copy, use cases, verification, swap
    backup = ws.backup()
    work = ws.open_work_copy()
    try:
        if migrate_first:
            work.migrate()
        analysis, names = _analyze(work, data, delimiter)
        if not analysis.ok or analysis.plan is None:
            work.discard()
            return make(
                FeedStatus.INVALID,
                issues=analysis.issues,
                analysis=analysis,
                names=names,
                backup=backup,
            )
        plan = analysis.plan
        before = snapshot(work.uow, plan)
        result = ApplyFeed(work.uow, work.clock).execute(plan, payment_description)
        verification = verify_feed(work.uow, plan, before)
    except DomainError as error:
        work.discard()
        return make(FeedStatus.FAILED, analysis=analysis, names=names, backup=backup, error=error)
    except Exception as error:
        work.discard()
        code = ws.record_failure(error, origin)
        return make(
            FeedStatus.FAILED,
            analysis=analysis,
            names=names,
            backup=backup,
            failure_code=code,
        )

    if not verification.ok:
        work.discard()
        return make(
            FeedStatus.VERIFY_FAILED,
            analysis=analysis,
            names=names,
            backup=backup,
            verification=verification,
            result=result,
        )
    ws.adopt(work)
    ledger.record(
        parsed.sha256,
        ws.clock.now(),
        {
            "entries": result.entries,
            "purchases": result.purchases,
            "transfers": result.transfers,
            "payments": result.payments,
            "balances": len(result.balances),
            "transactions": plan.transactions,
            "rows": analysis.data_rows,
        },
        forced=already is not None,
    )
    return make(
        FeedStatus.APPLIED,
        analysis=analysis,
        names=names,
        backup=backup,
        result=result,
        verification=verification,
        already=already,
    )
