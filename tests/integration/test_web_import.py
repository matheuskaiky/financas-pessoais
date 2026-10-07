"""``/importar``: the web page that feeds the database from a user-prepared file (CLAUDE.md 13.3).

Synthetic data only. Each test builds a scratch database with the example accounts.
"""

import hashlib
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from feed_support import scratch_container
from financas.application.csvfeed import service as feed_service
from financas.container import Container
from financas.domain.models import AccountKind
from financas.infrastructure.settings import Settings
from financas.interfaces.web.app import create_app
from financas.interfaces.web.pending import PendingFiles
from financas.interfaces.web.routes import importar
from html_text import visible

HEADERS = {"host": "localhost", "origin": "http://localhost"}
EXAMPLE = Path(__file__).parents[2] / "scripts" / "examples" / "entries_example.csv"
SIMPLE = (
    "date;kind;account;to_account;amount;description\n"
    "2026-09-01;expense;Conta Corrente;;-10,00;Pão\n"
    "2026-09-02;transfer;Conta Corrente;Conta Reserva;3,00;\n"
)
APPLY_BUTTON = "Aplicar importação"


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return scratch_container(tmp_path)


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(
        create_app(container), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )


def pending_dir(c: Container) -> Path:
    return c.import_dir / "pending"


def pending_files(c: Container) -> list[Path]:
    folder = pending_dir(c)
    return sorted(folder.iterdir()) if folder.is_dir() else []


def upload(client: TestClient, data: bytes, name: str = "movimentos.csv") -> str:
    response = client.post("/importar/analisar", files={"file": (name, data, "text/csv")})
    assert response.status_code == 200, response.text
    return response.text


def token_of(html: str) -> str:
    match = re.search(r'name="token" value="([0-9a-f]{64})"', html)
    assert match is not None, html
    return match.group(1)


def row_count(c: Container) -> int:
    with c.uow as work:
        return sum(len(work.transactions.list_by_account(a.id)) for a in work.accounts.list_all())


def balance_of(c: Container, nickname: str) -> int | None:
    with c.uow as work:
        account = next(a for a in work.accounts.list_all() if a.nickname == nickname)
        anchors = work.anchors.list_for_account(account.id)
    return anchors[-1].balance_cents if anchors else None


