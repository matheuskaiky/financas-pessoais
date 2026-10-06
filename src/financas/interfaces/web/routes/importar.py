"""Importar lançamentos: ``/importar``. Feeds the database from a file the user prepared.

The file follows the project's own format (CLAUDE.md 13.3, the guide in ``docs/``). Two steps, both
over HTMX: *analyze* (a dry run: nothing is written to the database) and *apply* (backup, working
copy, the existing use cases, verification, swap: all or nothing). Everything is done by
``application/csvfeed/service.py``, the same code as ``scripts/feed_from_csv.py``.

The bytes of an analyzed file wait in ``data/import/pending/<sha256>.csv`` (``pending.py``); the
apply request carries only that token. In demo mode the analysis works and the apply is refused.
"""

import threading
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from financas.application.csvfeed import template_text
from financas.application.csvfeed.model import FeedIssue, FeedKind
from financas.application.csvfeed.reader import file_sha256
from financas.application.csvfeed.service import FeedOutcome, FeedStatus, run_feed
from financas.interfaces import csvfeed as report
from financas.interfaces.formatting import format_date
from financas.interfaces.messages import render_error
from financas.interfaces.messages.csvfeed import PAYMENT_DESCRIPTION
from financas.interfaces.messages.importar import (
    COUNT_LABELS,
    IMPORT,
    IMPORT_ISSUE_OVERRIDES,
)
from financas.interfaces.web import nav
from financas.interfaces.web.pending import PendingFiles, is_token
from financas.interfaces.web.routes import WebContext

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_LISTED_PROBLEMS = 20
MAX_LISTED_WARNINGS = 10
ORIGIN = "/importar/aplicar"


@dataclass(frozen=True)
class AccountLine:
    name: str
    count: int
    cents: int


@dataclass(frozen=True)
class BalanceLine:
    label: str
    informed_cents: int
    computed_cents: int | None
    difference_cents: int | None


@dataclass
class ResultView:
    """What ``_importar_result.html`` shows. ``state``: preview, problems, blocked, applied."""

    state: str
    title: str = ""
    text: str = ""
    problems: list[str] = field(default_factory=lambda: [])
    more: str = ""
    counts: list[tuple[str, int]] = field(default_factory=lambda: [])
    accounts: list[AccountLine] = field(default_factory=lambda: [])
    warnings: list[str] = field(default_factory=lambda: [])
    balances: list[BalanceLine] = field(default_factory=lambda: [])
    token: str | None = None
    demo: bool = False
    created: int = 0


def issue_text(issue: FeedIssue) -> str:
    """One problem as a plain sentence: ``Linha 12, coluna “amount”: …``."""
    override = IMPORT_ISSUE_OVERRIDES.get(issue.code)
    message = override.format(**issue.params) if override else report.issue_message(issue)
    if issue.line is None:
        return IMPORT["issue_file"].format(message=message)
    if issue.column is None:
        return IMPORT["issue_line"].format(line=issue.line, message=message)
    return IMPORT["issue_line_column"].format(line=issue.line, column=issue.column, message=message)


def problems_view(issues: tuple[FeedIssue, ...]) -> ResultView:
    shown = [issue_text(i) for i in issues[:MAX_LISTED_PROBLEMS]]
    extra = len(issues) - len(shown)
    return ResultView(
        "problems",
        title=IMPORT["problems_title"].format(count=len(issues)),
        text=IMPORT["problems_help"],
        problems=shown,
        more=IMPORT["problems_more"].format(count=extra) if extra > 0 else "",
    )


def blocked(text: str, title: str = "") -> ResultView:
    return ResultView("blocked", title=title, text=text)


def summary_counts(outcome: FeedOutcome) -> list[tuple[str, int]]:
    """Counts by kind for the analysis card. Installment purchases and statement payments are
    rows of kind expense and transfer; they are shown on their own line, not twice."""
    assert outcome.analysis is not None and outcome.analysis.plan is not None
    plan = outcome.analysis.plan
    rows = plan.rows_by_kind
    counts = [
        (COUNT_LABELS["income"], rows.get(FeedKind.INCOME, 0)),
        (COUNT_LABELS["expense"], rows.get(FeedKind.EXPENSE, 0) - plan.purchases),
        (COUNT_LABELS["transfer"], rows.get(FeedKind.TRANSFER, 0) - plan.payments),
        (COUNT_LABELS["installments"], plan.purchases),
        (COUNT_LABELS["payments"], plan.payments),
    ]
    if rows.get(FeedKind.REFUND):
        counts.append((COUNT_LABELS["refund"], rows[FeedKind.REFUND]))
    if plan.balances:
        counts.append((COUNT_LABELS["balance"], plan.balances))
    return counts


def preview_view(outcome: FeedOutcome, *, token: str | None, demo: bool) -> ResultView:
    assert outcome.analysis is not None and outcome.analysis.plan is not None
    plan = outcome.analysis.plan
    names = outcome.names
    return ResultView(
        "preview",
        title=IMPORT["ok_title"],
        text=IMPORT["ok_text"],
        counts=summary_counts(outcome),
        accounts=[
            AccountLine(names[t.account_id], t.count, t.sum_cents)
            for t in sorted(plan.account_totals, key=lambda t: names[t.account_id].casefold())
        ],
        warnings=[report.render_warning(w) for w in plan.warnings[:MAX_LISTED_WARNINGS]],
        token=token,
        demo=demo,
        created=plan.transactions,
    )


