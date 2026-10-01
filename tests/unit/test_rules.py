import pytest

from financas.domain.errors import DomainError
from financas.domain.models import CategoryGroup, CategoryKind, TransactionKind
from financas.domain.rules import (
    normalize_color,
    validate_category_kind,
    validate_group_kind,
    validate_sign,
    validate_transfer_accounts,
)

K = TransactionKind


@pytest.mark.parametrize(
    ("kind", "amount"),
    [
        (K.EXPENSE, -1),
        (K.INCOME, 1),
        (K.REFUND, 1),
        (K.TRANSFER, 1),
        (K.TRANSFER, -1),
    ],
)
def test_valid_signs(kind: TransactionKind, amount: int) -> None:
    validate_sign(kind, amount)


@pytest.mark.parametrize(
    ("kind", "amount"),
    [
        (K.EXPENSE, 1),
        (K.EXPENSE, 0),
        (K.INCOME, -1),
        (K.INCOME, 0),
        (K.REFUND, -1),
        (K.REFUND, 0),
        (K.TRANSFER, 0),
    ],
)
def test_invalid_signs(kind: TransactionKind, amount: int) -> None:
    with pytest.raises(DomainError) as exc:
        validate_sign(kind, amount)
    assert exc.value.code == "SIGN_KIND_MISMATCH"
    assert exc.value.params == {"kind": kind.value}


@pytest.mark.parametrize(
    ("kind", "category_kind"),
    [
        (K.EXPENSE, CategoryKind.EXPENSE),
        (K.INCOME, CategoryKind.INCOME),
        (K.TRANSFER, CategoryKind.NEUTRAL),
        (K.REFUND, CategoryKind.NEUTRAL),
    ],
)
def test_compatible_category(kind: TransactionKind, category_kind: CategoryKind) -> None:
    validate_category_kind(kind, category_kind)


@pytest.mark.parametrize(
    ("kind", "category_kind"),
    [
        (K.EXPENSE, CategoryKind.INCOME),
        (K.EXPENSE, CategoryKind.NEUTRAL),
        (K.INCOME, CategoryKind.EXPENSE),
        (K.INCOME, CategoryKind.NEUTRAL),
        (K.TRANSFER, CategoryKind.EXPENSE),
        (K.TRANSFER, CategoryKind.INCOME),
        (K.REFUND, CategoryKind.EXPENSE),
        (K.REFUND, CategoryKind.INCOME),
    ],
)
def test_incompatible_category(kind: TransactionKind, category_kind: CategoryKind) -> None:
    with pytest.raises(DomainError) as exc:
        validate_category_kind(kind, category_kind)
    assert exc.value.code == "CATEGORY_KIND_MISMATCH"
    assert exc.value.params == {"kind": kind.value, "category_kind": category_kind.value}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("#1e395f", "#1E395F"), (" #0E6151 ", "#0E6151"), (None, None), ("", None)],
)
def test_normalize_color(text: str | None, expected: str | None) -> None:
    assert normalize_color(text) == expected


@pytest.mark.parametrize("text", ["1E395F", "#1E395", "#GGGGGG", "red", "#1E395F00", "#12345"])
def test_normalize_color_rejects_invalid(text: str) -> None:
    with pytest.raises(DomainError) as exc:
        normalize_color(text)
    assert exc.value.code == "INVALID_COLOR"


def test_transfer_needs_at_least_one_tracked_account() -> None:
    with pytest.raises(DomainError) as exc:
        validate_transfer_accounts(None, None)
    assert exc.value.code == "TRANSFER_NEEDS_ACCOUNT"


def test_transfer_accounts_must_differ() -> None:
    with pytest.raises(DomainError) as exc:
        validate_transfer_accounts("a", "a")
    assert exc.value.code == "TRANSFER_SAME_ACCOUNT"


def test_transfer_with_one_or_two_accounts_is_valid() -> None:
    validate_transfer_accounts("a", None)
    validate_transfer_accounts(None, "b")
    validate_transfer_accounts("a", "b")


@pytest.mark.parametrize(
    ("group", "kind"),
    [
        (CategoryGroup.ESSENTIAL, CategoryKind.EXPENSE),
        (CategoryGroup.NON_ESSENTIAL, CategoryKind.EXPENSE),
        (CategoryGroup.CHARGES, CategoryKind.EXPENSE),
        (CategoryGroup.REVIEW, CategoryKind.EXPENSE),
        (CategoryGroup.INCOME, CategoryKind.INCOME),
        (CategoryGroup.MOVEMENT, CategoryKind.NEUTRAL),
    ],
)
def test_group_agrees_with_kind(group: CategoryGroup, kind: CategoryKind) -> None:
    validate_group_kind(group, kind)


@pytest.mark.parametrize(
    ("group", "kind"),
    [
        (CategoryGroup.ESSENTIAL, CategoryKind.INCOME),
        (CategoryGroup.INCOME, CategoryKind.EXPENSE),
        (CategoryGroup.MOVEMENT, CategoryKind.EXPENSE),
        (CategoryGroup.REVIEW, CategoryKind.NEUTRAL),
    ],
)
def test_group_disagreeing_with_kind_is_rejected(group: CategoryGroup, kind: CategoryKind) -> None:
    with pytest.raises(DomainError) as exc:
        validate_group_kind(group, kind)
    assert exc.value.code == "CATEGORY_GROUP_KIND_MISMATCH"