def db_digest(c: Container) -> str:
    path = Path(c.settings.db_url.removeprefix("sqlite:///"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the page and the navigation ---------------------------------------------------------------


def test_page_has_the_friendly_header_and_a_real_file_input(client: TestClient) -> None:
    response = client.get("/importar")
    assert response.status_code == 200
    page = response.text
    assert "<h1>Importar lançamentos</h1>" in page
    assert (
        "Envie um arquivo com suas movimentações para registrar no sistema com segurança." in page
    )
    head = page[page.index('<header class="page-head">') : page.index("</header>")]
    assert "CSV" not in head and "valores separados" not in head
    buttons = re.findall(r"<button\b.*?</button>", page[page.index("<main>") :], flags=re.S)
    assert all("CSV" not in b for b in buttons)
    assert "Escolher arquivo" in page and "ou solte aqui" in page
    # keyboard friendly: a <label for> wrapping the real input
    assert re.search(r'<label class="imp-drop" for="imp-file"', page)
    assert 'id="imp-file" name="file" type="file"' in page
    assert 'aria-live="polite"' in page
    assert "/static/importar.js" in page and "onclick=" not in page
    assert client.get("/static/importar.js").status_code == 200
    assert client.get("/static/importar.css").status_code == 200


def test_navigation_shows_the_entry_in_the_sidebar_and_in_more(client: TestClient) -> None:
    home = client.get("/").text
    sidebar = home[home.index('<nav class="sidebar ink"') : home.index("</nav>")]
    assert 'href="/importar"' in sidebar and "Importar dados" in sidebar
    more = client.get("/more").text
    assert 'href="/importar"' in more and "Importar dados" in more
    current = client.get("/importar").text
    assert re.search(r'class="item" href="/importar"\s+aria-current="page"', current)


def test_template_download_is_the_documented_header(client: TestClient) -> None:
    response = client.get("/importar/modelo")
    assert response.status_code == 200
    assert response.text.startswith("date;kind;account;to_account;amount;")
    assert "attachment" in response.headers["content-disposition"]


# --- analysis: a dry run -----------------------------------------------------------------------


def test_a_valid_file_is_analyzed_with_counts_and_writes_nothing(
    client: TestClient, container: Container
) -> None:
    before = (row_count(container), db_digest(container))
    html = upload(client, EXAMPLE.read_bytes())
    text = visible(html)
    assert "Arquivo conferido" in text and APPLY_BUTTON in html
    for label in (
        "Receitas",
        "Despesas",
        "Transferências",
        "Parcelamentos",
        "Pagamentos de fatura",
        "Estornos",
        "Saldos informados",
    ):
        assert label in html, label
    pairs = re.findall(r'imp-n">(\d+)</span><span class="imp-l">([^<]+)<', html)
    counts = {label: n for n, label in pairs}
    assert counts == {
        "Receitas": "1",
        "Despesas": "4",
        "Transferências": "2",
        "Parcelamentos": "2",
        "Pagamentos de fatura": "1",
        "Estornos": "2",
        "Saldos informados": "2",
    }
    assert "<strong>23</strong> lançamento(s)" in html
    assert "Conta Corrente" in html and "R$" in text
    for description in ("Aluguel", "Fone de ouvido", "Livraria", "Salário setembro"):
        assert description not in html  # counts and totals only, never descriptions
    assert (row_count(container), db_digest(container)) == before  # nothing in the database
    assert not (container.import_dir / "csv_applied.jsonl").exists()
    assert [p.name for p in pending_files(container)] == [f"{token_of(html)}.csv"]


def test_an_invalid_file_lists_numbered_problems_and_has_no_apply_button(
    client: TestClient, container: Container
) -> None:
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    bad = "\n".join(
        [*lines[:4], "2026-09-02;expense;Conta Corrente;;1.234;Ambíguo;;;;;;;;", *lines[4:]]
    )
    html = upload(client, bad.encode())
    assert "Linha 5, coluna “amount”" in html and "Valor ambíguo" in html
    assert "problema(s)" in html and APPLY_BUTTON not in html and 'name="token"' not in html
    assert "[AMOUNT_AMBIGUOUS]" not in html and "--" not in visible(html).split("<main")[0]
    assert pending_files(container) == []  # an invalid file is never stored
    # business problem (unknown account) found after the syntax is clean
    unknown = SIMPLE.replace("Conta Reserva", "Conta Reservaa")
    html = upload(client, unknown.encode())
    assert "Linha 3, coluna “to_account”" in html and "Parecidas: Conta Reserva" in html
    assert APPLY_BUTTON not in html and "Pão" not in html


def test_at_most_twenty_problems_are_listed(client: TestClient) -> None:
    rows = "".join(f"2026-09-01;expense;Conta Corrente;;abc{i};x\n" for i in range(30))
    html = upload(client, ("date;kind;account;to_account;amount;description\n" + rows).encode())
    assert html.count("Linha ") == 20 and "e mais 10 problema(s)" in html
    assert "30 problema(s)" in html


@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (b"", "vazio.csv", "vazio"),
        ("date;kind\nSalário".encode("latin-1"), "latin.csv", "UTF-8"),
        (b"date;kind;account;amount\n", "so-cabecalho.csv", "só o cabeçalho"),
        (SIMPLE.encode(), "dados.xlsx", "terminar em .csv"),
    ],
)
def test_unusable_files_are_rejected_before_anything_is_stored(
    client: TestClient, container: Container, data: bytes, name: str, expected: str
) -> None:
    html = upload(client, data, name)
    assert expected in html and APPLY_BUTTON not in html
    assert pending_files(container) == []


def test_an_oversize_file_is_rejected_with_a_friendly_message(
    client: TestClient, container: Container
) -> None:
    big = SIMPLE.encode() + b"#" * (importar.MAX_UPLOAD_BYTES + 10)
    html = upload(client, big)
    assert "grande demais" in html and "5 MB" in html and APPLY_BUTTON not in html
    assert pending_files(container) == []
    missing = client.post("/importar/analisar", data={})
    assert "Escolha um arquivo" in missing.text and missing.status_code == 200


