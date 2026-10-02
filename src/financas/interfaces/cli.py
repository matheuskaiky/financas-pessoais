"""Command line adapter: thin layer over the use cases. All output is pt-BR."""

import datetime as dt
import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from financas.application.queries.balances import ListAccountBalances
from financas.application.queries.cards import (
    GetStatementDetail,
    ListActiveInstallments,
    ListCards,
)
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.cards import (
    CardPurchaseCommand,
    InformStatementTotal,
    PayStatement,
    PayStatementCommand,
    PostStatementDifference,
    PreviewCardPurchase,
    RegisterCardPurchase,
    SetCardSettings,
    SetStatementDates,
)
from financas.application.use_cases.catalog import (
    AppearanceTarget,
    CreateAccount,
    CreateAccountCommand,
    CreateCategory,
    CreateCategoryCommand,
    CreateInstitution,
    CreateInstitutionCommand,
    SetAccountActive,
    SetAppearance,
    SetAppearanceCommand,
)
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
    SuggestCategory,
)
from financas.container import Container, build_container
from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    CategoryGroup,
    CategoryKind,
    TransactionKind,
)
from financas.domain.money import YearMonth, format_brl, parse_brl
from financas.interfaces import messages
from financas.interfaces.formatting import (
    format_date,
    format_date_short,
    format_month_long,
    format_percent,
    parse_date,
)
from financas.interfaces.formatting import (
    format_month as format_month_short,
)
from financas.interfaces.resolve import (
    default_checking_account,
    find_account,
    find_card,
    find_category,
    find_institution,
    find_statement,
)

app = typer.Typer(
    help="Finanças pessoais: tudo local, nada sai do seu computador.", no_args_is_help=True
)
institution_app = typer.Typer(help="Instituições (bancos, corretoras).", no_args_is_help=True)
account_app = typer.Typer(help="Contas, cartões e investimentos.", no_args_is_help=True)
category_app = typer.Typer(help="Categorias.", no_args_is_help=True)
balance_app = typer.Typer(help="Saldos informados.", no_args_is_help=True)
card_app = typer.Typer(help="Cartões de crédito: compras, parcelas e limite.", no_args_is_help=True)
statement_app = typer.Typer(help="Faturas dos cartões.", no_args_is_help=True)
app.add_typer(institution_app, name="institution")
app.add_typer(account_app, name="account")
app.add_typer(category_app, name="category")
app.add_typer(balance_app, name="balance")
app.add_typer(card_app, name="card")
app.add_typer(statement_app, name="statement")

console = Console()
err_console = Console(stderr=True)

Color = Annotated[str | None, typer.Option("--color", help="Cor no formato #RRGGBB.")]
Image = Annotated[
    Path | None,
    typer.Option(
        "--image", exists=True, dir_okay=False, help="Imagem PNG, JPEG ou WebP (até 512 KB)."
    ),
]


def container() -> Container:
    return build_container()


