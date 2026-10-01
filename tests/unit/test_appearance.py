import pytest

from financas.domain.models import Account, AccountKind, Institution
from financas.interfaces.appearance import (
    DEFAULT_PALETTE,
    account_look,
    contrast_ratio,
    default_color,
    initials,
    institution_look,
    readable_text_color,
)


@pytest.mark.parametrize(
    ("background", "text"),
    [
        ("#FFFFFF", "#000000"),
        ("#000000", "#FFFFFF"),
        ("#1E395F", "#FFFFFF"),
        ("#FCFC30", "#000000"),
    ],
)
def test_text_color_has_the_better_contrast(background: str, text: str) -> None:
    assert readable_text_color(background) == text


def test_every_default_color_is_readable_with_white_text() -> None:
    for color in DEFAULT_PALETTE:
        assert contrast_ratio(color, "#FFFFFF") >= 4.5, color


def test_default_color_is_stable() -> None:
    assert default_color("banco_do_brasil") == default_color("banco_do_brasil")
    assert default_color("x") in DEFAULT_PALETTE


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Banco do Brasil", "BD"),
        ("Nubank", "N"),
        ("caixa econômica", "CE"),
        ("  ", ""),
    ],
)
def test_initials(name: str, expected: str) -> None:
    assert initials(name) == expected


def inst(**kw: object) -> Institution:
    return Institution("i" * 32, "bb", "Banco do Brasil", **kw)  # type: ignore[arg-type]


def acc(**kw: object) -> Account:
    return Account("a" * 32, AccountKind.CHECKING, "i" * 32, "Conta", **kw)  # type: ignore[arg-type]


def test_institution_uses_its_color_or_a_default() -> None:
    assert institution_look(inst(color="#FCFC30")).color == "#FCFC30"
    assert institution_look(inst()).color == default_color("bb")


def test_account_inherits_from_its_institution() -> None:
    parent = inst(color="#FCFC30", image_id="f" * 32)
    look = account_look(acc(), parent)
    assert (look.color, look.image_id) == ("#FCFC30", "f" * 32)


def test_account_own_color_and_image_win_independently() -> None:
    parent = inst(color="#FCFC30", image_id="f" * 32)
    only_color = account_look(acc(color="#0E6151"), parent)
    assert (only_color.color, only_color.image_id) == ("#0E6151", "f" * 32)
    only_image = account_look(acc(image_id="e" * 32), parent)
    assert (only_image.color, only_image.image_id) == ("#FCFC30", "e" * 32)
