"""CSV feed (CLAUDE.md 13.3): entries typed by the user in the project's own CSV format.

Two steps, both pure (the adapter reads the file and loads the context):

1. ``parse_feed(data, today)``: bytes -> raw rows -> typed rows, every syntax problem collected
   with its line and column (no database needed);
2. ``plan_feed(parsed, context)``: business rules against the accounts, categories and statements
   that exist, producing a ``FeedPlan`` (or the issues).

``ApplyFeed`` and ``verify_feed`` (``apply``) run a plan through the existing use cases.
"""

import datetime as dt
from dataclasses import dataclass

from financas.application.csvfeed.apply import (
    ApplyFeed,
    BalanceOutcome,
    FeedApplyResult,
    Snapshot,
    snapshot,
    verify_feed,
)
from financas.application.csvfeed.context import load_context
from financas.application.csvfeed.model import (
    FeedAnalysis,
    FeedContext,
    FeedIssue,
    FeedPlan,
    FeedRow,
)
from financas.application.csvfeed.planner import build_plan
from financas.application.csvfeed.reader import decode_text, file_sha256, parse_table
from financas.application.csvfeed.template import template_text
from financas.application.csvfeed.validate import validate_rows
from financas.domain.errors import DomainError

__all__ = [
    "ApplyFeed",
    "BalanceOutcome",
    "FeedAnalysis",
    "FeedApplyResult",
    "FeedContext",
    "FeedIssue",
    "FeedPlan",
    "ParsedFeed",
    "Snapshot",
    "analyze_feed",
    "load_context",
    "parse_feed",
    "plan_feed",
    "snapshot",
    "template_text",
    "verify_feed",
]


@dataclass(frozen=True)
class ParsedFeed:
    delimiter: str
    sha256: str
    data_rows: int
    rows: tuple[FeedRow, ...]
    issues: tuple[FeedIssue, ...]


def parse_feed(data: bytes, today: dt.date, delimiter: str | None = None) -> ParsedFeed:
    """Bytes of the file -> typed rows and issues (syntax only; sorted by line)."""
    sha = file_sha256(data)
    try:
        text = decode_text(data)
    except DomainError as error:
        return ParsedFeed(delimiter or ";", sha, 0, (), (FeedIssue(error.code),))
    table = parse_table(text, delimiter)
    rows, issues = validate_rows(table.rows, today)
    every = sorted([*table.issues, *issues], key=lambda i: i.line or 0)
    return ParsedFeed(table.delimiter, sha, len(table.rows), tuple(rows), tuple(every))


def plan_feed(parsed: ParsedFeed, context: FeedContext) -> FeedAnalysis:
    """Business rules against the database state. With syntax issues there is no plan."""
    if parsed.issues:
        return FeedAnalysis(parsed.delimiter, parsed.sha256, parsed.data_rows, parsed.issues, None)
    plan, issues = build_plan(list(parsed.rows), context)
    return FeedAnalysis(parsed.delimiter, parsed.sha256, parsed.data_rows, tuple(issues), plan)


def analyze_feed(data: bytes, context: FeedContext, delimiter: str | None = None) -> FeedAnalysis:
    return plan_feed(parse_feed(data, context.today, delimiter), context)
