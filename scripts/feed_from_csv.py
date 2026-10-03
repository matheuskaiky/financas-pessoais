"""Alimenta o banco a partir de um CSV no formato do projeto (CLAUDE.md 13.3, docs/CSV_IMPORT.md).

    uv run python scripts/feed_from_csv.py --template > lancamentos.csv
    uv run python scripts/feed_from_csv.py lancamentos.csv             # simulação (padrão)
    uv run python scripts/feed_from_csv.py lancamentos.csv --apply     # grava

Adaptador fino: lê o arquivo, monta o container e imprime o relatório em pt-BR. A análise e as
regras estão em ``financas.application.csvfeed``; o script fica fora das camadas, como ``cli.py``.

Códigos de saída: 0 ok · 1 uso ou erro de leitura · 2 arquivo com problemas (nada gravado) ·
3 falha ao aplicar (banco intacto) · 4 arquivo já aplicado.
"""

from pathlib import Path
from typing import Annotated

import typer

from financas.application.csvfeed import (
    ApplyFeed,
    FeedAnalysis,
    load_context,
    parse_feed,
    plan_feed,
    snapshot,
    template_text,
    verify_feed,
)
from financas.container import Container, build_container
from financas.domain.errors import DomainError
from financas.domain.money import format_brl
from financas.interfaces import csvfeed as report
from financas.interfaces.formatting import format_date
from financas.interfaces.messages import render_error
from financas.interfaces.messages.csvfeed import CHECK_LABELS, PAYMENT_DESCRIPTION, REPORT

EXIT_OK, EXIT_USAGE, EXIT_INVALID, EXIT_FAILED, EXIT_ALREADY = 0, 1, 2, 3, 4

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
_DELIMITERS = {",": ",", ";": ";", "tab": "\t", "\\t": "\t", "\t": "\t"}


def say(text: str) -> None:
    typer.echo(text)


def fail(text: str, code: int) -> typer.Exit:
    typer.echo(text, err=True)
    return typer.Exit(code)


def _analyze(
    c: Container, data: bytes, delimiter: str | None
) -> tuple[FeedAnalysis, dict[str, str]]:
    context = load_context(c.uow, c.clock)
    parsed = parse_feed(data, context.today, delimiter)
    names = {a.id: a.nickname for a in context.accounts}
    return plan_feed(parsed, context), names


def _discard(work: Container, path: Path) -> None:
    work.engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


@app.command()
def feed(
    file: Annotated[Path | None, typer.Argument(help="CSV no formato docs/CSV_IMPORT.md.")] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Grava no banco (sem esta opção é só simulação).")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Aplica mesmo que este arquivo já tenha sido aplicado.")
    ] = False,
    delimiter: Annotated[
        str | None,
        typer.Option("--delimiter", help="Separador: , ; ou tab (padrão: detectado no cabeçalho)."),
    ] = None,
    template: Annotated[
        bool,
        typer.Option("--template", help="Imprime o cabeçalho e uma linha de exemplo por tipo."),
    ] = False,
) -> None:
    """Alimenta o banco a partir de um CSV: simulação por padrão, --apply para gravar."""
    if template:
        typer.echo(template_text(), nl=False)
        return
    if file is None:
        raise fail("Informe o arquivo CSV (ou use --template).", EXIT_USAGE)
    chosen = None
    if delimiter is not None:
        chosen = _DELIMITERS.get(delimiter.lower())
        if chosen is None:
            raise fail("Separador inválido: use , ; ou tab.", EXIT_USAGE)
    try:
        data = file.read_bytes()
    except OSError as error:
        raise fail(
            REPORT["unreadable"].format(reason=error.strerror or "erro"), EXIT_USAGE
        ) from None

    c = build_container()
    try:
        _run(c, data, chosen, apply=apply, force=force)
    finally:
        if "engine" in c.__dict__:  # never create an engine (and its folder) just to close it
            c.engine.dispose()  # no -wal/-shm files are left behind


