import pytest

from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.domain.services.installments import (
    InstallmentLine,
    build_schedule,
    split_total,
)

YM = YearMonth


@pytest.mark.parametrize(
    ("total", "count", "parts"),
    [
        (30_100, 3, [10_034, 10_033, 10_033]),  # CLAUDE.md 9.4 example
        (7_823, 10, [785] + [782] * 9),  # Nubank pattern: remainder goes to the first
        (100, 1, [100]),
        (100, 4, [25, 25, 25, 25]),
        (10, 3, [4, 3, 3]),
    ],
)
def test_split_total(total: int, count: int, parts: list[int]) -> None:
    assert split_total(total, count) == parts
    assert sum(parts) == total
    assert max(parts) - min(parts) <= count - 1


def test_split_total_rejects_totals_smaller_than_the_count() -> None:
    with pytest.raises(DomainError) as exc:
        split_total(2, 3)
    assert exc.value.code == "INSTALLMENT_AMOUNT_TOO_SMALL"
    with pytest.raises(DomainError):
        split_total(0, 1)


def test_schedule_from_total_follows_one_statement_per_month() -> None:
    lines = build_schedule(count=3, first_number=1, first_statement=YM(2026, 8), total_cents=30_100)
    assert lines == [
        InstallmentLine(1, YM(2026, 8), 10_034),
        InstallmentLine(2, YM(2026, 9), 10_033),
        InstallmentLine(3, YM(2026, 10), 10_033),
    ]


def test_schedule_from_the_installment_value_repeats_it() -> None:
    lines = build_schedule(
        count=3, first_number=1, first_statement=YM(2026, 8), installment_cents=10_034
    )
    assert [line.amount_cents for line in lines] == [10_034] * 3


def test_running_purchase_generates_only_the_remaining_installments() -> None:
    # "installment 3 of 10 on the 2026-09 statement": installments 3..10, 2026-09 to 2027-04
    lines = build_schedule(
        count=10, first_number=3, first_statement=YM(2026, 9), installment_cents=6_188
    )
    assert len(lines) == 8
    assert (lines[0].number, lines[0].statement_month) == (3, YM(2026, 9))
    assert (lines[-1].number, lines[-1].statement_month) == (10, YM(2027, 4))


def test_running_purchase_from_a_total_uses_the_original_split() -> None:
    lines = build_schedule(count=3, first_number=2, first_statement=YM(2026, 9), total_cents=30_100)
    assert [(ln.number, ln.amount_cents) for ln in lines] == [(2, 10_033), (3, 10_033)]
    first = build_schedule(count=3, first_number=1, first_statement=YM(2026, 8), total_cents=30_100)
    assert first[0].amount_cents == 10_034


def test_single_payment_is_one_line() -> None:
    lines = build_schedule(count=1, first_number=1, first_statement=YM(2026, 7), total_cents=5_000)
    assert lines == [InstallmentLine(1, YM(2026, 7), 5_000)]


def test_year_rollover_in_the_schedule() -> None:
    lines = build_schedule(count=4, first_number=1, first_statement=YM(2026, 11), total_cents=400)
    assert [str(line.statement_month) for line in lines] == [
        "2026-11",
        "2026-12",
        "2027-01",
        "2027-02",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": 3, "first_number": 0},
        {"count": 3, "first_number": 4},
    ],
)
def test_out_of_range_numbers(kwargs: dict[str, int]) -> None:
    with pytest.raises(DomainError) as exc:
        build_schedule(first_statement=YM(2026, 7), total_cents=1_000, **kwargs)
    assert exc.value.code == "INSTALLMENT_OUT_OF_RANGE"


def test_exactly_one_amount_is_required() -> None:
    for kwargs in ({}, {"total_cents": 100, "installment_cents": 50}):
        with pytest.raises(DomainError) as exc:
            build_schedule(count=2, first_number=1, first_statement=YM(2026, 7), **kwargs)  # type: ignore[arg-type]
        assert exc.value.code == "AMOUNT_REQUIRED"
    with pytest.raises(DomainError) as exc:
        build_schedule(count=2, first_number=1, first_statement=YM(2026, 7), installment_cents=0)
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"


@pytest.mark.parametrize("count", [0, -1, 121, 20_000])
def test_installment_count_is_bounded(count: int) -> None:
    with pytest.raises(DomainError) as exc:
        build_schedule(
            count=count, first_number=1, first_statement=YM(2026, 7), total_cents=10_000_000
        )
    assert exc.value.code == "INVALID_INSTALLMENT_COUNT"
    with pytest.raises(DomainError):
        split_total(10_000_000, count)


def test_the_maximum_is_accepted() -> None:
    lines = build_schedule(
        count=120, first_number=1, first_statement=YM(2026, 7), total_cents=12_000
    )
    assert len(lines) == 120 and lines[-1].statement_month == YM(2036, 6)