def applied_view(outcome: FeedOutcome) -> ResultView:
    assert outcome.analysis is not None and outcome.analysis.plan is not None
    assert outcome.result is not None
    result = outcome.result
    created = outcome.analysis.plan.transactions
    balances = [
        BalanceLine(
            IMPORT["balance_line"].format(
                account=outcome.names[b.account_id], date=format_date(b.on_date)
            ),
            b.informed_cents,
            b.computed_cents,
            b.difference_cents,
        )
        for b in result.balances
    ]
    return ResultView(
        "applied",
        title=IMPORT["applied_title"],
        text=IMPORT["applied_text"].format(count=created),
        counts=summary_counts(outcome),
        balances=balances,
        created=created,
    )


def failure_view(outcome: FeedOutcome) -> ResultView:
    """The view of an outcome that is not a success; never a 500, never a stack trace."""
    status = outcome.status
    if status is FeedStatus.INVALID:
        return problems_view(outcome.issues)
    if status is FeedStatus.NO_ROWS:
        return blocked(IMPORT["no_rows"])
    if status is FeedStatus.NEEDS_MIGRATION:
        return blocked(IMPORT["needs_migration"])
    if status is FeedStatus.NO_DATABASE:
        return blocked(IMPORT["no_database"])
    if status is FeedStatus.ALREADY_APPLIED:
        assert outcome.already is not None
        return blocked(IMPORT["already"].format(when=report.applied_when(outcome.already)))
    if status is FeedStatus.VERIFY_FAILED:
        return blocked(IMPORT["verify_failed"])
    if outcome.error is not None:
        reason = render_error(outcome.error)
        return blocked(IMPORT["apply_failed"].format(reason=reason.rstrip(".")))
    return blocked(IMPORT["apply_failed_code"].format(code=outcome.failure_code or "-"))


def register(app: FastAPI, ctx: WebContext) -> None:
    nav.register(
        nav.NavEntry(
            id="importar",
            label="Importar dados",
            icon=nav.ICONS["importar"],
            href="/importar",
            group="movement",
            order=40,
            hint="registre vários lançamentos de uma vez, de um arquivo seu",
        )
    )
    c = ctx.c
    pending = PendingFiles(c.import_dir / "pending")
    lock = threading.Lock()  # one apply at a time (SQLite has one writer)
    limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)

    def fragment(request: Request, view: ResultView) -> HTMLResponse:
        return ctx.templates.TemplateResponse(
            request, "_importar_result.html", {"view": view, "demo": c.settings.demo}
        )

    def analyze(data: bytes) -> ResultView:
        ledger = report.FileLedger(c.import_dir)
        earlier = ledger.find(file_sha256(data))
        if earlier is not None:  # said first: the rules would complain about a paid statement
            return failure_view(FeedOutcome(FeedStatus.ALREADY_APPLIED, already=earlier))
        outcome = run_feed(c.feed_workspace(), ledger, data)
        if outcome.status is not FeedStatus.PREVIEW:
            return failure_view(outcome)
        if outcome.already is not None:
            return failure_view(FeedOutcome(FeedStatus.ALREADY_APPLIED, already=outcome.already))
        token = None if c.settings.demo else pending.save(data)  # only a valid file is stored
        return preview_view(outcome, token=token, demo=c.settings.demo)

    def apply(token: str) -> ResultView:
        data = pending.load(token)
        if data is None:
            return blocked(IMPORT["expired"])
        if not lock.acquire(blocking=False):
            return blocked(IMPORT["busy"])
        try:
            outcome = run_feed(
                c.feed_workspace(),
                report.FileLedger(c.import_dir),
                data,
                apply=True,
                payment_description=PAYMENT_DESCRIPTION,
                origin=ORIGIN,
            )
        finally:
            lock.release()
        if outcome.status in (FeedStatus.APPLIED, FeedStatus.ALREADY_APPLIED):
            pending.delete(token)
        if outcome.status is FeedStatus.APPLIED:
            return applied_view(outcome)
        return failure_view(outcome)

    @app.get("/importar", response_class=HTMLResponse)
    def importar_page(request: Request) -> HTMLResponse:
        return ctx.render(
            request,
            "importar.html",
            {"limit_mb": limit_mb, "title": IMPORT["page_title"], "subtitle": IMPORT["subtitle"]},
        )

    @app.get("/importar/modelo")
    def importar_template() -> Response:
        return Response(
            template_text().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="modelo-lancamentos.csv"'},
        )

    @app.post("/importar/analisar", response_class=HTMLResponse)
    async def importar_analyze(request: Request) -> HTMLResponse:
        # validated before anything is stored: size (header first, then the bytes), type, encoding
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + 64 * 1024:
            return fragment(request, blocked(IMPORT["too_large"].format(limit=limit_mb)))
        try:
            form = await request.form()
        except Exception:
            return fragment(request, blocked(IMPORT["unreadable"]))
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            return fragment(request, blocked(IMPORT["no_file"]))
        if not upload.filename.lower().endswith(".csv"):
            return fragment(request, blocked(IMPORT["wrong_type"]))
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return fragment(request, blocked(IMPORT["too_large"].format(limit=limit_mb)))
        pending.purge(c.clock.now().timestamp())
        view = await run_in_threadpool(analyze, data)
        return fragment(request, view)

    @app.post("/importar/aplicar", response_class=HTMLResponse)
    async def importar_apply(request: Request) -> HTMLResponse:
        if c.settings.demo:
            return fragment(request, blocked(IMPORT["demo"]))
        form = await request.form()
        token = str(form.get("token", ""))
        if not is_token(token):
            return fragment(request, blocked(IMPORT["expired"]))
        return fragment(request, await run_in_threadpool(apply, token))