def test_bom_and_tab_delimiter_are_accepted(client: TestClient) -> None:
    text = SIMPLE.replace(";", "\t")
    html = upload(client, b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert "Arquivo conferido" in html


# --- apply -------------------------------------------------------------------------------------


def test_apply_writes_everything_makes_a_backup_and_refuses_the_same_file_twice(
    client: TestClient, container: Container
) -> None:
    html = upload(client, EXAMPLE.read_bytes())
    token = token_of(html)
    assert balance_of(container, "Conta Corrente") is None
    response = client.post("/importar/aplicar", data={"token": token})
    assert response.status_code == 200
    done = response.text
    assert "Importação concluída" in done and "23 lançamento(s) registrados" in done
    assert "backup" in done and "Aluguel" not in done
    assert row_count(container) == 23
    assert balance_of(container, "Conta Corrente") == 345678  # the informed balance is recorded
    assert any(container.settings.backups_dir.iterdir())
    assert "Saldo informado de Conta Corrente" in visible(done)
    assert pending_files(container) == []  # the uploaded bytes are deleted after the apply
    assert not (container.import_dir / "csv_work.db").exists()
    ledger = (container.import_dir / "csv_applied.jsonl").read_text(encoding="utf-8")
    assert hashlib.sha256(EXAMPLE.read_bytes()).hexdigest() in ledger
    before = (row_count(container), db_digest(container))
    # the same file again: refused at the analysis, and at the apply even with a stored copy
    again = upload(client, EXAMPLE.read_bytes())
    assert "já foi importado" in again and APPLY_BUTTON not in again
    PendingFiles(pending_dir(container)).save(EXAMPLE.read_bytes())
    second = client.post("/importar/aplicar", data={"token": token})
    assert "já foi importado" in second.text and "Importação concluída" not in second.text
    assert (row_count(container), db_digest(container)) == before
    assert pending_files(container) == []


def test_apply_registers_the_right_totals(client: TestClient, container: Container) -> None:
    token = token_of(upload(client, SIMPLE.encode()))
    assert "1 lançamento" not in client.post("/importar/aplicar", data={"token": token}).text
    with container.uow as work:
        accounts = {a.nickname: a for a in work.accounts.list_all()}
        checking = work.transactions.list_by_account(accounts["Conta Corrente"].id)
        reserve = work.transactions.list_by_account(accounts["Conta Reserva"].id)
    assert sorted(t.amount_cents for t in checking) == [-1000, -300]
    assert [t.amount_cents for t in reserve] == [300]
    assert accounts["Conta Corrente"].kind is AccountKind.CHECKING


def test_a_failure_in_the_middle_rolls_everything_back(
    client: TestClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = token_of(upload(client, EXAMPLE.read_bytes()))
    real = feed_service.ApplyFeed

    class Exploding(real):  # type: ignore[valid-type,misc]
        def _payment(self, action: object, description: str) -> None:
            raise RuntimeError("boom with Aluguel inside")

    monkeypatch.setattr(feed_service, "ApplyFeed", Exploding)
    before = (row_count(container), db_digest(container))
    response = client.post("/importar/aplicar", data={"token": token})
    assert response.status_code == 200
    assert "Nada foi alterado" in response.text and "código" in response.text
    assert "boom" not in response.text and "Aluguel" not in response.text
    assert (row_count(container), db_digest(container)) == before
    assert not (container.import_dir / "csv_work.db").exists()
    assert not (container.import_dir / "csv_applied.jsonl").exists()
    assert len(pending_files(container)) == 1  # kept: the person can try again
    log = container.failures.path.read_text(encoding="utf-8")
    assert "RuntimeError" in log and "Aluguel" not in log and "boom" not in log
    monkeypatch.undo()
    retry = client.post("/importar/aplicar", data={"token": token})
    assert "Importação concluída" in retry.text


def test_a_failed_verification_leaves_the_database_untouched(
    client: TestClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    from financas.application.imports.apply import Verification

    def failing(*_: object) -> Verification:
        result = Verification()
        result.add("SUM_BY_ACCOUNT", False, "x")
        return result

    token = token_of(upload(client, SIMPLE.encode()))
    monkeypatch.setattr(feed_service, "verify_feed", failing)
    before = db_digest(container)
    response = client.post("/importar/aplicar", data={"token": token})
    assert "conferências automáticas" in response.text and "Nada foi alterado" in response.text
    assert db_digest(container) == before
    assert not (container.import_dir / "csv_applied.jsonl").exists()


@pytest.mark.parametrize("token", ["", "abc", "../../etc/passwd", "G" * 64, "a" * 63, "a" * 64])
def test_apply_rejects_bad_or_unknown_tokens(
    client: TestClient, container: Container, token: str
) -> None:
    before = (row_count(container), db_digest(container))
    response = client.post("/importar/aplicar", data={"token": token})
    assert response.status_code == 200 and "Envie o arquivo de novo" in response.text
    assert (row_count(container), db_digest(container)) == before


def test_a_pending_file_that_was_tampered_with_is_not_applied(
    client: TestClient, container: Container
) -> None:
    token = token_of(upload(client, SIMPLE.encode()))
    (pending_dir(container) / f"{token}.csv").write_bytes(SIMPLE.encode() + b"2026-09-03;x\n")
    response = client.post("/importar/aplicar", data={"token": token})
    assert "Envie o arquivo de novo" in response.text and row_count(container) == 0


def test_cross_origin_posts_are_refused(client: TestClient) -> None:
    response = client.post(
        "/importar/aplicar", data={"token": "a" * 64}, headers={"origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_stale_pending_files_are_purged(container: Container) -> None:
    store = PendingFiles(pending_dir(container))
    old = store.save(b"old")
    fresh = store.save(b"fresh")
    path = pending_dir(container) / f"{old}.csv"
    stamp = time.time() - 3 * 24 * 3600
    os.utime(path, (stamp, stamp))
    assert store.purge(time.time()) == 1
    assert store.load(old) is None and store.load(fresh) == b"fresh"
    assert store.load("../x") is None


# --- demo mode ---------------------------------------------------------------------------------


def test_demo_mode_previews_but_never_applies(tmp_path: Path) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'data' / 'f.db'}",
        data_dir=tmp_path / "data",
        _env_file=None,  # type: ignore[call-arg]
    ).for_demo()
    demo = Container(settings)
    demo.ensure_demo()
    with demo.uow as work:
        account = next(a for a in work.accounts.list_all() if a.kind is AccountKind.CHECKING)
    data = (
        f"date;kind;account;amount;description\n2026-09-01;expense;{account.nickname};-10,00;Pão\n"
    ).encode()
    client = TestClient(
        create_app(demo), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )
    before = (row_count(demo), db_digest(demo))
    html = upload(client, data)
    assert "Arquivo conferido" in html and "Modo demonstração: a importação não grava nada." in html
    assert re.search(r'<button type="submit" class="cta" disabled', html)
    assert 'name="token"' not in html and not (demo.import_dir / "pending").exists()
    forced = client.post("/importar/aplicar", data={"token": "a" * 64})
    assert "a importação não grava nada" in forced.text
    assert (row_count(demo), db_digest(demo)) == before


def test_a_file_with_payment_methods_and_a_pix_split_is_analysed_and_applied(
    client: TestClient, container: Container
) -> None:
    """The sample with ``metodo_pagamento``: boleto, débito, a PIX split in two items, methods
    inferred from the wording, and a card purchase that is always a card."""
    sample = (
        Path(__file__).parents[2] / "scripts" / "examples" / "entries_payment_methods_example.csv"
    )
    page = upload(client, sample.read_bytes(), "pagamentos.csv")
    assert APPLY_BUTTON in page and "Compras com subitens" in visible(page)
    applied = client.post("/importar/aplicar", data={"token": token_of(page)})
    assert applied.status_code == 200 and "Importação concluída" in applied.text
    with container.uow as work:
        accounts = {a.nickname: a.id for a in work.accounts.list_all()}
        rows = {
            t.description: t
            for name in ("Conta Corrente", "Cartão Exemplo")
            for t in work.transactions.list_by_account(accounts[name])
        }
        split = work.transactions.splits_for([rows["Feira de sábado"].id])
    methods = {d: (t.payment_method.value if t.payment_method else None) for d, t in rows.items()}
    assert methods == {
        "Condomínio Edifício Solar": "boleto",
        "Padaria Pão Quente": "debito",
        "Feira de sábado": "pix",
        "PIX TRANSF MARIA": "pix",  # from the wording
        "PAGTO ELETRON COBRANCA ENERGIA": "boleto",
        "COMPRA DEBITO FARMACIA": "debito",
        "Salário setembro": "transferencia",
        "Notebook": "cartao_credito",  # a card is always a card
    }
    assert [i.amount_cents for i in split[rows["Feira de sábado"].id]] == [11_000, 7_000]
    assert (
        rows["Feira de sábado"].amount_cents == -18_000
        and rows["Feira de sábado"].category_id is None
    )
