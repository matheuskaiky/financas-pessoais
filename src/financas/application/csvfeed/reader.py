"""Turns the text of a CSV file into raw rows (no I/O: the adapter reads the file).

UTF-8 with or without BOM, delimiter detected from the header line (``,`` ``;`` or tab), RFC-4180
quoting, empty lines and lines starting with ``#`` ignored, first row is the header.
"""

import csv
import hashlib
import io
import re

from financas.application.csvfeed.model import (
    COLUMN_ORDER,
    MAX_DATA_ROWS,
    REQUIRED_COLUMNS,
    FeedColumn,
    FeedIssue,
    ParsedTable,
    RawRow,
)
from financas.application.csvfeed.parsing import resolve_header
from financas.domain.errors import DomainError
from financas.domain.services.text import clean_text

DELIMITERS = (";", ",", "\t")
_NEWLINE = re.compile(r"\r\n|\n|\r")


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_text(data: bytes) -> str:
    """UTF-8 (a BOM is accepted); anything else is an error, never a guess about the encoding."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DomainError("FILE_NOT_UTF8") from None
    if "\x00" in text:
        raise DomainError("FILE_NOT_UTF8")
    return text


def _records(text: str) -> list[tuple[int, str]]:
    """``(first line number, record text)``: a quoted field may span lines (even quote count)."""
    lines = _NEWLINE.split(text)
    records: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for number, line in enumerate(lines, start=1):
        if not pending:
            if not line.strip() or line.startswith("#"):
                continue
            start = number
        pending.append(line)
        if "".join(pending).count('"') % 2 == 0:
            records.append((start, "\n".join(pending)))
            pending = []
    if pending:
        records.append((start, "\n".join(pending)))  # unterminated: reported by the caller
    return records


def _split(record: str, delimiter: str) -> list[str]:
    reader = csv.reader(io.StringIO(record, newline=""), delimiter=delimiter, strict=True)
    rows = list(reader)
    if len(rows) != 1:
        raise csv.Error("record")
    return rows[0]


def _detect_delimiter(header_record: str) -> str | None:
    """The delimiter that makes the most header cells known names; ``None``: a tie."""
    best: list[str] = []
    best_score = -1
    seen: set[tuple[str, ...]] = set()
    for delimiter in DELIMITERS:
        try:
            cells = _split(header_record, delimiter)
        except csv.Error:
            continue
        score = sum(1 for c in cells if resolve_header(c) is not None)
        if score > best_score:
            best, best_score, seen = [delimiter], score, {tuple(cells)}
        elif score == best_score and tuple(cells) not in seen:
            best.append(delimiter)
            seen.add(tuple(cells))
    if best_score <= 0:
        counts = {d: header_record.count(d) for d in DELIMITERS}
        top = max(counts.values())
        return next(d for d in DELIMITERS if counts[d] == top) if top else DELIMITERS[0]
    return best[0] if len(best) == 1 else None


def parse_table(text: str, delimiter: str | None = None) -> ParsedTable:
    """Header and rows. Problems are collected as issues; a file with header issues has no rows."""
    if text.startswith("﻿"):
        text = text[1:]
    records = _records(text)
    if not records:
        return ParsedTable(delimiter or DELIMITERS[0], (), (), (FeedIssue("FILE_EMPTY"),))
    header_line, header_record = records[0]
    chosen = delimiter or _detect_delimiter(header_record)
    if chosen is None:
        return ParsedTable(DELIMITERS[0], (), (), (FeedIssue("DELIMITER_AMBIGUOUS", header_line),))
    issues: list[FeedIssue] = []
    try:
        header_cells = _split(header_record, chosen)
    except csv.Error:
        issues.append(FeedIssue("MALFORMED_ROW", header_line))
        return ParsedTable(chosen, (), (), tuple(issues))
    if header_record.count('"') % 2:
        issues.append(FeedIssue("UNTERMINATED_QUOTE", header_line))
        return ParsedTable(chosen, (), (), tuple(issues))

    columns: list[FeedColumn | None] = []
    seen: set[FeedColumn] = set()
    for cell in header_cells:
        name = clean_text(cell)
        if not name:
            columns.append(None)
            continue
        column = resolve_header(name)
        if column is None:
            issues.append(FeedIssue("UNKNOWN_COLUMN", header_line, None, {"name": name[:60]}))
            columns.append(None)
        elif column in seen:
            issues.append(FeedIssue("DUPLICATE_COLUMN", header_line, column.value))
            columns.append(None)
        else:
            seen.add(column)
            columns.append(column)
    issues.extend(
        FeedIssue("MISSING_COLUMN", header_line, c.value) for c in REQUIRED_COLUMNS if c not in seen
    )
    if issues:
        return ParsedTable(chosen, tuple(c for c in COLUMN_ORDER if c in seen), (), tuple(issues))

    rows: list[RawRow] = []
    for line, record in records[1:]:
        if len(rows) >= MAX_DATA_ROWS:
            issues.append(FeedIssue("TOO_MANY_ROWS", line, None, {"max": MAX_DATA_ROWS}))
            break
        if record.count('"') % 2:
            issues.append(FeedIssue("UNTERMINATED_QUOTE", line))
            continue
        try:
            cells = _split(record, chosen)
        except csv.Error:
            issues.append(FeedIssue("MALFORMED_ROW", line))
            continue
        if not any(c.strip() for c in cells):
            continue  # an Excel row of empty cells (";;;;")
        extra = cells[len(columns) :]
        if len(cells) < len(columns) or any(c.strip() for c in extra):
            issues.append(
                FeedIssue("ROW_WIDTH", line, None, {"found": len(cells), "expected": len(columns)})
            )
            continue
        values: dict[FeedColumn, str] = {}
        for column, cell in zip(columns, cells, strict=False):
            if column is None:
                if cell.strip():
                    issues.append(FeedIssue("UNNAMED_COLUMN", line))
                continue
            values[column] = clean_text(cell)
        rows.append(RawRow(line, values))
    return ParsedTable(
        chosen, tuple(c for c in COLUMN_ORDER if c in seen), tuple(rows), tuple(issues)
    )
