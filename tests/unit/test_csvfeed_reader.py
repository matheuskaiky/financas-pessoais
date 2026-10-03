"""File structure of the CSV feed: encoding, delimiter, quoting, comments, header problems."""

import pytest

from feed_support import TODAY
from financas.application.csvfeed import parse_feed
from financas.application.csvfeed.model import FeedColumn
from financas.application.csvfeed.reader import decode_text, parse_table
from financas.domain.errors import DomainError

H = "date;kind;account;amount;description"


def codes(text: str, delimiter: str | None = None) -> list[tuple[str, int | None, str | None]]:
    table = parse_table(text, delimiter)
    return [(i.code, i.line, i.column) for i in table.issues]


def test_semicolon_comma_and_tab_are_detected_from_the_header() -> None:
    for sep in (";", ",", "\t"):
        text = sep.join(["date", "kind", "account", "amount", "description"]) + "\n"
        text += sep.join(["2026-09-01", "expense", "Conta", "10", "Pão"]) + "\n"
        table = parse_table(text)
        assert table.delimiter == sep and not table.issues
        assert table.rows[0].get(FeedColumn.DESCRIPTION) == "Pão"


def test_delimiter_override_and_decimal_commas_survive_a_comma_header_in_quotes() -> None:
    text = 'date;kind;account;amount;description\n2026-09-01;expense;Conta;"1,50";"a, b"\n'
    table = parse_table(text)
    assert table.delimiter == ";"
    assert table.rows[0].get(FeedColumn.AMOUNT) == "1,50"
    assert table.rows[0].get(FeedColumn.DESCRIPTION) == "a, b"
    forced = parse_table(text.replace(";", ","), ",")
    assert forced.delimiter == ","


def test_bom_comments_blank_lines_and_empty_excel_rows_are_ignored() -> None:
    text = (
        "﻿# a comment\n\n" + H + "\n# another\n   \n"
        "2026-09-01;expense;Conta;10;Pão\n;;;;\n2026-09-02;expense;Conta;11;Leite\n"
    )
    table = parse_table(text)
    assert not table.issues
    assert [r.line for r in table.rows] == [6, 8]
    assert len(table.rows) == 2


def test_line_numbers_are_physical_lines_of_the_file() -> None:
    text = "# c\n" + H + "\n\n2026-09-01;expense;Conta;10;Pão\n"
    assert [r.line for r in parse_table(text).rows] == [4]


def test_quoted_field_with_newline_and_escaped_quotes() -> None:
    text = (
        H + '\n2026-09-01;expense;Conta;10;"linha 1\nlinha ""2"""\n2026-09-02;expense;Conta;5;x\n'
    )
    table = parse_table(text)
    assert not table.issues
    assert table.rows[0].get(FeedColumn.DESCRIPTION) == 'linha 1\nlinha "2"'
    assert [r.line for r in table.rows] == [2, 4]


def test_a_line_starting_with_hash_inside_quotes_is_data() -> None:
    text = H + '\n2026-09-01;expense;Conta;10;"a\n# not a comment"\n'
    assert parse_table(text).rows[0].get(FeedColumn.DESCRIPTION) == "a\n# not a comment"


def test_text_is_trimmed_and_nfc_normalised() -> None:
    decomposed = "Café São João"
    table = parse_table(f"{H}\n2026-09-01;expense;Conta;10;  {decomposed}  \n")
    assert table.rows[0].get(FeedColumn.DESCRIPTION) == "Café São João"


def test_header_aliases_in_portuguese_and_any_order() -> None:
    table = parse_table("Valor;Data;Conta;Tipo;Descrição\n10;2026-09-01;C;despesa;x\n")
    assert not table.issues
    assert table.rows[0].get(FeedColumn.AMOUNT) == "10"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (H + ";valro\n", [("UNKNOWN_COLUMN", 1, None)]),
        (H + ";amount\n", [("DUPLICATE_COLUMN", 1, "amount")]),
        ("date;kind;account;description\n", [("MISSING_COLUMN", 1, "amount")]),
        ("", [("FILE_EMPTY", None, None)]),
        ("# only comments\n\n", [("FILE_EMPTY", None, None)]),
    ],
)
def test_header_problems(text: str, expected: list[tuple[str, int | None, str | None]]) -> None:
    assert codes(text) == expected


def test_data_rows_are_not_read_when_the_header_is_wrong() -> None:
    table = parse_table(H + ";valro\n2026-09-01;expense;Conta;10;x;1\n")
    assert table.rows == ()


def test_row_problems() -> None:
    text = (
        H + "\n"
        "2026-09-01;expense;Conta;10\n"  # too few
        "2026-09-01;expense;Conta;10;x;y\n"  # too many
        '2026-09-01;expense;Conta;10;"abc\n'  # unterminated
        '2026-09-01;expense;Conta;10;"abc"def\n'  # text after the closing quote
    )
    found = codes(text)
    assert ("ROW_WIDTH", 2, None) in found and ("ROW_WIDTH", 3, None) in found
    assert any(c == "UNTERMINATED_QUOTE" for c, _, _ in found)


def test_extra_empty_trailing_cells_and_unnamed_columns() -> None:
    assert not parse_table(H + ";\n2026-09-01;expense;Conta;10;x;\n").issues
    found = codes(H + ";\n2026-09-01;expense;Conta;10;x;oops\n")
    assert found == [("UNNAMED_COLUMN", 2, None)]


def test_too_many_rows() -> None:
    body = "2026-09-01;expense;Conta;10;x\n" * 20_001
    found = codes(H + "\n" + body)
    assert found == [("TOO_MANY_ROWS", 20_002, None)]
    assert (
        len(parse_table(H + "\n" + body[: len("2026-09-01;expense;Conta;10;x\n") * 20_000]).rows)
        == 20_000
    )


def test_bytes_must_be_utf8() -> None:
    assert decode_text("﻿date".encode()) == "date"
    assert decode_text("Salário".encode("utf-8-sig")) == "Salário"
    for bad in ("Salário".encode("latin-1"), b"a\x00b"):
        with pytest.raises(DomainError) as error:
            decode_text(bad)
        assert error.value.code == "FILE_NOT_UTF8"
    parsed = parse_feed("Salário".encode("latin-1"), TODAY)
    assert [i.code for i in parsed.issues] == ["FILE_NOT_UTF8"]


def test_garbage_bytes_never_crash_the_reader() -> None:
    import random

    rng = random.Random(3)
    pieces = [";", ",", "\t", '"', "\n", "\r\n", "#", "date", "kind", "amount", "ã", "1,5", " "]
    for _ in range(1500):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 30)))
        parse_feed(text.encode(), TODAY)
    for _ in range(300):
        parse_feed(bytes(rng.randrange(256) for _ in range(rng.randint(0, 60))), TODAY)