def handle_errors[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except DomainError as error:
            err_console.print(f"[red]{messages.render_error(error)}[/red]")
            raise typer.Exit(1) from error

    return wrapper


def _set_appearance(
    c: Container, target: AppearanceTarget, entity_id: str, color: str | None, image: Path | None
) -> None:
    if color is None and image is None:
        return
    SetAppearance(c.uow, c.images).execute(
        SetAppearanceCommand(
            target, entity_id, color=color, image=image.read_bytes() if image else None
        )
    )


def _swatch(color: str | None) -> str:
    return f"[{color}]■[/{color}] {color}" if color else "—"


@app.command("init")
@handle_errors
def init() -> None:
    """Cria o banco de dados e as categorias iniciais."""
    c = container()
    c.migrate()
    created = c.seed()
    console.print(f"Banco pronto. Categorias criadas: {created}.")


# --- institutions -----------------------------------------------------------------------------


@institution_app.command("add")
@handle_errors
def institution_add(
    name: Annotated[str, typer.Argument(help="Nome, por exemplo: Banco do Brasil.")],
    group: Annotated[str | None, typer.Option("--group", help="Conglomerado (para o FGC).")] = None,
    color: Color = None,
    image: Image = None,
) -> None:
    """Cadastra uma instituição."""
    c = container()
    institution = CreateInstitution(c.uow).execute(
        CreateInstitutionCommand(name=name, group_slug=group, color=color)
    )
    _set_appearance(c, AppearanceTarget.INSTITUTION, institution.id, color, image)
    console.print(f"Instituição criada: {institution.name} ({institution.slug}).")


@institution_app.command("list")
@handle_errors
def institution_list() -> None:
    """Lista as instituições."""
    c = container()
    with c.uow as work:
        items = work.institutions.list_all()
    table = Table("Nome", "Identificador", "Cor", "Imagem")
    for i in items:
        table.add_row(i.name, i.slug, _swatch(i.color), "sim" if i.image_id else "—")
    console.print(table)


# --- accounts ---------------------------------------------------------------------------------


@account_app.command("add")
@handle_errors
def account_add(
    nickname: Annotated[str, typer.Argument(help="Apelido, por exemplo: Conta corrente.")],
    institution: Annotated[str, typer.Option("--institution", "-i", help="Nome da instituição.")],
    kind: Annotated[
        AccountKind, typer.Option("--kind", "-k", help="checking, credit_card ou investment.")
    ] = AccountKind.CHECKING,
    color: Color = None,
    image: Image = None,
) -> None:
    """Cadastra uma conta, cartão ou conta de investimento."""
    c = container()
    inst = find_institution(c.uow, institution)
    account = CreateAccount(c.uow).execute(
        CreateAccountCommand(kind, inst.id, nickname, color=color)
    )
    _set_appearance(c, AppearanceTarget.ACCOUNT, account.id, color, image)
    console.print(f"Conta criada: {account.nickname} ({messages.ACCOUNT_KIND_LABELS[kind]}).")


@account_app.command("list")
@handle_errors
def account_list() -> None:
    """Lista as contas com o saldo de hoje."""
    c = container()
    balances = {b.account_id: b for b in ListAccountBalances(c.uow).execute(c.clock.today())}
    with c.uow as work:
        accounts = work.accounts.list_all()
        institutions = {i.id: i for i in work.institutions.list_all()}
    table = Table("Conta", "Instituição", "Tipo", "Saldo", "Cor", "Situação")
    for a in accounts:
        balance = balances[a.id].balance_cents if a.id in balances else None
        shown = (
            "veja “card list”"
            if a.kind is AccountKind.CREDIT_CARD
            else ("saldo indisponível" if balance is None else format_brl(balance))
        )
        table.add_row(
            a.nickname,
            institutions[a.institution_id].name,
            messages.ACCOUNT_KIND_LABELS[a.kind],
            shown,
            _swatch(a.color),
            "ativa" if a.is_active else "desativada",
        )
    console.print(table)


@account_app.command("deactivate")
@handle_errors
def account_deactivate(name: Annotated[str, typer.Argument(help="Apelido da conta.")]) -> None:
    """Desativa uma conta (ela deixa de aceitar lançamentos)."""
    c = container()
    account = find_account(c.uow, name)
    SetAccountActive(c.uow).execute(account.id, False)
    console.print(f"Conta desativada: {account.nickname}.")


# --- categories -------------------------------------------------------------------------------


@category_app.command("list")
@handle_errors
def category_list() -> None:
    """Lista as categorias."""
    c = container()
    with c.uow as work:
        items = work.categories.list_all()
    table = Table("Nome", "Identificador", "Grupo", "Tipo", "Orçamento", "Cor")
    for cat in items:
        table.add_row(
            cat.name,
            cat.slug,
            messages.CATEGORY_GROUP_LABELS[cat.group],
            messages.CATEGORY_KIND_LABELS[cat.kind],
            format_brl(cat.monthly_budget_cents) if cat.monthly_budget_cents is not None else "—",
            _swatch(cat.color),
        )
    console.print(table)


@category_app.command("add")
@handle_errors
def category_add(
    name: Annotated[str, typer.Argument(help="Nome da categoria.")],
    group: Annotated[
        CategoryGroup,
        typer.Option(
            "--group", "-g", help="essential, non_essential, charges, income, movement, review."
        ),
    ],
    kind: Annotated[
        CategoryKind, typer.Option("--kind", "-k", help="expense, income ou neutral.")
    ] = CategoryKind.EXPENSE,
    budget: Annotated[str | None, typer.Option("--budget", help="Orçamento mensal.")] = None,
    color: Color = None,
) -> None:
    """Cria uma categoria."""
    c = container()
    category = CreateCategory(c.uow).execute(
        CreateCategoryCommand(
            name=name,
            group=group,
            kind=kind,
            monthly_budget_cents=parse_brl(budget) if budget else None,
            color=color,
        )
    )
    console.print(f"Categoria criada: {category.name} ({category.slug}).")


# --- appearance -------------------------------------------------------------------------------


@app.command("style")
@handle_errors
def style(
    target: Annotated[AppearanceTarget, typer.Argument(help="institution, account ou category.")],
    name: Annotated[str, typer.Argument(help="Nome do item.")],
    color: Color = None,
    image: Image = None,
    remove_image: Annotated[bool, typer.Option("--remove-image", help="Remove a imagem.")] = False,
) -> None:
    """Define cor e imagem de uma instituição, conta ou categoria."""
    c = container()
    finders = {
        AppearanceTarget.INSTITUTION: find_institution,
        AppearanceTarget.ACCOUNT: find_account,
        AppearanceTarget.CATEGORY: find_category,
    }
    entity = finders[target](c.uow, name)
    SetAppearance(c.uow, c.images).execute(
        SetAppearanceCommand(
            target,
            entity.id,
            color=color if color is not None else entity.color,
            image=image.read_bytes() if image else None,
            remove_image=remove_image,
        )
    )
    console.print("Aparência atualizada.")


# --- entries ----------------------------------------------------------------------------------


@app.command("add")
@handle_errors
def add(
    amount: Annotated[str, typer.Argument(help="Valor, por exemplo: 45,90.")],
    description: Annotated[str, typer.Argument(help="Descrição.")],
    kind: Annotated[
        TransactionKind, typer.Option("--kind", "-k", help="expense, income ou refund.")
    ] = TransactionKind.EXPENSE,
    account: Annotated[str | None, typer.Option("--account", "-a", help="Conta.")] = None,
    category: Annotated[str | None, typer.Option("--category", "-c", help="Categoria.")] = None,
    date: Annotated[str, typer.Option("--date", "-d", help="dd/mm/aaaa, hoje ou ontem.")] = "hoje",
    recurring: Annotated[
        bool, typer.Option("--recurring", "-r", help="Lançamento recorrente.")
    ] = False,
    notes: Annotated[str | None, typer.Option("--notes", help="Observações.")] = None,
    statement: Annotated[
        str | None, typer.Option("--statement", help="Fatura (AAAA-MM), só para cartões.")
    ] = None,
) -> None:
    """Lança uma despesa, receita ou estorno (num cartão, só despesa ou estorno)."""
    c = container()
    chosen = find_account(c.uow, account) if account else default_checking_account(c.uow)
    if chosen is None:
        err_console.print(
            "[red]Informe a conta com --account (há zero ou várias contas correntes).[/red]"
        )
        raise typer.Exit(1)
    if category:
        category_id = find_category(c.uow, category).id
    else:
        suggestion = SuggestCategory(c.uow).execute(description, kind)
        category_id = suggestion.id if suggestion else None
        if suggestion:
            console.print(f"Categoria sugerida: {suggestion.name}.")
    transaction = RegisterTransaction(c.uow).execute(
        RegisterTransactionCommand(
            account_id=chosen.id,
            posted_on=parse_date(date, c.clock.today()),
            kind=kind,
            amount_cents=parse_brl(amount),
            description=description,
            category_id=category_id,
            is_recurring=recurring,
            notes=notes,
            statement_month=YearMonth.parse(statement) if statement else None,
        )
    )
    console.print(
        f"{messages.TRANSACTION_KIND_LABELS[kind]} lançada: {format_brl(transaction.amount_cents)}"
        f" em {format_date(transaction.posted_on)} ({chosen.nickname})."
        f" Código: {transaction.id[:8]}"
    )


@app.command("transfer")
@handle_errors
def transfer(
    amount: Annotated[str, typer.Argument(help="Valor, por exemplo: 500,00.")],
    from_account: Annotated[
        str | None, typer.Option("--from", help="Conta de origem (omita se não for controlada).")
    ] = None,
    to_account: Annotated[
        str | None, typer.Option("--to", help="Conta de destino (omita se não for controlada).")
    ] = None,
    date: Annotated[str, typer.Option("--date", "-d", help="dd/mm/aaaa, hoje ou ontem.")] = "hoje",
    description: Annotated[str, typer.Option("--description", help="Descrição.")] = "",
) -> None:
    """Lança uma transferência (inclui aportes e resgates). Não é receita nem despesa."""
    c = container()
    origin = find_account(c.uow, from_account).id if from_account else None
    destination = find_account(c.uow, to_account).id if to_account else None
    legs = RegisterTransfer(c.uow).execute(
        RegisterTransferCommand(
            origin, destination, parse_date(date, c.clock.today()), parse_brl(amount), description
        )
    )
    console.print(f"Transferência lançada ({len(legs)} perna(s)). Código: {legs[0].id[:8]}")


@app.command("list")
@handle_errors
def list_entries(
    month: Annotated[
        str | None, typer.Option("--month", "-m", help="AAAA-MM (padrão: este mês).")
    ] = None,
) -> None:
    """Lista os lançamentos do mês."""
    c = container()
    ym = YearMonth.parse(month) if month else YearMonth.from_date(c.clock.today())
    period = Period.month(ym)
    with c.uow as work:
        rows = work.transactions.list_between(period.start, period.end)
        accounts = {a.id: a.nickname for a in work.accounts.list_all()}
        categories = {x.id: x.name for x in work.categories.list_all()}
    table = Table(
        "Código",
        "Data",
        "Conta",
        "Tipo",
        "Categoria",
        "Descrição",
        "Valor",
        title=format_month_long(ym),
    )
    for t in rows:
        style_ = "red" if t.amount_cents < 0 else "green"
        table.add_row(
            t.id[:8],
            format_date(t.posted_on),
            accounts[t.account_id],
            messages.TRANSACTION_KIND_LABELS[t.kind],
            categories[t.category_id],
            t.description,
            f"[{style_}]{format_brl(t.amount_cents)}[/{style_}]",
        )
    console.print(table)


@app.command("delete")
@handle_errors
def delete(
    code: Annotated[str, typer.Argument(help="Código do lançamento (do comando list).")],
) -> None:
    """Apaga um lançamento (numa transferência, apaga as duas pernas)."""
    c = container()
    with c.uow as work:
        matches = [
            t
            for t in work.transactions.list_between(dt.date.min, dt.date.max)
            if t.id.startswith(code)
        ]
    if len(matches) != 1:
        raise DomainError(
            "AMBIGUOUS_REFERENCE" if matches else "NOT_FOUND", reference=code, entity="transaction"
        )
    count = DeleteTransaction(c.uow).execute(matches[0].id)
    console.print(f"Lançamentos apagados: {count}.")


# --- balances ---------------------------------------------------------------------------------


@balance_app.command("set")
@handle_errors
def balance_set(
    account: Annotated[str, typer.Argument(help="Apelido da conta.")],
    amount: Annotated[
        str, typer.Argument(help="Saldo informado, por exemplo: 1.234,56 ou -50,00.")
    ],
    date: Annotated[str, typer.Option("--date", "-d", help="dd/mm/aaaa, hoje ou ontem.")] = "hoje",
    note: Annotated[str | None, typer.Option("--note", help="Observação.")] = None,
) -> None:
    """Informa o saldo de uma conta numa data."""
    c = container()
    chosen = find_account(c.uow, account)
    result = RecordBalance(c.uow).execute(
        RecordBalanceCommand(chosen.id, parse_date(date, c.clock.today()), parse_brl(amount), note)
    )
    console.print(f"Saldo registrado: {format_brl(result.anchor.balance_cents)}.")
    if result.difference_cents is not None and result.computed_cents is not None:
        console.print(
            f"O sistema esperava {format_brl(result.computed_cents)}; diferença de "
            f"{format_brl(result.difference_cents)} (lançamentos faltando ou rendimento)."
        )


@balance_app.command("list")
@handle_errors
def balance_list() -> None:
    """Mostra os saldos de hoje."""
    account_list()


# --- reports and operations -------------------------------------------------------------------


@app.command("summary")
@handle_errors
def summary(
    month: Annotated[
        str | None, typer.Option("--month", "-m", help="AAAA-MM (padrão: este mês).")
    ] = None,
    year: Annotated[int | None, typer.Option("--year", "-y", help="Ano inteiro.")] = None,
) -> None:
    """Totais do mês ou do ano."""
    c = container()
    if year is not None:
        period, title = Period.year(year), str(year)
    else:
        ym = YearMonth.parse(month) if month else YearMonth.from_date(c.clock.today())
        period, title = Period.month(ym), format_month_long(ym)
    result = GetSummary(c.uow).execute(period)
    with c.uow as work:
        categories = {x.id: x for x in work.categories.list_all()}
    console.print(f"[bold]{title}[/bold]")
    console.print(f"Receitas:  {format_brl(result.income_cents)}")
    console.print(f"Despesas:  {format_brl(result.expenses_cents)}")
    if result.refunds_cents:
        console.print(
            f"Estornos:  {format_brl(result.refunds_cents)} "
            f"(despesas líquidas: {format_brl(result.net_expenses_cents)})"
        )
    console.print(f"Saldo:     {format_brl(result.balance_cents)}")
    console.print(f"Taxa de poupança: {format_percent(result.savings_rate)}")
    console.print(
        f"Recorrentes: {format_brl(result.recurring_expenses_cents)} · "
        f"Variáveis: {format_brl(result.variable_expenses_cents)}"
    )
    table = Table("Categoria", "Grupo", "Total", "Qtd.", "% das despesas")
    for row in result.by_category:
        table.add_row(
            categories[row.category_id].name,
            messages.CATEGORY_GROUP_LABELS[row.group],
            format_brl(row.total_cents),
            str(row.count),
            format_percent(row.share),
        )
    console.print(table)


@app.command("backup")
@handle_errors
def backup() -> None:
    """Copia o banco e as imagens para data/backups/."""
    c = container()
    target = c.backup()
    console.print(f"Backup criado em {target}.")
    console.print("Guarde também uma cópia em outro disco ou dispositivo.")


@app.command("serve")
def serve(port: Annotated[int, typer.Option("--port", help="Porta.")] = 8000) -> None:
    """Inicia o painel web em http://127.0.0.1:8000."""
    import uvicorn

    from financas.interfaces.web.app import create_app

    c = container()
    c.migrate()
    c.seed()
    uvicorn.run(create_app(c), host=c.settings.host, port=port)


# --- cards ------------------------------------------------------------------------------------


def _optional_money(text: str | None) -> int | None:
    return parse_brl(text) if text else None


@card_app.command("add")
@handle_errors
def card_add(
    nickname: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    institution: Annotated[str, typer.Option("--institution", "-i", help="Instituição.")],
    closing: Annotated[int, typer.Option("--closing", help="Dia de fechamento (1 a 31).")],
    due: Annotated[int, typer.Option("--due", help="Dia de vencimento (1 a 31).")],
    limit: Annotated[
        str | None, typer.Option("--limit", help="Limite, por exemplo 6.000,00.")
    ] = None,
    color: Color = None,
    image: Image = None,
) -> None:
    """Cadastra um cartão de crédito."""
    c = container()
    inst = find_institution(c.uow, institution)
    card = CreateAccount(c.uow).execute(
        CreateAccountCommand(
            AccountKind.CREDIT_CARD,
            inst.id,
            nickname,
            color=color,
            closing_day=closing,
            due_day=due,
            credit_limit_cents=_optional_money(limit),
        )
    )
    _set_appearance(c, AppearanceTarget.ACCOUNT, card.id, color, image)
    console.print(f"Cartão criado: {card.nickname} (fecha dia {closing}, vence dia {due}).")


@card_app.command("edit")
@handle_errors
def card_edit(
    name: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    closing: Annotated[
        int | None, typer.Option("--closing", help="Novo dia de fechamento.")
    ] = None,
    due: Annotated[int | None, typer.Option("--due", help="Novo dia de vencimento.")] = None,
    limit: Annotated[str | None, typer.Option("--limit", help="Novo limite.")] = None,
    no_limit: Annotated[bool, typer.Option("--no-limit", help="Remove o limite.")] = False,
) -> None:
    """Altera fechamento, vencimento ou limite. Só vale para lançamentos novos."""
    c = container()
    card = find_card(c.uow, name)
    new_limit = None if no_limit else (_optional_money(limit) if limit else card.credit_limit_cents)
    SetCardSettings(c.uow).execute(
        card.id,
        closing if closing is not None else card.closing_day or 0,
        due if due is not None else card.due_day or 0,
        new_limit,
    )
    console.print("Cartão atualizado. Faturas já criadas mantêm as datas.")


@card_app.command("list")
@handle_errors
def card_list() -> None:
    """Cartões: limite, comprometido, faturas a pagar e parcelas futuras."""
    c = container()
    overview = ListCards(c.uow, c.clock).execute()
    table = Table("Cartão", "Fecha / vence", "Limite", "Comprometido", "Disponível", "Uso")
    for view in overview.cards:
        usage, account = view.usage, view.account
        table.add_row(
            account.nickname,
            f"dia {account.closing_day} / dia {account.due_day}",
            format_brl(usage.limit_cents)
            if usage.limit_cents is not None
            else "limite não informado",
            format_brl(usage.committed_cents),
            format_brl(usage.available_cents) if usage.available_cents is not None else "—",
            f"{format_percent(usage.percent / 100 if usage.percent is not None else None)} "
            f"· {messages.LIMIT_ALERT_LABELS[usage.alert]}",
        )
    console.print(table)
    console.print(f"Faturas a pagar (fechadas): {format_brl(overview.closed_to_pay_cents)}")
    console.print(f"Faturas abertas: {format_brl(overview.open_total_cents)}")
    console.print(f"Parcelas futuras: {format_brl(overview.future_installments_cents)}")


def _print_preview(preview, with_total: bool = True) -> None:
    console.print(messages.explain_assignment(preview.assignment))
    console.print(messages.best_day_hint(preview.assignment))
    table = Table("Parcela", "Fatura", "Fecha", "Vence", "Valor", title="Cronograma das parcelas")
    for line in preview.lines:
        table.add_row(
            f"{line.number}/{line.installment_total}",
            format_month_short(line.statement_month),
            format_date_short(line.closing_date),
            format_date_short(line.due_date),
            format_brl(line.amount_cents),
        )
    console.print(table)
    if with_total:
        console.print(f"Total das parcelas geradas: {format_brl(preview.total_cents)}")


@card_app.command("buy")
@handle_errors
def card_buy(
    description: Annotated[str, typer.Argument(help="Descrição da compra.")],
    card: Annotated[str, typer.Option("--card", help="Apelido do cartão.")],
    date: Annotated[
        str | None, typer.Option("--date", "-d", help="Data da compra (dd/mm/aaaa).")
    ] = None,
    total: Annotated[str | None, typer.Option("--total", help="Valor total da compra.")] = None,
    installment_value: Annotated[
        str | None, typer.Option("--installment-value", help="Valor da parcela, como na fatura.")
    ] = None,
    installments: Annotated[int, typer.Option("--installments", "-n", help="Nº de parcelas.")] = 1,
    current: Annotated[
        int, typer.Option("--current", help="Parcela atual (compra em andamento).")
    ] = 1,
    statement: Annotated[
        str | None, typer.Option("--statement", help="Fatura da parcela atual (AAAA-MM).")
    ] = None,
    category: Annotated[str | None, typer.Option("--category", "-c", help="Categoria.")] = None,
    recurring: Annotated[bool, typer.Option("--recurring", "-r", help="Recorrente.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Salvar sem perguntar.")] = False,
) -> None:
    """Lança uma compra no cartão (à vista, parcelada ou em andamento) e mostra o cronograma."""
    c = container()
    chosen = find_card(c.uow, card)
    cmd = CardPurchaseCommand(
        account_id=chosen.id,
        description=description,
        purchased_on=parse_date(date, c.clock.today()) if date else None,
        category_id=find_category(c.uow, category).id if category else None,
        installments=installments,
        current_installment=current,
        total_cents=_optional_money(total),
        installment_cents=_optional_money(installment_value),
        statement_month=YearMonth.parse(statement) if statement else None,
        is_recurring=recurring,
    )
    _print_preview(PreviewCardPurchase(c.uow).execute(cmd))
    if not yes and not typer.confirm("Salvar esta compra?"):
        console.print("Nada foi salvo.")
        raise typer.Exit(0)
    result = RegisterCardPurchase(c.uow).execute(cmd)
    console.print(
        f"Compra salva: {len(result.transactions)} lançamento(s) no cartão {chosen.nickname}."
    )


@card_app.command("installments")
@handle_errors
def card_installments(
    card: Annotated[str | None, typer.Option("--card", help="Apelido do cartão.")] = None,
) -> None:
    """Parcelas ativas: quantas faltam e quanto ainda há a pagar."""
    c = container()
    account_id = find_card(c.uow, card).id if card else None
    table = Table("Compra", "Pagas", "Restam", "A pagar", "Próxima fatura")
    for view in ListActiveInstallments(c.uow, c.clock).execute(account_id):
        table.add_row(
            view.plan.description,
            f"{view.paid_count}/{view.plan.installment_total}",
            str(view.remaining_count),
            format_brl(view.to_pay_cents),
            format_month_short(view.next_statement_month) if view.next_statement_month else "—",
        )
    console.print(table)


# --- statements -------------------------------------------------------------------------------


def _status_text(view) -> str:
    return messages.STATEMENT_STATUS_LABELS[view.status]


@statement_app.command("list")
@handle_errors
def statement_list(
    card: Annotated[str | None, typer.Option("--card", help="Apelido do cartão.")] = None,
) -> None:
    """Faturas dos cartões: situação, total, pago e a pagar."""
    c = container()
    overview = ListCards(c.uow, c.clock).execute()
    table = Table("Cartão", "Fatura", "Situação", "Total", "Pago", "A pagar")
    for view in overview.cards:
        if card and find_card(c.uow, card).id != view.account.id:
            continue
        for s in view.statements:
            table.add_row(
                view.account.nickname,
                messages.statement_label(s.statement),
                _status_text(s),
                format_brl(s.total_cents),
                format_brl(s.paid_cents),
                format_brl(s.outstanding_cents),
            )
    console.print(table)


@statement_app.command("show")
@handle_errors
def statement_show(
    card: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    month: Annotated[str, typer.Argument(help="Mês de fechamento (AAAA-MM).")],
) -> None:
    """Detalhe de uma fatura: lançamentos e conferência com o banco."""
    c = container()
    _, statement = find_statement(c.uow, card, month)
    detail = GetStatementDetail(c.uow, c.clock).execute(statement.id)
    view = detail.view
    console.print(f"[bold]{messages.statement_label(statement)}[/bold] · {_status_text(view)}")
    console.print(
        f"Total lançado {format_brl(view.total_cents)} · pago {format_brl(view.paid_cents)}"
        f" · a pagar {format_brl(view.outstanding_cents)}"
    )
    rec = view.reconciliation
    if rec.informed_cents is not None and rec.difference_cents is not None:
        console.print(
            f"Conferência: lançado {format_brl(rec.entered_cents)} · informado pelo banco "
            f"{format_brl(rec.informed_cents)} · diferença {format_brl(rec.difference_cents)}"
        )
    table = Table("Data", "Descrição", "Parcela", "Valor")
    for t in detail.entries:
        installment = f"{t.installment_number}" if t.installment_number else "—"
        table.add_row(
            format_date(t.posted_on),
            t.description or messages.TRANSACTION_KIND_LABELS[t.kind],
            installment,
            format_brl(t.amount_cents),
        )
    console.print(table)


@statement_app.command("pay")
@handle_errors
def statement_pay(
    card: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    month: Annotated[str, typer.Argument(help="Mês de fechamento (AAAA-MM).")],
    from_account: Annotated[str, typer.Option("--from", help="Conta corrente que paga.")],
    amount: Annotated[
        str | None, typer.Option("--amount", help="Valor (padrão: o que falta).")
    ] = None,
    date: Annotated[str, typer.Option("--date", "-d", help="Data do pagamento.")] = "hoje",
) -> None:
    """Paga uma fatura (transferência da conta para o cartão). Aceita pagamento parcial."""
    c = container()
    _, statement = find_statement(c.uow, card, month)
    origin = find_account(c.uow, from_account)
    legs = PayStatement(c.uow, c.clock).execute(
        PayStatementCommand(
            statement.id,
            origin.id,
            parse_date(date, c.clock.today()),
            _optional_money(amount),
            f"Pagamento da fatura {format_month_short(statement.month)}",
        )
    )
    console.print(f"Pagamento de {format_brl(legs[1].amount_cents)} registrado.")


@statement_app.command("inform")
@handle_errors
def statement_inform(
    card: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    month: Annotated[str, typer.Argument(help="Mês de fechamento (AAAA-MM).")],
    amount: Annotated[str | None, typer.Argument(help="Total que o banco mostra.")] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Remove o total informado.")] = False,
) -> None:
    """Informa o total da fatura segundo o banco, para conferir com os lançamentos."""
    c = container()
    _, statement = find_statement(c.uow, card, month)
    InformStatementTotal(c.uow).execute(
        statement.id, None if clear else (parse_brl(amount) if amount else None)
    )
    console.print("Total informado atualizado.")


@statement_app.command("difference")
@handle_errors
def statement_difference(
    card: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    month: Annotated[str, typer.Argument(help="Mês de fechamento (AAAA-MM).")],
) -> None:
    """Lança a diferença da conferência como um gasto “Não categorizado”."""
    c = container()
    _, statement = find_statement(c.uow, card, month)
    entry = PostStatementDifference(c.uow).execute(statement.id, "Diferença de conferência")
    console.print(f"Diferença lançada: {format_brl(-entry.amount_cents)}.")


@statement_app.command("dates")
@handle_errors
def statement_dates(
    card: Annotated[str, typer.Argument(help="Apelido do cartão.")],
    month: Annotated[str, typer.Argument(help="Mês de fechamento (AAAA-MM).")],
    closing: Annotated[str, typer.Option("--closing", help="Nova data de fechamento.")],
    due: Annotated[str, typer.Option("--due", help="Nova data de vencimento.")],
) -> None:
    """Corrige as datas de fechamento e vencimento de uma fatura."""
    c = container()
    _, statement = find_statement(c.uow, card, month)
    today = c.clock.today()
    updated = SetStatementDates(c.uow).execute(
        statement.id, parse_date(closing, today), parse_date(due, today)
    )
    console.print(messages.statement_label(updated))
