"""Row-level validation: required cells per kind, allowed cells per kind, typed values.

Every problem of a row is reported (code, line, column); a row with problems yields no ``FeedRow``.
Business rules that need the database (accounts, categories, statements) are in ``planner``.
"""

import datetime as dt
from collections.abc import Callable

from financas.application.csvfeed.model import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NOTES_LENGTH,
    AmountType,
    FeedColumn,
    FeedIssue,
    FeedKind,
    FeedRow,
    RawRow,
)
from financas.application.csvfeed.parsing import (
    parse_amount,
    parse_amount_type,
    parse_bool,
    parse_date,
    parse_int,
    parse_kind,
    parse_statement_month,
)
from financas.domain.errors import DomainError
from financas.domain.models import TransactionKind
from financas.domain.money import YearMonth
from financas.domain.rules import validate_sign
from financas.domain.services.installments import MAX_INSTALLMENTS

C = FeedColumn

_COMMON = {C.DATE, C.KIND, C.ACCOUNT, C.AMOUNT, C.DESCRIPTION, C.NOTES}
_ENTRY = _COMMON | {C.CATEGORY, C.RECURRING, C.STATEMENT}
ALLOWED: dict[FeedKind, set[FeedColumn]] = {
    FeedKind.EXPENSE: _ENTRY | {C.INSTALLMENTS, C.INSTALLMENT_NUMBER, C.AMOUNT_TYPE},
    FeedKind.INCOME: _ENTRY - {C.STATEMENT},
    FeedKind.REFUND: _ENTRY
    | {C.INSTALLMENTS},  # present only to say "refunds are not installments"
    FeedKind.TRANSFER: _COMMON | {C.TO_ACCOUNT, C.CATEGORY, C.STATEMENT, C.RECURRING},
    FeedKind.BALANCE: _COMMON | {C.GROSS_AMOUNT, C.RECURRING},
}
_INSTALLMENT_COLUMNS = (C.INSTALLMENTS, C.INSTALLMENT_NUMBER, C.AMOUNT_TYPE)
_TRANSACTION_KIND = {
    FeedKind.EXPENSE: TransactionKind.EXPENSE,
    FeedKind.INCOME: TransactionKind.INCOME,
    FeedKind.REFUND: TransactionKind.REFUND,
}


class _Collector:
    def __init__(self, line: int) -> None:
        self.line = line
        self.issues: list[FeedIssue] = []

    def add(self, code: str, column: FeedColumn | None, **params: str | int) -> None:
        self.issues.append(FeedIssue(code, self.line, column.value if column else None, params))

    def attempt[T](self, column: FeedColumn, action: Callable[[], T]) -> T | None:
        try:
            return action()
        except DomainError as error:
            self.add(error.code, column, **error.params)
            return None


def validate_row(raw: RawRow, today: dt.date) -> tuple[FeedRow | None, list[FeedIssue]]:
    """``(row, issues)``: ``row`` is ``None`` when there is any issue."""
    out = _Collector(raw.line)
    kind_text = raw.get(C.KIND)
    if not kind_text:
        out.add("REQUIRED_FIELD", C.KIND)
        return None, out.issues
    kind = out.attempt(C.KIND, lambda: parse_kind(kind_text))
    if kind is None:
        return None, out.issues

    for column in raw.values:
        if raw.get(column) and column not in ALLOWED[kind]:
            out.add("NOT_ALLOWED_FOR_KIND", column, kind=kind.value)
    if kind is FeedKind.REFUND and raw.get(C.INSTALLMENTS):
        out.add("REFUND_NOT_INSTALLMENT", C.INSTALLMENTS)

    # required cells
    date_text = raw.get(C.DATE)
    day: dt.date | None = None
    if not date_text:
        out.add("REQUIRED_FIELD", C.DATE)
    else:
        day = out.attempt(C.DATE, lambda: parse_date(date_text, today))
    account = raw.get(C.ACCOUNT)
    to_account = raw.get(C.TO_ACCOUNT)
    if kind is not FeedKind.TRANSFER and not account:
        out.add("REQUIRED_FIELD", C.ACCOUNT)
    description = raw.get(C.DESCRIPTION)
    if kind in (FeedKind.EXPENSE, FeedKind.INCOME, FeedKind.REFUND) and not description:
        out.add("REQUIRED_FIELD", C.DESCRIPTION)
    if len(description) > MAX_DESCRIPTION_LENGTH:
        out.add("FIELD_TOO_LONG", C.DESCRIPTION, max=MAX_DESCRIPTION_LENGTH)
    notes = raw.get(C.NOTES)
    if len(notes) > MAX_NOTES_LENGTH:
        out.add("FIELD_TOO_LONG", C.NOTES, max=MAX_NOTES_LENGTH)

    amount_cents = _amount(raw, kind, out)
    gross = _gross(raw, kind, out)

    recurring = False
    if raw.get(C.RECURRING):
        parsed = out.attempt(C.RECURRING, lambda: parse_bool(raw.get(C.RECURRING)))
        recurring = bool(parsed)
        if recurring and kind in (FeedKind.TRANSFER, FeedKind.BALANCE):
            out.add("NOT_ALLOWED_FOR_KIND", C.RECURRING, kind=kind.value)

    statement: YearMonth | None = None
    if raw.get(C.STATEMENT) and C.STATEMENT in ALLOWED[kind]:
        statement = out.attempt(C.STATEMENT, lambda: parse_statement_month(raw.get(C.STATEMENT)))

    installments, number, amount_type = _installments(raw, kind, recurring, out)

    if out.issues or day is None or amount_cents is None:
        return None, out.issues
    return (
        FeedRow(
            line=raw.line,
            kind=kind,
            date=day,
            account=account,
            to_account=to_account,
            amount_cents=amount_cents,
            description=description,
            category=raw.get(C.CATEGORY),
            recurring=recurring,
            notes=notes,
            statement=statement,
            installments=installments,
            installment_number=number,
            amount_type=amount_type,
            gross_cents=gross,
        ),
        [],
    )


