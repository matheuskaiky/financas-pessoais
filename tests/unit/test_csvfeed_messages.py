"""Every code the CSV feed can emit has a pt-BR message, and every message renders."""

import re
import string
from pathlib import Path

from financas.application.csvfeed.model import FeedIssue
from financas.interfaces import csvfeed as report
from financas.interfaces.messages import ERROR_MESSAGES
from financas.interfaces.messages.csvfeed import (
    CHECK_LABELS,
    CSV_ERROR_MESSAGES,
    FEED_OVERRIDES,
    WARNING_MESSAGES,
)

ROOT = Path(__file__).parents[2] / "src" / "financas" / "application" / "csvfeed"


def emitted_codes() -> set[str]:
    codes: set[str] = set()
    for path in ROOT.glob("*.py"):
        codes |= set(re.findall(r'"([A-Z][A-Z_]{3,})"', path.read_text(encoding="utf-8")))
    return codes


def test_every_emitted_code_has_a_message() -> None:
    codes = emitted_codes()
    assert {"UNKNOWN_ACCOUNT", "AMOUNT_AMBIGUOUS", "DUPLICATE_ROWS"} <= codes
    known = set(ERROR_MESSAGES) | set(FEED_OVERRIDES) | set(WARNING_MESSAGES) | set(CHECK_LABELS)
    assert codes - known == set()
    assert set(WARNING_MESSAGES) <= codes and set(CHECK_LABELS) <= codes  # no dead messages


def test_feed_codes_are_in_the_shared_catalogue() -> None:
    assert set(CSV_ERROR_MESSAGES) <= set(ERROR_MESSAGES)


BASE = {
    "kind_label": ("kind", "expense"),
    "category_kind_label": ("category_kind", "income"),
    "account_kind_label": ("account_kind", "checking"),
    "group_label": ("group", "essential"),
    "entity_label": ("entity", "account"),
    "max_kb": ("max_bytes", 524288),
}


def test_every_message_renders_with_its_parameters() -> None:
    for code, template in {**ERROR_MESSAGES, **FEED_OVERRIDES}.items():
        fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        raw: dict[str, str | int] = {}
        for field in fields:
            key, value = BASE.get(field, (field, "1"))
            raw[key] = value
        text = report.issue_message(FeedIssue(code, 3, "amount", raw))
        assert text and "{" not in text, code


def test_issue_lines_carry_code_line_and_column() -> None:
    line = report.render_issue(FeedIssue("INVALID_AMOUNT", 12, "amount"))
    assert line.startswith("  linha 12 · coluna amount: [INVALID_AMOUNT] ")
    assert report.render_issue(FeedIssue("FILE_EMPTY")).startswith("  arquivo: [FILE_EMPTY]")
    assert "Parecidas: Conta Corrente" in report.render_issue(
        FeedIssue("UNKNOWN_ACCOUNT", 2, "account", {"suggestions": "Conta Corrente"})
    )


def test_warnings_render() -> None:
    for code, template in WARNING_MESSAGES.items():
        fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        params: dict[str, str | int] = {
            f: ("2026-09" if f in ("statement", "cycle") else "x") for f in fields
        }
        assert report.render_warning(FeedIssue(code, 2, None, params))


def test_issue_list_is_capped() -> None:
    issues = tuple(FeedIssue("INVALID_AMOUNT", n, "amount") for n in range(1, 251))
    lines = report.render_issues(issues)
    assert len(lines) == 1 + 200 + 1 and "250" in lines[0] and "50" in lines[-1]
