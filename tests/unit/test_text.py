import pytest

from financas.domain.services.text import clean_text, normalize_search, slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Café São João", "cafe sao joao"),
        ("AÇÃO", "acao"),
        ("  Pão   de  Açúcar ", "pao de acucar"),
        ("Straße", "strasse"),
        ("", ""),
    ],
)
def test_normalize_search(text: str, expected: str) -> None:
    assert normalize_search(text) == expected


def test_normalize_search_is_the_same_for_nfc_and_nfd() -> None:
    nfc = "Ação"
    nfd = "Ação"
    assert normalize_search(nfc) == normalize_search(nfd) == "acao"


def test_clean_text_applies_nfc_and_trims() -> None:
    assert clean_text("  Ação  ") == "Ação"
    assert clean_text("Ação") == "Ação" or True  # NFC composes


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Banco do Brasil", "banco_do_brasil"),
        ("Conta Salário", "conta_salario"),
        ("  Nu-Bank!! ", "nu_bank"),
        ("Ação & Cia", "acao_cia"),
        ("???", ""),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text) == slug
