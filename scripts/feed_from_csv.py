"""Alimenta o banco a partir de um CSV no formato do projeto.

Veja CLAUDE.md 13.3 e docs/IMPORTACAO_DADOS.md.

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

from financas.application.csvfeed import template_text
from financas.application.csvfeed.service import FeedStatus, run_feed
from financas.container import Container, build_container
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


@app.command()
def feed(
    file: Annotated[
        Path | None, typer.Argument(help="CSV no formato docs/IMPORTACAO_DADOS.md.")
    ] = None,
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
        c.close()  # no -wal/-shm files are left behind


def _run(c: Container, data: bytes, chosen: str | None, *, apply: bool, force: bool) -> None:
    outcome = run_feed(
        c.feed_workspace(),
        report.FileLedger(c.import_dir),
        data,
        chosen,
        apply=apply,
        force=force,
        payment_description=PAYMENT_DESCRIPTION,
        origin="feed_from_csv",
    )
    status = outcome.status
    if status is FeedStatus.NO_DATABASE:
        raise fail(REPORT["no_database"].format(path=c.settings.db_url), EXIT_USAGE)
    if status is FeedStatus.NO_ROWS:
        raise fail(REPORT["no_rows"], EXIT_USAGE)
    if status is FeedStatus.NEEDS_MIGRATION:
        raise fail(REPORT["needs_migration"], EXIT_USAGE)
    if status is FeedStatus.ALREADY_APPLIED:
        assert outcome.already is not None
        raise fail(
            REPORT["already"].format(sha=outcome.sha256, when=report.applied_when(outcome.already)),
            EXIT_ALREADY,
        )
    if status is FeedStatus.INVALID:
        for line in report.render_issues(outcome.issues):
            typer.echo(line, err=True)
        raise typer.Exit(EXIT_INVALID)

    names = outcome.names
    if outcome.analysis is not None:
        say(
            "\n".join(
                report.render_plan(
                    outcome.analysis, names, REPORT["title_apply"] if apply else None
                )
            )
        )
    if status is FeedStatus.PREVIEW:
        if outcome.already is not None:
            say(
                REPORT["already"].format(
                    sha=outcome.sha256[:16] + "…", when=report.applied_when(outcome.already)
                )
            )
        say(REPORT["next"])
        return
    say(REPORT["backup"].format(path=outcome.backup))
    if status is FeedStatus.FAILED:
        if outcome.error is not None:
            typer.echo(REPORT["apply_failed"].format(code=outcome.error.code), err=True)
            typer.echo(render_error(outcome.error), err=True)
        else:
            typer.echo(REPORT["apply_failed"].format(code=outcome.failure_code), err=True)
        raise typer.Exit(EXIT_FAILED)

    verification = outcome.verification
    result = outcome.result
    assert verification is not None and result is not None
    for check in verification.checks:
        if not check.ok:
            label = CHECK_LABELS.get(check.code, check.code)
            typer.echo(REPORT["check_fail"].format(label=label, detail=check.detail), err=True)
    for code in sorted({chk.code for chk in verification.checks}):
        if all(chk.ok for chk in verification.checks if chk.code == code):
            say(REPORT["check_ok"].format(label=CHECK_LABELS.get(code, code)))
    if status is FeedStatus.VERIFY_FAILED:
        typer.echo(REPORT["verify_failed"], err=True)
        raise typer.Exit(EXIT_FAILED)

    if result.balances:
        say(REPORT["balances_title"])
        for outcome_balance in result.balances:
            fields = {
                "account": names[outcome_balance.account_id],
                "date": format_date(outcome_balance.on_date),
                "informed": format_brl(outcome_balance.informed_cents),
            }
            if outcome_balance.computed_cents is None or outcome_balance.difference_cents is None:
                say(REPORT["balance_diff_none"].format(**fields))
            else:
                say(
                    REPORT["balance_diff"].format(
                        **fields,
                        computed=format_brl(outcome_balance.computed_cents),
                        difference=format_brl(outcome_balance.difference_cents),
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
