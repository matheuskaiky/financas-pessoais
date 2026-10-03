"""pt-BR report and applied-files ledger of the CSV feed (CLAUDE.md 13.3).

Counts and totals only: no descriptions, no amounts per entry. The script (and a future web page)
calls these; the container and the files are handled by the caller.
"""

import datetime as dt
import json
from collections.abc import Mapping
from pathlib import Path

from financas.application.csvfeed.model import FeedAnalysis, FeedIssue, FeedKind, FeedPlan
from financas.domain.errors import DomainError
from financas.domain.money import YearMonth, format_brl
from financas.interfaces.formatting import format_date, format_date_short, format_month
from financas.interfaces.messages import render_error
from financas.interfaces.messages.csvfeed import (
    FEED_OVERRIDES,
    KIND_PLURAL,
    REASON_LABELS,
    REPORT,
    WARNING_MESSAGES,
)

MAX_LISTED_ISSUES = 200
LEDGER_NAME = "csv_applied.jsonl"


def issue_message(issue: FeedIssue) -> str:
    override = FEED_OVERRIDES.get(issue.code)
    if override is not None:
        return override
    return render_error(DomainError(issue.code, **issue.params))


def render_issue(issue: FeedIssue) -> str:
    message = issue_message(issue)
    if issue.line is None:
        return REPORT["issue_file"].format(code=issue.code, message=message)
    if issue.column is None:
        return REPORT["issue_nocolumn"].format(line=issue.line, code=issue.code, message=message)
    return REPORT["issue"].format(
        line=issue.line, column=issue.column, code=issue.code, message=message
    )


def render_issues(issues: tuple[FeedIssue, ...], limit: int = MAX_LISTED_ISSUES) -> list[str]:
    lines = [REPORT["errors_title"].format(count=len(issues))]
    lines.extend(render_issue(i) for i in issues[:limit])
    if len(issues) > limit:
        lines.append(REPORT["errors_more"].format(count=len(issues) - limit, shown=limit))
    return lines


def _month(text: str) -> str:
    return format_month(YearMonth.parse(text))


def render_warning(issue: FeedIssue) -> str:
    params = dict(issue.params)
    for key in ("statement", "cycle"):
        if key in params:
            params[key] = _month(str(params[key]))
    message = WARNING_MESSAGES[issue.code].format(**params)
    if issue.line is None:
        return REPORT["warning_plain"].format(message=message)
    return REPORT["warning_line"].format(line=issue.line, message=message)


def render_plan(
    analysis: FeedAnalysis, names: Mapping[str, str], title: str | None = None
) -> list[str]:
    plan = analysis.plan
    assert plan is not None
    return [
        title or REPORT["title_dry"],
        REPORT["file"].format(
            rows=analysis.data_rows,
            delimiter="TAB" if analysis.delimiter == "\t" else f"“{analysis.delimiter}”",
            sha=analysis.sha256[:16] + "…",
        ),
        *_plan_body(plan, names),
    ]


def _plan_body(plan: FeedPlan, names: Mapping[str, str]) -> list[str]:
    kinds = " · ".join(
        f"{KIND_PLURAL[k.value]} {plan.rows_by_kind[k]}" for k in FeedKind if k in plan.rows_by_kind
    )
    lines = [
        REPORT["rows"].format(kinds=kinds or REPORT["none"]),
        REPORT["created"].format(
            transactions=plan.transactions,
            purchases=plan.purchases,
            payments=plan.payments,
            balances=plan.balances,
        ),
    ]
    if plan.account_totals:
        lines.append(REPORT["accounts"])
        for total in sorted(plan.account_totals, key=lambda t: names[t.account_id].casefold()):
            lines.append(
                f"  {names[total.account_id]} · {total.count} lançamento(s) · "
                f"{format_brl(total.sum_cents)}"
            )
    if plan.statements:
        lines.append(REPORT["statements"])
        for touch in plan.statements:
            lines.append(
                REPORT["statement_line"].format(
                    account=names[touch.account_id],
                    month=format_month(touch.month),
                    closing=format_date_short(touch.closing_date),
                    due=format_date_short(touch.due_date),
                    state=REPORT["state_existing"] if touch.exists else REPORT["state_new"],
                    entries=touch.entries,
                    payments=touch.payments,
                    owed=format_brl(touch.owed_cents),
                    paid=format_brl(touch.paid_cents),
                )
            )
        reasons = " · ".join(
            f"{REASON_LABELS[r.value]} {n}" for r, n in sorted(plan.reasons.items())
        )
        if reasons:
            lines.append(REPORT["reasons"].format(reasons=reasons))
    lines.append(REPORT["warnings"] + ("" if plan.warnings else " " + REPORT["none"]))
    lines.extend(render_warning(w) for w in plan.warnings)
    return lines


# ---- applied-files ledger (data/import/csv_applied.jsonl, no schema change) -------------------


def ledger_path(import_dir: Path) -> Path:
    return import_dir / LEDGER_NAME


def find_applied(path: Path, sha256: str) -> dict[str, object] | None:
    """The ledger entry of a file already applied (same SHA-256), or ``None``."""
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("sha256") == sha256:
            return entry  # pyright: ignore[reportUnknownVariableType]
    return None


def record_applied(
    path: Path, sha256: str, when: dt.datetime, counts: Mapping[str, int], forced: bool
) -> None:
    """One JSON line: hash, timestamp, counts. Never a file name, a description or an amount."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "sha256": sha256,
        "applied_at": when.isoformat(timespec="seconds"),
        "counts": dict(counts),
        "forced": forced,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def applied_when(entry: Mapping[str, object]) -> str:
    text = str(entry.get("applied_at", ""))
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        return text
    return f"{format_date(stamp.date())} {stamp:%H:%M}"
