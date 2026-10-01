"""The CLI end to end, on a temporary database (synthetic data only)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from financas.interfaces.cli import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINANCAS_DB_URL", f"sqlite:///{tmp_path / 'data' / 'f.db'}")
    monkeypatch.setenv("FINANCAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COLUMNS", "220")


def run(*args: str, ok: bool = True) -> str:
    result = runner.invoke(app, list(args), terminal_width=200)
    assert (result.exit_code == 0) is ok, result.output
    return result.output


def setup_basic() -> None:
    run("init")
    run("institution", "add", "Banco do Brasil", "--color", "#fcfc30")
    run("account", "add", "Conta Corrente", "-i", "banco do brasil")
    run("account", "add", "Caixinha", "-i", "banco", "-k", "investment")


def test_full_flow() -> None:
    setup_basic()
    assert "Saúde" in run("category", "list")
    run("balance", "set", "corrente", "1.000,00", "-d", "01/07/2026")
    out = run(
        "add", "45,90", "Padaria São João", "-c", "food", "-d", "10/07/2026", "-a", "corrente"
    )
    assert "-R$ 45,90" in out
    out = run("add", "5.000,00", "Salário", "-k", "income", "-c", "salary", "-d", "05/07/2026")
    assert "R$ 5.000,00" in out
    out = run("transfer", "500,00", "--from", "corrente", "--to", "caixinha", "-d", "06/07/2026")
    assert "2 perna(s)" in out
    # the next entry with the same description gets the suggested category
    assert "Categoria sugerida: Alimentação" in run(
        "add", "10", "padaria sao joao", "-d", "11/07/2026"
    )
    listing = run("list", "-m", "2026-07")
    assert "Padaria São João" in listing and "julho de 2026" in listing
    summary = run("summary", "-m", "2026-07")
    assert "R$ 5.000,00" in summary and "R$ 55,90" in summary and "Alimentação" in summary
    accounts = run("account", "list")
    assert "Conta Corrente" in accounts and "saldo indisponível" in accounts  # the savings has none


def test_errors_are_shown_in_portuguese_with_exit_code_1() -> None:
    setup_basic()
    assert "Valor inválido" in run("add", "abc", "x", ok=False)
    assert "Cor inválida" in run("institution", "add", "Nu", "--color", "roxo", ok=False)
    assert "Data inválida" in run("add", "1", "x", "-d", "31/02/2026", ok=False)
    assert "não encontrado" in run("add", "1", "x", "-a", "inexistente", ok=False)
    assert "não combina" in run("add", "1", "x", "-k", "income", "-c", "food", ok=False)
    assert "Informe ao menos uma conta" in run("transfer", "10", ok=False)


def test_style_sets_color_and_image(tmp_path: Path) -> None:
    setup_basic()
    image = tmp_path / "logo.png"
    image.write_bytes(PNG)
    run("style", "institution", "banco", "--image", str(image), "--color", "#1e395f")
    assert "#1E395F" in run("institution", "list")
    assert len(list((tmp_path / "data" / "images").iterdir())) == 1
    run("style", "institution", "banco", "--remove-image")
    assert list((tmp_path / "data" / "images").iterdir()) == []
    bad = tmp_path / "logo.svg"
    bad.write_text("<svg/>", encoding="utf-8")
    assert "Formato de imagem" in run(
        "style", "institution", "banco", "--image", str(bad), ok=False
    )


def test_delete_and_backup(tmp_path: Path) -> None:
    setup_basic()
    out = run("add", "10", "Teste", "-d", "10/07/2026")
    code = out.split("Código:")[1].strip().rstrip(".")
    assert "apagados: 1" in run("delete", code)
    assert "não encontrado" in run("delete", "zzzzzzzz", ok=False)
    assert "Backup criado" in run("backup")
    assert len(list((tmp_path / "data" / "backups").iterdir())) == 1