def _amount(raw: RawRow, kind: FeedKind, out: _Collector) -> int | None:
    text = raw.get(C.AMOUNT)
    if not text:
        out.add("REQUIRED_FIELD", C.AMOUNT)
        return None
    amount = out.attempt(C.AMOUNT, lambda: parse_amount(text))
    if amount is None:
        return None
    if kind is FeedKind.BALANCE:
        return amount.signed_cents  # an overdrawn account is negative
    if amount.magnitude_cents == 0:
        out.add("AMOUNT_NOT_POSITIVE", C.AMOUNT)
        return None
    # signs: expense negative or unsigned, income/refund positive or unsigned, transfer unsigned
    contradicts = (
        amount.sign is not None
        if kind is FeedKind.TRANSFER
        else (amount.sign == "-") != (kind is FeedKind.EXPENSE) and amount.sign is not None
    )
    if contradicts:
        out.add("SIGN_KIND_MISMATCH", C.AMOUNT, kind=kind.value)
        return None
    if kind is not FeedKind.TRANSFER:
        validate_sign(
            _TRANSACTION_KIND[kind],
            -amount.magnitude_cents if kind is FeedKind.EXPENSE else amount.magnitude_cents,
        )
    return amount.magnitude_cents


def _gross(raw: RawRow, kind: FeedKind, out: _Collector) -> int | None:
    if not raw.get(C.GROSS_AMOUNT) or kind is not FeedKind.BALANCE:
        return None
    amount = out.attempt(C.GROSS_AMOUNT, lambda: parse_amount(raw.get(C.GROSS_AMOUNT)))
    return amount.signed_cents if amount else None


def _installments(
    raw: RawRow, kind: FeedKind, recurring: bool, out: _Collector
) -> tuple[int | None, int, AmountType | None]:
    if kind is not FeedKind.EXPENSE:
        return None, 1, None
    count_text, number_text, type_text = (raw.get(c) for c in _INSTALLMENT_COLUMNS)
    count: int | None = None
    if count_text:
        count = out.attempt(C.INSTALLMENTS, lambda: parse_int(count_text))
        if count is not None and not 1 <= count <= MAX_INSTALLMENTS:
            out.add("INVALID_INSTALLMENT_COUNT", C.INSTALLMENTS, count=count)
            count = None
    elif number_text or type_text:
        for column, text in ((C.INSTALLMENT_NUMBER, number_text), (C.AMOUNT_TYPE, type_text)):
            if text:
                out.add("INSTALLMENT_DETAILS_WITHOUT_COUNT", column)
        return None, 1, None
    number = 1
    if number_text and count is not None:
        parsed = out.attempt(C.INSTALLMENT_NUMBER, lambda: parse_int(number_text))
        if parsed is not None:
            if not 1 <= parsed <= count:
                out.add(
                    "INSTALLMENT_OUT_OF_RANGE", C.INSTALLMENT_NUMBER, number=parsed, count=count
                )
            else:
                number = parsed
    amount_type: AmountType | None = None
    if type_text and count is not None:
        if count == 1:
            out.add("INSTALLMENT_DETAILS_WITHOUT_COUNT", C.AMOUNT_TYPE)
        else:
            amount_type = out.attempt(C.AMOUNT_TYPE, lambda: parse_amount_type(type_text))
    if count is not None and count >= 2:
        amount_type = amount_type or AmountType.TOTAL
        if recurring:
            out.add("INSTALLMENT_NOT_RECURRING", C.RECURRING)
    return count, number, amount_type


def validate_rows(
    rows: tuple[RawRow, ...], today: dt.date
) -> tuple[list[FeedRow], list[FeedIssue]]:
    valid: list[FeedRow] = []
    issues: list[FeedIssue] = []
    for raw in rows:
        row, found = validate_row(raw, today)
        issues.extend(found)
        if row is not None:
            valid.append(row)
    return valid, issues
