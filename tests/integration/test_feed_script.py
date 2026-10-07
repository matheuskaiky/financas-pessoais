"""``scripts/feed_from_csv.py`` against scratch databases: dry run, apply, guards, rollback."""

import csv
import dataclasses
import hashlib
import io
import json
from pathlib import Path

import feed_from_csv
import pytest
from typer.testing import CliRunner

from feed_support import TODAY, scratch_container, synthetic_context
from financas.application.csvfeed import analyze_feed, template_text
from financas.application.csvfeed import service as feed_service
from financas.application.csvfeed.model import AccountInfo, FeedContext
from financas.application.imports.apply import Verification
from financas.container import Container, build_container
from financas.domain.models import AccountKind, InvestmentTracking

EXAMPLE = Path(__file__).parents[2] / "scripts" / "examples" / "entries_example.csv"
runner = CliRunner()


@pytest.fixture
def scratch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Container:
    c = scratch_container(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINANCAS_DB_URL", c.settings.db_url)
    monkeypatch.setenv("FINANCAS_DATA_DIR", str(c.settings.data_dir))
    return c


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(feed_from_csv.app, list(args), terminal_width=200)
    return result.exit_code, result.output


def db_digest(c: Container) -> str:
    return hashlib.sha256(
        Path(c.settings.db_url.removeprefix("sqlite:///")).read_bytes()
    ).hexdigest()


def state(c: Container) -> dict[str, tuple[int, int]]:
    fresh = build_container()
    with fresh.uow as work:
        result = {
            a.nickname: (
                len(rows := work.transactions.list_by_account(a.id)),
                sum(t.amount_cents for t in rows),
            )
            for a in work.accounts.list_all()
        }
    fresh.engine.dispose()
    return result


def files_under(c: Container) -> set[str]:
    return {str(p.relative_to(c.settings.data_dir)) for p in c.settings.data_dir.rglob("*")}


def test_template_prints_a_header_that_parses_back_and_examples_that_work(
    scratch: Container,
) -> None:
    code, out = invoke("--template")
    assert code == 0 and out == template_text()
    header, *examples = out.splitlines()
    assert header.startswith("date;kind;account;to_account;amount;")
    assert len(examples) == 16 and all(e.startswith("# ") for e in examples)
    assert analyze_feed(out.encode(), synthetic_context()).data_rows == 0
    # uncommented, the examples are valid rows (against the accounts the template names)
    ctx = synthetic_context()
    names = {"Checking": AccountKind.CHECKING, "Savings": AccountKind.INVESTMENT,
             "Card": AccountKind.CREDIT_CARD}  # fmt: skip
    accounts = tuple(
        AccountInfo(
            n, n, k, True,
            due_day=5 if k is AccountKind.CREDIT_CARD else None,
            closing_days_before_due=11 if k is AccountKind.CREDIT_CARD else None,
            tracking=InvestmentTracking.ACCOUNT if k is AccountKind.INVESTMENT else None,
        )
        for n, k in names.items()
    )  # fmt: skip
    ctx = dataclasses.replace(ctx, accounts=accounts)
    filled = "\n".join([header, *(e.removeprefix("# ") for e in examples)]) + "\n"
    analysis = analyze_feed(filled.encode(), ctx)
    assert analysis.ok, analysis.issues
    assert analysis.data_rows == 16


def test_example_file_is_valid_against_the_scratch_accounts(scratch: Container) -> None:
    code, out = invoke(str(EXAMPLE))
    assert code == 0, out
    assert "nada foi gravado" in out and "Serão criados: 23 lançamento(s)" in out
    assert "Aluguel" not in out and "Fone de ouvido" not in out  # never descriptions


def test_dry_run_writes_nothing(scratch: Container) -> None:
    before_digest, before_files = db_digest(scratch), files_under(scratch)
    code, _ = invoke(str(EXAMPLE))
    assert code == 0
    assert db_digest(scratch) == before_digest and files_under(scratch) == before_files
    assert state(scratch)["Conta Corrente"] == (0, 0)


def test_apply_imports_the_example_with_expected_counts_and_totals(scratch: Container) -> None:
    code, out = invoke(str(EXAMPLE), "--apply")
    assert code == 0, out
    assert "Concluído: 7 lançamento(s) simples, 2 compra(s) parcelada(s), 2 transferência(s)" in out
    assert state(scratch) == {
        "Conta Corrente": (7, 223510),
        "Conta Reserva": (1, 30000),
        "Caixinha Exemplo": (1, 100000),
        "Cartão Exemplo": (14, -277090),
    }
    fresh = build_container()
    with fresh.uow as work:
        card = next(a for a in work.accounts.list_all() if a.kind is AccountKind.CREDIT_CARD)
        assert len(work.statements.list_for_card(card.id)) == 8
        anchors = sum(len(work.anchors.list_for_account(a.id)) for a in work.accounts.list_all())
        descriptions = {t.description for t in work.transactions.list_by_account(card.id)}
    fresh.engine.dispose()
    assert anchors == 2
    assert "Pagamento de fatura" in descriptions  # the adapter's text for a payment without one
    assert any(scratch.settings.backups_dir.iterdir())
    ledger = (scratch.import_dir / "csv_applied.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(ledger[0])
    assert entry["sha256"] == hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    assert entry["counts"]["transactions"] == 23 and entry["forced"] is False
    assert set(entry) == {"sha256", "applied_at", "counts", "forced"}
    assert not (scratch.import_dir / "csv_work.db").exists()
    assert "Aluguel" not in out


SIMPLE = (
    "date;kind;account;to_account;amount;description\n"
    "2026-09-01;expense;Conta Corrente;;-10,00;Pão\n"
    "2026-09-02;transfer;Conta Corrente;Conta Reserva;3,00;\n"
)


def test_second_apply_is_refused_and_force_overrides(scratch: Container, tmp_path: Path) -> None:
    path = tmp_path / "simple.csv"
    path.write_text(SIMPLE, encoding="utf-8")
    assert invoke(str(path), "--apply")[0] == 0
    after_first = state(scratch)
    code, out = invoke(str(path), "--apply")
    assert code == 4 and "já foi aplicado" in out and "--force" in out
    assert state(scratch) == after_first
    code, out = invoke(str(path), "--apply", "--force")
    assert code == 0, out
    assert state(scratch)["Conta Corrente"] == (4, -2600)
    lines = (scratch.import_dir / "csv_applied.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["forced"] for line in lines] == [False, True]
    assert "já foi aplicado" in invoke(str(path))[1]  # a dry run only mentions it


def test_forcing_the_example_again_is_stopped_by_the_paid_statement(scratch: Container) -> None:
    assert invoke(str(EXAMPLE), "--apply")[0] == 0
    before = db_digest(scratch)
    code, out = invoke(str(EXAMPLE), "--apply", "--force")
    assert code == 2 and "[STATEMENT_ALREADY_PAID]" in out  # history is never rewritten
    assert db_digest(scratch) == before


def test_a_file_with_bad_rows_writes_nothing_and_lists_the_errors(
    scratch: Container, tmp_path: Path
) -> None:
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    before = db_digest(scratch), files_under(scratch)
    # stage 1: syntax problems (reported together, with line and column)
    syntax = tmp_path / "syntax.csv"
    syntax.write_text(
        "\n".join(
            [*lines[:4], "2026-09-02;expense;Conta Corrente;;1.234;Ambíguo;;;;;;;;",
             "31/02/2026;expense;Conta Corrente;;5,00;Data;;;;;;;;", *lines[4:]]
        )
        + "\n",
        encoding="utf-8",
    )  # fmt: skip
    # stage 2: business problems, found once the syntax is clean
    business = tmp_path / "business.csv"
    business.write_text(
        "\n".join([*lines[:4], "2026-09-01;expense;Conta Corent;;10,00;Erro de digitação;;;;;;;;"])
        + "\n",
        encoding="utf-8",
    )
    for extra in ((), ("--apply",)):
        code, out = invoke(str(syntax), *extra)
        assert code == 2
        assert "linha 5 · coluna amount: [AMOUNT_AMBIGUOUS]" in out
        assert "linha 6 · coluna date: [INVALID_DATE]" in out
        code, out = invoke(str(business), *extra)
        assert code == 2
        assert "linha 5 · coluna account: [UNKNOWN_ACCOUNT]" in out
        assert "Parecidas: Conta Corrente" in out
        assert "Erro de digitação" not in out
        assert db_digest(scratch) == before[0] and files_under(scratch) == before[1]


def test_syntax_errors_exit_2_without_touching_the_database(
    scratch: Container, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_text("date;kind;account;amout\n2026-09-01;expense;Conta Corrente;5\n", "utf-8")
    code, out = invoke(str(broken), "--apply")
    assert code == 2 and "[UNKNOWN_COLUMN]" in out and "[MISSING_COLUMN]" in out
    latin = tmp_path / "latin.csv"
    latin.write_bytes("date;kind\nSalário".encode("latin-1"))
    assert "[FILE_NOT_UTF8]" in invoke(str(latin))[1]


def test_a_failed_verification_leaves_the_database_untouched(
    scratch: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*_: object) -> Verification:
        result = Verification()
        result.add("SUM_BY_ACCOUNT", False, "x")
        return result

    before = db_digest(scratch)
    with monkeypatch.context() as patch:
        patch.setattr(feed_service, "verify_feed", failing)
        code, out = invoke(str(EXAMPLE), "--apply")
    assert code == 3 and "o banco não foi alterado" in out
    assert db_digest(scratch) == before
    assert state(scratch)["Conta Corrente"] == (0, 0)
    assert not (scratch.import_dir / "csv_work.db").exists()
    assert not (scratch.import_dir / "csv_applied.jsonl").exists()  # not recorded as applied
    assert invoke(str(EXAMPLE), "--apply")[0] == 0  # and the same file can still be applied


def test_a_failure_in_the_middle_of_the_apply_rolls_everything_back(
    scratch: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = feed_service.ApplyFeed

    class Exploding(real):  # type: ignore[valid-type,misc]
        def _payment(self, action: object, description: str) -> None:
            raise RuntimeError("boom with Aluguel inside")

    monkeypatch.setattr(feed_service, "ApplyFeed", Exploding)
    before = db_digest(scratch)
    code, out = invoke(str(EXAMPLE), "--apply")
    assert code == 3 and "boom" not in out and "Aluguel" not in out
    assert db_digest(scratch) == before
    assert not (scratch.import_dir / "csv_work.db").exists()


def test_other_delimiters_bom_and_crlf_give_the_same_plan(
    scratch: Container, tmp_path: Path
) -> None:
    rows = [
        r for r in csv.reader(io.StringIO(EXAMPLE.read_text("utf-8")), delimiter=";")
        if r and not r[0].startswith("#")
    ]  # fmt: skip
    expected = invoke(str(EXAMPLE))[1].split("Serão criados")[1].splitlines()[0]
    for name, delimiter in (("comma", ","), ("tab", "\t")):
        target = tmp_path / f"{name}.csv"
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
        code, out = invoke(str(target))
        assert code == 0, out
        assert out.split("Serão criados")[1].splitlines()[0] == expected
        forced_code, _ = invoke(str(target), "--delimiter", "tab" if name == "tab" else ",")
        assert forced_code == 0
    assert invoke(str(tmp_path / "comma.csv"), "--delimiter", ";")[0] == 2  # wrong on purpose


def test_usage_errors_exit_1(scratch: Container, tmp_path: Path) -> None:
    assert invoke()[0] == 1
    assert invoke(str(tmp_path / "missing.csv"))[0] == 1
    assert invoke(str(EXAMPLE), "--delimiter", "x")[0] == 1
    empty = tmp_path / "empty.csv"
    empty.write_text("date;kind;account;amount\n# nothing\n", encoding="utf-8")
    assert invoke(str(empty))[0] == 1


def test_missing_database_exits_1_and_is_not_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FINANCAS_DB_URL", f"sqlite:///{tmp_path / 'none' / 'f.db'}")
    monkeypatch.setenv("FINANCAS_DATA_DIR", str(tmp_path / "none"))
    code, out = invoke(str(EXAMPLE), "--apply")
    assert code == 1 and "Banco de dados não encontrado" in out
    assert not (tmp_path / "none").exists()


def test_accents_and_case_in_names_and_unknown_category(scratch: Container, tmp_path: Path) -> None:
    path = tmp_path / "names.csv"
    path.write_text(
        "date;kind;account;amount;description;category\n"
        "2026-09-10;expense;CARTAO EXEMPLO;10,00;Café São João;ALIMENTACAO\n"
        "2026-09-10;expense;conta corrente;10,00;Ação;educacao\n",
        encoding="utf-8",
    )
    assert invoke(str(path), "--apply")[0] == 0
    fresh = build_container()
    with fresh.uow as work:
        found = {
            t.description: t.description_search
            for a in work.accounts.list_all()
            for t in work.transactions.list_by_account(a.id)
        }
    fresh.engine.dispose()
    assert found == {"Café São João": "cafe sao joao", "Ação": "acao"}
    path.write_text(path.read_text("utf-8").replace("educacao", "mercadinho"), encoding="utf-8")
    code, out = invoke(str(path))
    assert code == 2 and "[UNKNOWN_CATEGORY]" in out


def test_future_data_limit_and_today_constant() -> None:
    assert TODAY.year == 2026 and isinstance(synthetic_context(), FeedContext)
