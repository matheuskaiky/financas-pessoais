"""FastAPI + Jinja2 + HTMX adapter. All user-visible text is pt-BR (templates and messages)."""

import datetime as dt
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from financas.application.queries.balances import ListAccountBalances
from financas.application.queries.summary import GetSummary, Period, summarize
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
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
from financas.container import Container
from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    CategoryGroup,
    CategoryKind,
    TransactionKind,
)
from financas.domain.money import YearMonth, format_brl, parse_brl
from financas.domain.services.images import MAX_IMAGE_BYTES
from financas.domain.services.text import normalize_search
from financas.interfaces import appearance, messages
from financas.interfaces.formatting import (
    format_date,
    format_date_short,
    format_month,
    format_month_long,
    format_percent,
    format_signed,
    parse_date,
)

HERE = Path(__file__).parent
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSP = (
    "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)
_GROUP_COLORS = {
    "essential": "#1E395F",
    "non_essential": "#6FA3C7",
    "charges": "#B3261E",
    "review": "#8A5A00",
}
_ENTRY_KINDS = (TransactionKind.EXPENSE, TransactionKind.INCOME, TransactionKind.REFUND)


def _color(color: str | None, use_color: str | None) -> str | None:
    """A color input always sends a value, so a checkbox says whether to use it."""
    return color if use_color else None


async def _upload(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(MAX_IMAGE_BYTES + 1)  # one byte more than allowed: detects "too large"
    return data or None


def create_app(c: Container) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = c
    templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    allowed_hosts = _LOCAL_HOSTS | {c.settings.host}

    templates.env.filters.update(
        brl=format_brl,
        date=format_date,
        date_short=format_date_short,
        signed=format_signed,
        month_short=format_month,
        percent=format_percent,
    )
    templates.env.globals.update(
        kind_labels=messages.TRANSACTION_KIND_LABELS,
        group_labels=messages.CATEGORY_GROUP_LABELS,
        category_kind_labels=messages.CATEGORY_KIND_LABELS,
        account_kind_labels=messages.ACCOUNT_KIND_LABELS,
    )

    # --- security: this app is local, but a web page open in the browser must not drive it ------

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "")
        hostname = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if hostname not in allowed_hosts:  # DNS rebinding
            return Response("Host não permitido.", status_code=400)
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site")
            cross_origin = (origin is not None and urlsplit(origin).netloc != host) or (
                fetch_site is not None and fetch_site not in {"same-origin", "none"}
            )
            if cross_origin:  # CSRF
                return Response("Origem não permitida.", status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # --- shared page helpers ---------------------------------------------------------------------

    def today() -> dt.date:
        return c.clock.today()

    def backup_label() -> str:
        last = c.last_backup_date()
        if last is None:
            return "Nenhum backup ainda"
        age = (today() - last).days
        return "Último backup: hoje" if age == 0 else f"Último backup: há {age} dia(s)"

    def backup_notice() -> str | None:
        last = c.last_backup_date()
        if last is None:
            return "Você ainda não fez backup. Use o botão “Fazer backup”."
        age = (today() - last).days
        if age > c.settings.backup_warn_days:
            return f"O último backup foi há {age} dias. Faça um novo backup."
        return None

    def render(
        request: Request,
        template: str,
        context: dict[str, object],
        *,
        error: DomainError | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        ok = request.query_params.get("ok")
        notice = messages.FLASH_MESSAGES.get(ok) if ok else None
        if ok == "balance":
            notice = _balance_notice(request) or notice
        context = {
            **context,
            "error": messages.render_error(error) if error else None,
            "notice": notice,
            "backup_notice": backup_notice(),
            "backup_label": backup_label(),
            "nav": template.split(".")[0],
        }
        return templates.TemplateResponse(
            request, template, context, status_code=400 if error else status
        )

    def _balance_notice(request: Request) -> str | None:
        diff = request.query_params.get("diff")
        computed = request.query_params.get("computed")
        try:
            if diff is not None and computed is not None:
                return (
                    f"Saldo registrado. O sistema esperava {format_brl(int(computed))}; "
                    f"diferença de {format_brl(int(diff))} "
                    "(lançamentos faltando ou rendimento)."
                )
        except ValueError:
            return None
        return None

    def back(path: str, ok: str | None = None, **query: object) -> RedirectResponse:
        params = {**({"ok": ok} if ok else {}), **query}
        suffix = f"?{urlencode(params)}" if params else ""
        return RedirectResponse(path + suffix, status_code=303)

    def lookups() -> dict[str, object]:
        with c.uow as work:
            institutions = {
                i.id: i
                for i in sorted(
                    work.institutions.list_all(), key=lambda i: normalize_search(i.name)
                )
            }
            accounts = sorted(work.accounts.list_all(), key=lambda a: normalize_search(a.nickname))
            categories = sorted(work.categories.list_all(), key=lambda x: normalize_search(x.name))
        return {
            "institutions": institutions,
            "accounts": accounts,
            "categories": categories,
            "account_looks": {
                a.id: appearance.account_look(a, institutions[a.institution_id]) for a in accounts
            },
            "institution_looks": {
                i.id: appearance.institution_look(i) for i in institutions.values()
            },
            "category_colors": {
                x.id: x.color or appearance.default_color(x.slug) for x in categories
            },
        }

    # --- dashboard -----------------------------------------------------------------------------

    def attention_items(data: dict[str, object]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        notice = backup_notice()
        if notice:
            items.append({"tag": "Backup", "text": notice, "href": "/#backup"})
        for b in ListAccountBalances(c.uow).execute(today()):
            if b.balance_cents is None:
                name = next(a.nickname for a in data["accounts"] if a.id == b.account_id)  # type: ignore[attr-defined]
                items.append(
                    {
                        "tag": "Saldo",
                        "text": f"{name}: saldo indisponível. Informe um saldo para ver o valor.",
                        "href": "/accounts",
                    }
                )
        return items

    def dashboard_context(year: int, month: int) -> dict[str, object]:
        period = Period.year(year) if month == 0 else Period.month(YearMonth(year, month))
        queries = GetSummary(c.uow)
        summary = queries.execute(period)
        monthly = queries.months_of_year(year)
        data = lookups()
        with c.uow as work:
            recurring = [
                t
                for t in work.transactions.list_between(period.start, period.end)
                if t.kind is TransactionKind.EXPENSE and t.is_recurring
            ]
        recurring.sort(key=lambda t: t.amount_cents)  # most negative (biggest) first
        categories = {x.id: x for x in data["categories"]}  # type: ignore[attr-defined]
        title = str(year) if month == 0 else format_month_long(YearMonth(year, month))
        top = summary.by_category[0].total_cents if summary.by_category else 0
        return {
            **data,
            "summary": summary,
            "title": title,
            "year": year,
            "month": month,
            "years": sorted({today().year - 3 + n for n in range(5)} | {year}),
            "month_names": list(enumerate(_MONTH_NAMES, start=1)),
            "category_by_id": categories,
            "category_bar": {
                r.category_id: (r.total_cents / top * 100) if top else 0
                for r in summary.by_category
            },
            "recurring": recurring[:5],
            "recurring_count": len(recurring),
            "attention": attention_items(data),
            "group_colors": _GROUP_COLORS,
            "chart_months": [format_month(YearMonth.from_date(m.period.start)) for m in monthly],
            "chart_income": [m.income_cents / 100 for m in monthly],
            "chart_expenses": [m.net_expenses_cents / 100 for m in monthly],
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, year: int | None = None, month: int | None = None):
        now = today()
        year = year if year and 1900 <= year <= 9999 else now.year
        month = month if month is not None and 0 <= month <= 12 else now.month
        return render(request, "dashboard.html", dashboard_context(year, month))

    # --- entries -------------------------------------------------------------------------------

    PAGE_SIZE = 50

    def entries_context(
        month: str | None,
        filters: dict[str, str],
        form: dict[str, str] | None = None,
        limit: int = PAGE_SIZE,
    ) -> dict[str, object]:
        try:
            ym = YearMonth.parse(month) if month else YearMonth.from_date(today())
        except DomainError:
            ym = YearMonth.from_date(today())
        period = Period.month(ym)
        data = lookups()
        categories = {x.id: x for x in data["categories"]}  # type: ignore[attr-defined]
        with c.uow as work:
            month_rows = work.transactions.list_between(period.start, period.end)
            recent = work.transactions.list_between(today() - dt.timedelta(days=120), today())
        wanted = normalize_search(filters.get("q", ""))
        rows = [
            t
            for t in month_rows
            if (not filters.get("account") or t.account_id == filters["account"])
            and (not filters.get("kind") or t.kind.value == filters["kind"])
            and (not filters.get("category") or t.category_id == filters["category"])
            and (not wanted or wanted in t.description_search)
        ]
        summary = summarize(month_rows, categories, period)
        accounts = data["accounts"]
        entry_accounts = [
            a
            for a in accounts  # type: ignore[attr-defined]
            if a.is_active and a.kind is AccountKind.CHECKING
        ]
        transfer_accounts = [
            a
            for a in accounts  # type: ignore[attr-defined]
            if a.is_active and a.kind is not AccountKind.CREDIT_CARD
        ]
        last_used = next(
            (t.account_id for t in recent if t.kind is not TransactionKind.TRANSFER), None
        )
        default_account = (
            last_used
            if last_used in {a.id for a in entry_accounts}
            else (entry_accounts[0].id if len(entry_accounts) == 1 else "")
        )
        form = form or {}
        kind = form.get("kind", "expense")
        return {
            **data,
            "rows": rows[:limit],
            "row_count": len(rows),
            "month_count": len(month_rows),
            "next_limit": limit + PAGE_SIZE,
            "summary": summary,
            "month_value": str(ym),
            "month_label": format_month_long(ym),
            "prev_month": str(ym.add_months(-1)),
            "next_month": str(ym.add_months(1)),
            "filters": filters,
            "entry_accounts": entry_accounts,
            "transfer_accounts": transfer_accounts,
            "category_by_id": categories,
            "form": {
                "kind": kind,
                "account_id": form.get("account_id", default_account),
                "from_account": form.get("from_account", default_account),
                "to_account": form.get("to_account", ""),
                "date": form.get("date", today().isoformat()),
                "amount": form.get("amount", ""),
                "description": form.get("description", ""),
                "category_id": form.get("category_id", ""),
                "recurring": form.get("recurring", ""),
                "notes": form.get("notes", ""),
            },
            "category_options": category_options("expense" if kind == "transfer" else kind),
            "entry_kinds": _ENTRY_KINDS,
        }

    def category_options(kind: str) -> list[object]:
        wanted = CategoryKind.NEUTRAL if kind == "refund" else CategoryKind(kind)
        with c.uow as work:
            options = [x for x in work.categories.list_all() if x.kind is wanted]
        return sorted(options, key=lambda x: normalize_search(x.name))

    @app.get("/entries", response_class=HTMLResponse)
    def entries(
        request: Request,
        month: str | None = None,
        account: str = "",
        kind: str = "",
        category: str = "",
        q: str = "",
        limit: int = PAGE_SIZE,
    ):
        filters = {"account": account, "kind": kind, "category": category, "q": q}
        return render(request, "entries.html", entries_context(month, filters, limit=limit))

    @app.get("/entries/category-field", response_class=HTMLResponse)
    def category_field(
        request: Request, kind: str = "expense", description: str = "", category_id: str = ""
    ):
        try:
            transaction_kind = TransactionKind(kind)
            if transaction_kind is TransactionKind.TRANSFER:
                raise ValueError
        except ValueError:
            return Response("Tipo inválido.", status_code=400)
        suggestion = SuggestCategory(c.uow).execute(description, transaction_kind)
        options = category_options(kind)
        chosen = (
            category_id
            if category_id in {o.id for o in options}  # type: ignore[attr-defined]
            else (suggestion.id if suggestion else "")
        )
        return templates.TemplateResponse(
            request,
            "_category_field.html",
            {
                "category_options": options,
                "form": {"category_id": chosen},
                "suggested": bool(suggestion and chosen == suggestion.id),
            },
        )

    @app.post("/entries")
    def add_entry(
        request: Request,
        kind: Annotated[str, Form()],
        date: Annotated[str, Form()],
        amount: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        account_id: Annotated[str, Form()] = "",
        from_account: Annotated[str, Form()] = "",
        to_account: Annotated[str, Form()] = "",
        category_id: Annotated[str, Form()] = "",
        recurring: Annotated[str, Form()] = "",
        notes: Annotated[str, Form()] = "",
    ):
        form = {k: v for k, v in locals().items() if isinstance(v, str)}
        try:
            if kind == TransactionKind.TRANSFER.value:
                RegisterTransfer(c.uow).execute(
                    RegisterTransferCommand(
                        from_account or None,
                        to_account or None,
                        parse_date(date, today()),
                        parse_brl(amount),
                        description,
                        notes or None,
                    )
                )
                return back("/entries", "transfer")
            RegisterTransaction(c.uow).execute(
                RegisterTransactionCommand(
                    account_id=account_id,
                    posted_on=parse_date(date, today()),
                    kind=TransactionKind(kind),
                    amount_cents=parse_brl(amount),
                    description=description,
                    category_id=category_id or None,
                    is_recurring=bool(recurring),
                    notes=notes or None,
                )
            )
        except DomainError as error:
            return render(request, "entries.html", entries_context(None, {}, form), error=error)
        return back("/entries", "entry")

    @app.post("/transfers")
    def add_transfer(
        request: Request,
        amount: Annotated[str, Form()],
        date: Annotated[str, Form()],
        from_account: Annotated[str, Form()] = "",
        to_account: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
    ):
        try:
            RegisterTransfer(c.uow).execute(
                RegisterTransferCommand(
                    from_account or None,
                    to_account or None,
                    parse_date(date, today()),
                    parse_brl(amount),
                    description,
                )
            )
        except DomainError as error:
            return render(request, "entries.html", entries_context(None, {}), error=error)
        return back("/entries", "transfer")

    @app.post("/entries/{transaction_id}/delete")
    def delete_entry(request: Request, transaction_id: str):
        try:
            DeleteTransaction(c.uow).execute(transaction_id)
        except DomainError as error:
            return render(request, "entries.html", entries_context(None, {}), error=error)
        if request.headers.get("hx-request"):
            return Response(status_code=200, headers={"HX-Refresh": "true"})
        return back("/entries", "deleted")

    # --- accounts and institutions -------------------------------------------------------------

    def accounts_context() -> dict[str, object]:
        data = lookups()
        balances = {b.account_id: b for b in ListAccountBalances(c.uow).execute(today())}
        return {
            **data,
            "balances": balances,
            "today": today().isoformat(),
            "kinds": list(AccountKind),
        }

    @app.get("/accounts", response_class=HTMLResponse)
    def accounts(request: Request):
        return render(request, "accounts.html", accounts_context())

    @app.post("/institutions")
    async def add_institution(
        request: Request,
        name: Annotated[str, Form()],
        group: Annotated[str, Form()] = "",
        color: Annotated[str, Form()] = "",
        use_color: Annotated[str, Form()] = "",
        image: Annotated[UploadFile | None, File()] = None,
    ):
        try:
            chosen = _color(color, use_color)
            institution = CreateInstitution(c.uow).execute(
                CreateInstitutionCommand(name=name, group_slug=group or None, color=chosen)
            )
            data = await _upload(image)
            if data is not None:
                SetAppearance(c.uow, c.images).execute(
                    SetAppearanceCommand(
                        AppearanceTarget.INSTITUTION, institution.id, color=chosen, image=data
                    )
                )
        except DomainError as error:
            return render(request, "accounts.html", accounts_context(), error=error)
        return back("/accounts", "institution")

    @app.post("/accounts")
    async def add_account(
        request: Request,
        nickname: Annotated[str, Form()],
        institution_id: Annotated[str, Form()],
        kind: Annotated[str, Form()] = "checking",
        color: Annotated[str, Form()] = "",
        use_color: Annotated[str, Form()] = "",
        image: Annotated[UploadFile | None, File()] = None,
    ):
        try:
            chosen = _color(color, use_color)
            account = CreateAccount(c.uow).execute(
                CreateAccountCommand(AccountKind(kind), institution_id, nickname, color=chosen)
            )
            data = await _upload(image)
            if data is not None:
                SetAppearance(c.uow, c.images).execute(
                    SetAppearanceCommand(
                        AppearanceTarget.ACCOUNT, account.id, color=chosen, image=data
                    )
                )
        except DomainError as error:
            return render(request, "accounts.html", accounts_context(), error=error)
        return back("/accounts", "account")

    @app.post("/appearance/{target}/{entity_id}")
    async def set_appearance(
        request: Request,
        target: AppearanceTarget,
        entity_id: str,
        color: Annotated[str, Form()] = "",
        use_color: Annotated[str, Form()] = "",
        remove_image: Annotated[str, Form()] = "",
        image: Annotated[UploadFile | None, File()] = None,
    ):
        page = "categories" if target is AppearanceTarget.CATEGORY else "accounts"
        try:
            SetAppearance(c.uow, c.images).execute(
                SetAppearanceCommand(
                    target,
                    entity_id,
                    color=_color(color, use_color),
                    image=await _upload(image),
                    remove_image=bool(remove_image),
                )
            )
        except DomainError as error:
            ctx = categories_context() if page == "categories" else accounts_context()
            return render(request, f"{page}.html", ctx, error=error)
        return back(f"/{page}", "appearance")

    @app.post("/accounts/{account_id}/active")
    def set_active(account_id: str, request: Request, active: Annotated[str, Form()] = ""):
        try:
            SetAccountActive(c.uow).execute(account_id, bool(active))
        except DomainError as error:
            return render(request, "accounts.html", accounts_context(), error=error)
        return back("/accounts", "account")

    @app.post("/accounts/{account_id}/balance")
    def set_balance(
        request: Request,
        account_id: str,
        date: Annotated[str, Form()],
        amount: Annotated[str, Form()],
        note: Annotated[str, Form()] = "",
    ):
        try:
            result = RecordBalance(c.uow).execute(
                RecordBalanceCommand(
                    account_id, parse_date(date, today()), parse_brl(amount), note or None
                )
            )
        except DomainError as error:
            return render(request, "accounts.html", accounts_context(), error=error)
        if result.computed_cents is not None and result.difference_cents is not None:
            return back(
                "/accounts",
                "balance",
                computed=result.computed_cents,
                diff=result.difference_cents,
            )
        return back("/accounts", "balance")

    # --- categories ----------------------------------------------------------------------------

    def categories_context() -> dict[str, object]:
        return {**lookups(), "groups": list(CategoryGroup), "kinds": list(CategoryKind)}

    @app.get("/categories", response_class=HTMLResponse)
    def categories(request: Request):
        return render(request, "categories.html", categories_context())

    @app.post("/categories")
    def add_category(
        request: Request,
        name: Annotated[str, Form()],
        group: Annotated[str, Form()],
        kind: Annotated[str, Form()],
        budget: Annotated[str, Form()] = "",
        color: Annotated[str, Form()] = "",
        use_color: Annotated[str, Form()] = "",
    ):
        try:
            CreateCategory(c.uow).execute(
                CreateCategoryCommand(
                    name=name,
                    group=CategoryGroup(group),
                    kind=CategoryKind(kind),
                    monthly_budget_cents=parse_brl(budget) if budget.strip() else None,
                    color=_color(color, use_color),
                )
            )
        except DomainError as error:
            return render(request, "categories.html", categories_context(), error=error)
        return back("/categories", "category")

    # --- images and backup ---------------------------------------------------------------------

    @app.get("/images/{image_id}")
    def image(image_id: str):
        found = c.images.open(image_id)
        if found is None:
            return Response("Imagem não encontrada.", status_code=404)
        data, image_type = found
        return Response(
            data,
            media_type=f"image/{image_type}",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/backup")
    def backup():
        c.backup()
        return back("/", "backup")

    return app


_MONTH_NAMES = [n.capitalize() for n in (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)]  # fmt: skip