def _run(c: Container, data: bytes, chosen: str | None, *, apply: bool, force: bool) -> None:
    if not c.database_exists():
        raise fail(REPORT["no_database"].format(path=c.settings.db_url), EXIT_USAGE)
    ledger = report.ledger_path(c.import_dir)

    # 1. syntax (no database needed): every problem with its line and column, nothing written
    today = c.clock.today()
    parsed = parse_feed(data, today, chosen)
    if parsed.issues:
        for line in report.render_issues(parsed.issues):
            typer.echo(line, err=True)
        raise typer.Exit(EXIT_INVALID)
    if parsed.data_rows == 0:
        raise fail(REPORT["no_rows"], EXIT_USAGE)
    already = report.find_applied(ledger, parsed.sha256)
    if apply and already is not None and not force:
        raise fail(
            REPORT["already"].format(sha=parsed.sha256, when=report.applied_when(already)),
            EXIT_ALREADY,
        )

    # 2. business rules against the database
    migrate_first = c.needs_migration()
    if migrate_first and not apply:
        raise fail(REPORT["needs_migration"], EXIT_USAGE)
    if not migrate_first:
        analysis, names = _analyze(c, data, chosen)
        if not analysis.ok:
            for line in report.render_issues(analysis.issues):
                typer.echo(line, err=True)
            raise typer.Exit(EXIT_INVALID)
        say(
            "\n".join(report.render_plan(analysis, names, REPORT["title_apply"] if apply else None))
        )
        if not apply:
            if already is not None:
                say(
                    REPORT["already"].format(
                        sha=parsed.sha256[:16] + "…", when=report.applied_when(already)
                    )
                )
            say(REPORT["next"])
            return

    # 3. --apply: backup, working copy, apply, verify, swap
    backup = c.backup()
    say(REPORT["backup"].format(path=backup))
    work_path = c.import_dir / "csv_work.db"
    work = c.working_copy(work_path)
    try:
        if migrate_first:
            work.migrate()
        analysis, names = _analyze(work, data, chosen)
        if not analysis.ok or analysis.plan is None:
            _discard(work, work_path)
            for line in report.render_issues(analysis.issues):
                typer.echo(line, err=True)
            raise typer.Exit(EXIT_INVALID)
        if migrate_first:
            say("\n".join(report.render_plan(analysis, names, REPORT["title_apply"])))
        plan = analysis.plan
        before = snapshot(work.uow, plan)
        result = ApplyFeed(work.uow, work.clock).execute(plan, PAYMENT_DESCRIPTION)
        verification = verify_feed(work.uow, plan, before)
    except typer.Exit:
        raise
    except DomainError as error:
        _discard(work, work_path)
        typer.echo(REPORT["apply_failed"].format(code=error.code), err=True)
        typer.echo(render_error(error), err=True)
        raise typer.Exit(EXIT_FAILED) from error
    except Exception as error:
        _discard(work, work_path)
        failure = c.failures.record_exception("csvfeed", "cli_error", error, path="feed_from_csv")
        typer.echo(REPORT["apply_failed"].format(code=failure), err=True)
        raise typer.Exit(EXIT_FAILED) from error

    for check in verification.checks:
        label = CHECK_LABELS.get(check.code, check.code)
        if check.ok:
            continue
        typer.echo(REPORT["check_fail"].format(label=label, detail=check.detail), err=True)
    for code in sorted({chk.code for chk in verification.checks}):
        if all(chk.ok for chk in verification.checks if chk.code == code):
            say(REPORT["check_ok"].format(label=CHECK_LABELS.get(code, code)))
    if not verification.ok:
        _discard(work, work_path)
        typer.echo(REPORT["verify_failed"], err=True)
        raise typer.Exit(EXIT_FAILED)

    c.adopt(work)
    counts = {
        "entries": result.entries,
        "purchases": result.purchases,
        "transfers": result.transfers,
        "payments": result.payments,
        "balances": len(result.balances),
        "transactions": plan.transactions,
        "rows": analysis.data_rows,
    }
    report.record_applied(ledger, parsed.sha256, c.clock.now(), counts, forced=already is not None)
    if result.balances:
        say(REPORT["balances_title"])
        for outcome in result.balances:
            fields = {
                "account": names[outcome.account_id],
                "date": format_date(outcome.on_date),
                "informed": format_brl(outcome.informed_cents),
            }
            if outcome.computed_cents is None or outcome.difference_cents is None:
                say(REPORT["balance_diff_none"].format(**fields))
            else:
                say(
                    REPORT["balance_diff"].format(
                        **fields,
                        computed=format_brl(outcome.computed_cents),
                        difference=format_brl(outcome.difference_cents),
                    )
                )
    say(
        REPORT["done"].format(
            entries=result.entries,
            purchases=result.purchases,
            transfers=result.transfers,
            payments=result.payments,
            balances=len(result.balances),
        )
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
