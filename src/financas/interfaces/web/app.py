"""FastAPI + Jinja2 + HTMX adapter. All user-visible text is pt-BR (templates and messages)."""

import datetime as dt
import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from financas.application.queries.balances import ListAccountBalances
from financas.application.queries.cards import (
    GetStatementDetail,
    InstallmentSchedule,
    ListActiveInstallments,
    ListCards,
    StatementView,
)
from financas.application.queries.investments import (
    GetFixedIncomeOverview,
    GetInvestmentPeriodTotals,
    GetNetWorth,
    GetYearEndPosition,
    ListHoldings,
    ListInvestments,
)
from financas.application.queries.merchants import ListMerchants
from financas.application.queries.plan_purchases import ListPlanPurchases
from financas.application.queries.planning import (
    BudgetRange,
    GetBudget,
    GetDailyFlow,
    GetRecurring,
)
from financas.application.queries.review import CountPendingReview
from financas.application.queries.summary import GetSummary, Period
from financas.application.use_cases.balances import RecordBalance, RecordBalanceCommand
from financas.application.use_cases.budget import SetCategoryBudgets
from financas.application.use_cases.cards import (
    AdjustInstallment,
    CardPurchaseCommand,
    DeletePurchase,
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
    SetInvestmentSettings,
)
from financas.application.use_cases.holdings import (
    RecordHoldingValuation,
    RecordHoldingValuationCommand,
    RedeemHolding,
    RedeemHoldingCommand,
    RegisterHolding,
    RegisterHoldingCommand,
    SetHoldingFlags,
)
from financas.application.use_cases.investments import (
    FlowDirection,
    RegisterInvestmentFlow,
    RegisterInvestmentFlowCommand,
)
from financas.application.use_cases.transactions import (
    DeleteTransaction,
    RegisterTransaction,
    RegisterTransactionCommand,
    RegisterTransfer,
    RegisterTransferCommand,
    SplitItem,
    SuggestCategory,
)
from financas.container import Container
from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    AssetClass,
    Category,
    CategoryGroup,
    CategoryKind,
    Indexer,
    InstrumentType,
    InvestmentTracking,
    Liquidity,
    RateMode,
    StatementStatus,
    Transaction,
    TransactionKind,
)
from financas.domain.money import YearMonth, format_brl, parse_brl
from financas.domain.services.images import MAX_IMAGE_BYTES, detect_image_type
from financas.domain.services.splits import allocations, split_ids
from financas.domain.services.text import normalize_search
from financas.interfaces import appearance, messages
from financas.interfaces.formatting import (
    format_date,
    format_date_short,
    format_day_header,
    format_day_label,
    format_decimal_comma,
    format_month,
    format_month_long,
    format_percent,
    format_signed,
    parse_date,
    parse_percent_bps,
)
from financas.interfaces.web import fp_charts, fp_money, nav
from financas.interfaces.web.payment_dates import payment_window
from financas.interfaces.web.routes import MODULES, WebContext
from financas.interfaces.web.shared import Lookups
from financas.interfaces.web.shared import enum_of as _enum
from financas.interfaces.web.shared import int_of as _int
from financas.interfaces.web.shared import opt_int as _opt_int

HERE = Path(__file__).parent
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSP = (
    "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)
_ENTRY_KINDS = (TransactionKind.EXPENSE, TransactionKind.INCOME, TransactionKind.REFUND)


def _color(color: str | None, use_color: str | None) -> str | None:
    """A color input always sends a value, so a checkbox says whether to use it."""
    return color if use_color else None


async def _upload(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(MAX_IMAGE_BYTES + 1)  # one byte more than allowed: detects "too large"
    return data or None


_PREFILL_FIELDS = {
    "f_kind": "kind",
    "f_amount": "amount",
    "f_description": "description",
    "f_date": "date",
    "f_category": "category_id",
}


def _prefill_from_query(query: dict[str, str]) -> dict[str, str]:
    """The quick-form fields a ``?fill=1&f_*`` link carries (unknown or empty ones are dropped)."""
    form = {field: query[key][:300] for key, field in _PREFILL_FIELDS.items() if query.get(key)}
    if account := query.get("f_account"):
        form["account_id"] = account
        form["from_account"] = account  # a transfer reads "De" from the same choice
    return form


def _is_browser_noise(path: str) -> bool:
    """Requests browsers make on their own (devtools, source maps): not failures of the system."""
    return path.startswith("/.well-known/") or path.endswith(".map")


def _money_parts(cents: int) -> tuple[str, str, str]:
    """``(sign, whole, cents)`` of an amount, for big serif figures: ``("-", "1.234", "56")``."""
    whole, frac = divmod(abs(cents), 100)
    return ("−" if cents < 0 else "", f"{whole:,}".replace(",", "."), f"{frac:02d}")


def create_app(c: Container) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = c
    templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    allowed_hosts = _LOCAL_HOSTS | {c.settings.host}

    templates.env.filters.update(
        brl=format_brl,
        money_parts=_money_parts,
        date=format_date,
        date_short=format_date_short,
        day_label=format_day_label,
        day_title=lambda day: format_day_header(day, c.clock.today()),
        signed=format_signed,
        month_short=format_month,
        percent=format_percent,
    )
    # `brl` stays plain text (attributes, aria-label, option labels). `money` writes the .money
    # markup the privacy mode blurs; `currency`/`amount` are text forms; `config` feeds the head.
    fp_money.install(templates.env, locale="pt-BR", currency="BRL")
    fp_charts.install(templates.env)  # after fp_money: cx_* filters of the v3 patch
    templates.env.globals["card_text_color"] = appearance.card_text_color
    templates.env.globals.update(
        kind_labels=messages.TRANSACTION_KIND_LABELS,
        group_labels=messages.CATEGORY_GROUP_LABELS,
        category_kind_labels=messages.CATEGORY_KIND_LABELS,
        account_kind_labels=messages.ACCOUNT_KIND_LABELS,
        decimal=format_decimal_comma,
        explain=messages.explain_assignment,
        best_day=messages.best_day_hint,
        statement_label=messages.statement_label,
        closing_countdown=messages.closing_countdown,
        due_countdown=messages.due_countdown,
        alert_labels=messages.ALERT_KIND_LABELS,
        describe_alert=messages.describe_alert,
        asset_labels=messages.ASSET_CLASS_LABELS,
        instrument_labels=messages.INSTRUMENT_TYPE_LABELS,
        indexer_labels=messages.INDEXER_LABELS,
        rate_mode_labels=messages.RATE_MODE_LABELS,
        liquidity_labels=messages.LIQUIDITY_LABELS,
        bucket_labels=messages.LIQUIDITY_BUCKET_LABELS,
        tracking_labels=messages.TRACKING_LABELS,
        holding_status_labels=messages.HOLDING_STATUS_LABELS,
        rate=messages.format_rate,
        status_labels=messages.STATEMENT_STATUS_LABELS,
        limit_labels=messages.LIMIT_ALERT_LABELS,
        nav_groups=nav.groups,
        nav_count=lambda key: CountPendingReview(c.uow).execute() if key == "review" else 0,
        nav_current=nav.resolve,
        nav_more_groups=nav.more_groups,
        nav_in_more=nav.in_more,
        assistant_enabled=False,  # CLAUDE.md section 16: the assistant is not implemented yet
        demo_mode=c.settings.demo,  # synthetic data (``financas demo``): the pages say so
    )

    # --- security: this app is local, but a web page open in the browser must not drive it ------

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = c.failures.new_id()
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
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/static/"):
            # no-cache = revalidate (ETag); else an old script may be reused (heuristic freshness)
            response.headers.setdefault("Cache-Control", "no-cache")
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
        err = request.query_params.get("err")
        if err in messages.PARAMETERLESS_ERRORS and error is None:
            error = DomainError(err)
        ok = request.query_params.get("ok")
        notice = messages.FLASH_MESSAGES.get(ok) if ok else None
        if ok == "balance":
            notice = _balance_notice(request) or notice
        if ok == "valuation_yield":
            notice = _valuation_notice(request) or messages.FLASH_MESSAGES["valuation"]
        context = {
            **context,
            "error": messages.render_error(error) if error else None,
            "notice": notice,
            "backup_notice": backup_notice(),
            "backup_label": backup_label(),
            "nav": context.get("nav") or template.split(".")[0],
        }
        return templates.TemplateResponse(
            request, template, context, status_code=400 if error else status
        )

    def _valuation_notice(request: Request) -> str | None:
        try:
            diff = int(request.query_params.get("diff", ""))
        except ValueError:
            return None
        return (
            "Avaliação registrada. Rendimento desde a avaliação anterior, descontadas as "
            f"movimentações: {format_signed(diff)} (não é renda)."
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

    def lookups() -> Lookups:
        with c.uow as work:
            institutions = {
                i.id: i
                for i in sorted(
                    work.institutions.list_all(), key=lambda i: normalize_search(i.name)
                )
            }
            accounts = sorted(work.accounts.list_all(), key=lambda a: normalize_search(a.nickname))
            categories = sorted(work.categories.list_all(), key=lambda x: normalize_search(x.name))
        return Lookups(
            institutions=institutions,
            accounts=accounts,
            categories=categories,
            account_looks={
                a.id: appearance.account_look(a, institutions[a.institution_id]) for a in accounts
            },
            institution_looks={i.id: appearance.institution_look(i) for i in institutions.values()},
            category_colors={x.id: x.color or appearance.default_color(x.slug) for x in categories},
        )

    # pt-BR pages for framework errors (the defaults are English JSON)

    def failure_code(request: Request) -> str:
        return str(getattr(request.state, "request_id", "")) or c.failures.new_id()

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        code = c.failures.record_exception(
            "server",
            "server_error",
            exc,
            id=failure_code(request),
            method=request.method,
            path=request.url.path,
            status=500,
        )
        response = render(
            request,
            "error.html",
            {
                "heading": "Algo deu errado",
                "detail": "O sistema não conseguiu concluir a ação. Nada foi perdido.",
                "failure_code": code,
            },
            status=500,
        )
        response.headers["X-Request-ID"] = code  # built outside the guard middleware
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = _CSP
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path != "/favicon.ico" and not _is_browser_noise(request.url.path):
            c.failures.record(
                "server",
                "http_error",
                id=failure_code(request),
                method=request.method,
                path=request.url.path,
                status=exc.status_code,
            )
        heading, detail = {
            404: ("Página não encontrada", "Esse endereço não existe neste sistema."),
            405: ("Ação não permitida", "Esse endereço não aceita esta ação."),
        }.get(exc.status_code, ("Algo deu errado", "Não foi possível concluir a ação."))
        return render(
            request, "error.html", {"heading": heading, "detail": detail}, status=exc.status_code
        )

    @app.exception_handler(DomainError)
    async def unexpected_domain_error(request: Request, exc: DomainError):
        return render(
            request,
            "error.html",
            {"heading": "Não foi possível", "detail": messages.render_error(exc)},
            status=400,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return render(
            request,
            "error.html",
            {"heading": "Confira os campos", "detail": "Algum campo está faltando ou inválido."},
            status=400,
        )

    # The dashboard (/) and /analises live in routes/charts.py (design v3, work package 2).

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
        categories = {x.id: x for x in data["categories"]}
        with c.uow as work:
            # refunded entries are listed (struck through) but the summary below never counts them
            month_rows = work.transactions.list_between(
                period.start, period.end, include_refunded=True
            )
            recent = work.transactions.list_between(today() - dt.timedelta(days=120), today())
            splits = work.transactions.splits_for(split_ids(month_rows))
        wanted = normalize_search(filters.get("q", ""))
        # an installment purchase is ONE row, on its purchase date (the card screen keeps the
        # monthly installments); its figures come from the plan (queries/plan_purchases.py)
        purchases = {p.plan.id: p for p in ListPlanPurchases(c.uow).execute()}
        in_month = [p for p in purchases.values() if period.start <= p.purchased_on <= period.end]
        plan_rows = [p.as_entry() for p in in_month]
        plan_by_anchor = {p.anchor.id: p for p in in_month}
        plain_rows = [t for t in month_rows if t.plan_id not in purchases]
        month_rows = sorted(
            [*plain_rows, *plan_rows], key=lambda t: t.posted_on, reverse=True
        )  # stable: the order inside a day is kept

        def category_matches(t: Transaction) -> bool:
            plan_purchase = plan_by_anchor.get(t.id)
            if plan_purchase is not None:
                return filters["category"] in plan_purchase.category_ids
            # an itemized entry matches the category of the entry or of any of its items
            return (
                filters["category"] in {cid for cid, _ in allocations(t, splits)}
                or t.category_id == filters["category"]
            )

        rows = [
            t
            for t in month_rows
            if (not filters.get("account") or t.account_id == filters["account"])
            and (not filters.get("kind") or t.kind.value == filters["kind"])
            and (not filters.get("category") or category_matches(t))
            and (
                not wanted
                or wanted in t.description_search
                or wanted in normalize_search(t.merchant or "")
            )
        ]
        summary = GetSummary(c.uow).execute(period)  # card entries count in the statement month
        accounts = data["accounts"]
        entry_accounts = [
            a
            for a in accounts  # type: ignore[attr-defined]
            if a.is_active and a.kind in (AccountKind.CHECKING, AccountKind.CREDIT_CARD)
        ]
        checking_accounts = [a for a in entry_accounts if a.kind is AccountKind.CHECKING]
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
            else (checking_accounts[0].id if len(checking_accounts) == 1 else "")
        )
        form = form or {}
        kind = form.get("kind", "expense")
        return {
            **data,
            "rows": rows[:limit],
            "splits": splits,
            "plan_purchases": plan_by_anchor,
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
                "merchant": form.get("merchant", ""),
            },
            "merchants": ListMerchants(c.uow).execute(),
            "category_options": category_options("expense" if kind == "transfer" else kind),
            "entry_kinds": _ENTRY_KINDS,
        }

    def category_options(kind: str) -> list[Category]:
        try:
            wanted = CategoryKind.NEUTRAL if kind == "refund" else CategoryKind(kind)
        except ValueError:
            wanted = CategoryKind.EXPENSE
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
        limit: str = "",
    ):
        filters = {"account": account, "kind": kind, "category": category, "q": q}
        # ⌘K "registrar por frase": ?fill=1&f_kind=..&f_amount=.. pre-fills the quick form (a draft:
        # nothing is saved until the user submits the form, and the kind is still the user's choice)
        prefill = (
            _prefill_from_query(dict(request.query_params))
            if "fill" in request.query_params
            else None
        )
        return render(
            request,
            "entries.html",
            entries_context(month, filters, prefill, limit=_opt_int(limit) or PAGE_SIZE),
        )

    @app.get("/entries/category-field", response_class=HTMLResponse)
    def category_field(
        request: Request, kind: str = "expense", description: str = "", category_id: str = ""
    ):
        if kind == TransactionKind.TRANSFER.value:
            return Response(status_code=204)  # a transfer has no category field: nothing to swap
        try:
            transaction_kind = TransactionKind(kind)
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
        merchant: Annotated[str, Form()] = "",
    ):
        form = {k: v for k, v in locals().items() if isinstance(v, str)}
        try:
            if kind != TransactionKind.TRANSFER.value and not account_id:
                raise DomainError("ACCOUNT_REQUIRED")
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
                    kind=_enum(TransactionKind, kind),
                    amount_cents=parse_brl(amount),
                    description=description,
                    category_id=category_id or None,
                    is_recurring=bool(recurring),
                    notes=notes or None,
                    merchant=merchant or None,
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
            DeleteTransaction(c.uow, c.clock).execute(transaction_id)
        except DomainError as error:
            # HTMX does not swap 4xx pages: go back to the list with the message in the URL
            target = f"/entries?{urlencode({'err': error.code})}"
            if request.headers.get("hx-request"):
                return Response(status_code=200, headers={"HX-Redirect": target})
            return RedirectResponse(target, status_code=303)
        if request.headers.get("hx-request"):
            return Response(status_code=200, headers={"HX-Refresh": "true"})
        return back("/entries", "deleted")

    # --- accounts and institutions -------------------------------------------------------------

    def accounts_context() -> dict[str, object]:
        data = lookups()
        balances = {b.account_id: b for b in ListAccountBalances(c.uow).execute(today())}
        data["accounts"] = [a for a in data["accounts"] if a.kind is not AccountKind.CREDIT_CARD]
        return {
            **data,
            "balances": balances,
            "today": today().isoformat(),
            "kinds": [k for k in AccountKind if k is not AccountKind.CREDIT_CARD],
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
            data = await _upload(image)
            if data is not None:
                detect_image_type(data)  # reject a bad image before anything is created
            institution = CreateInstitution(c.uow).execute(
                CreateInstitutionCommand(name=name, group_slug=group or None, color=chosen)
            )
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
        opening_balance: Annotated[str, Form()] = "",
        opening_date: Annotated[str, Form()] = "",
        image: Annotated[UploadFile | None, File()] = None,
    ):
        try:
            chosen = _color(color, use_color)
            data = await _upload(image)
            if data is not None:
                detect_image_type(data)
            account = CreateAccount(c.uow).execute(
                CreateAccountCommand(
                    _enum(AccountKind, kind),
                    institution_id,
                    nickname,
                    color=chosen,
                    opening_balance_cents=_money(opening_balance),
                    opening_balance_on=_iso_date(opening_date),
                )
            )
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

    # --- cards ---------------------------------------------------------------------------------

    def _money(text: str) -> int | None:
        return parse_brl(text) if text.strip() else None

    def _iso_date(text: str) -> dt.date | None:
        return parse_date(text, today()) if text.strip() else None

    def pick_statement(views: list[StatementView], month: str | None) -> StatementView | None:
        """The statement asked for, else the open one, else the oldest unpaid, else the newest."""
        if not views:
            return None
        if month:
            for v in views:
                if str(v.statement.month) == month:
                    return v
        by_status = {s: [v for v in views if v.status is s] for s in StatementStatus}
        for status in (StatementStatus.OPEN, StatementStatus.CLOSED):
            if by_status[status]:
                return by_status[status][0]
        return views[-1]

    def cards_context(
        card_id: str | None, month: str | None, ano: str | None = None
    ) -> dict[str, object]:
        overview = ListCards(c.uow, c.clock).execute()
        data = lookups()
        chosen = next((v for v in overview.cards if v.account.id == card_id), None) or (
            overview.cards[0] if overview.cards else None
        )
        # Year filter: the statements of one year at a time. ``?ano=`` (lenient) wins, then the
        # year of ``?month=``, then the year of the statement the default rule picks.
        years: list[int] = []
        year: int | None = None
        shown = chosen.statements if chosen else []
        if chosen and chosen.statements:
            years = sorted({v.statement.month.year for v in chosen.statements})
            wanted: int | None = None
            if ano and ano.strip().isdigit() and int(ano) in years:
                wanted = int(ano)
            elif month and month[:4].isdigit() and int(month[:4]) in years:
                wanted = int(month[:4])
            if wanted is None:
                default = pick_statement(chosen.statements, month)
                wanted = default.statement.month.year if default else years[-1]
            year = wanted
            shown = [v for v in chosen.statements if v.statement.month.year == year]
        statement = pick_statement(shown, month) if chosen else None
        detail = (
            GetStatementDetail(c.uow, c.clock).execute(statement.statement.id)
            if statement
            else None
        )
        checking = [a for a in data["accounts"] if a.kind is AccountKind.CHECKING and a.is_active]
        with c.uow as work:
            plans = {p.id: p for p in work.plans.list_all()}
            splits = work.transactions.splits_for(split_ids(detail.entries)) if detail else {}
        return {
            **data,
            "plans": plans,
            "splits": splits,
            "overview": overview,
            "chosen": chosen,
            "years": years,
            "year": year,
            "shown_statements": shown,
            "statement": statement,
            "detail": detail,
            "category_by_id": {x.id: x for x in data["categories"]},
            "checking_accounts": checking,
            "payment_bounds": payment_window(c.uow, statement.statement, today())
            if statement
            else None,
            "merchants": ListMerchants(c.uow).execute(),
            "installments": ListActiveInstallments(c.uow, c.clock).execute(chosen.account.id)
            if chosen
            else [],
            "schedule": InstallmentSchedule(c.uow, c.clock).execute(chosen.account.id)
            if chosen
            else [],
            "today": today().isoformat(),
            "institution_choices": list(data["institutions"].values()),
        }

    @app.get("/cards", response_class=HTMLResponse)
    def cards(
        request: Request,
        card: str | None = None,
        month: str | None = None,
        ano: str | None = None,
    ):
        return render(request, "cards.html", cards_context(card, month, ano))

    @app.post("/cards")
    async def add_card(
        request: Request,
        nickname: Annotated[str, Form()],
        institution_id: Annotated[str, Form()],
        closing_days_before_due: Annotated[str, Form()],
        due_day: Annotated[str, Form()],
        limit: Annotated[str, Form()] = "",
        color: Annotated[str, Form()] = "",
        use_color: Annotated[str, Form()] = "",
        image: Annotated[UploadFile | None, File()] = None,
    ):
        try:
            chosen = _color(color, use_color)
            data = await _upload(image)
            if data is not None:
                detect_image_type(data)
            card = CreateAccount(c.uow).execute(
                CreateAccountCommand(
                    AccountKind.CREDIT_CARD,
                    institution_id,
                    nickname,
                    color=chosen,
                    closing_days_before_due=_int(
                        closing_days_before_due, "INVALID_DAYS_BEFORE_DUE"
                    ),
                    due_day=_int(due_day, "INVALID_CARD_DAY"),
                    credit_limit_cents=_money(limit),
                )
            )
            data = await _upload(image)
            if data is not None:
                SetAppearance(c.uow, c.images).execute(
                    SetAppearanceCommand(
                        AppearanceTarget.ACCOUNT, card.id, color=chosen, image=data
                    )
                )
        except DomainError as error:
            return render(request, "cards.html", cards_context(None, None), error=error)
        return back("/cards", "card", card=card.id)

    @app.post("/cards/{card_id}/settings")
    def card_settings(
        request: Request,
        card_id: str,
        closing_days_before_due: Annotated[str, Form()],
        due_day: Annotated[str, Form()],
        limit: Annotated[str, Form()] = "",
    ):
        try:
            SetCardSettings(c.uow, c.clock).execute(
                card_id,
                _int(closing_days_before_due, "INVALID_DAYS_BEFORE_DUE"),
                _int(due_day, "INVALID_CARD_DAY"),
                _money(limit),
            )
        except DomainError as error:
            return render(request, "cards.html", cards_context(card_id, None), error=error)
        return back("/cards", "card_settings", card=card_id)

    def statement_redirect(statement_id: str, ok: str) -> RedirectResponse:
        with c.uow as work:
            statement = work.statements.get(statement_id)
        assert statement is not None
        return back("/cards", ok, card=statement.account_id, month=str(statement.month))

    def statement_error(request: Request, statement_id: str, error: DomainError) -> HTMLResponse:
        with c.uow as work:
            statement = work.statements.get(statement_id)
        card = statement.account_id if statement else None
        month = str(statement.month) if statement else None
        return render(request, "cards.html", cards_context(card, month), error=error)

    @app.post("/statements/{statement_id}/pay")
    def pay_statement(
        request: Request,
        statement_id: str,
        from_account: Annotated[str, Form()],
        date: Annotated[str, Form()],
        amount: Annotated[str, Form()] = "",
    ):
        try:
            with c.uow as work:
                statement = work.statements.get(statement_id)
            label = format_month(statement.month) if statement else ""
            PayStatement(c.uow, c.clock).execute(
                PayStatementCommand(
                    statement_id,
                    from_account,
                    parse_date(date, today()),
                    _money(amount),
                    f"Pagamento da fatura {label}",
                )
            )
        except DomainError as error:
            return statement_error(request, statement_id, error)
        return statement_redirect(statement_id, "payment")

    @app.post("/statements/{statement_id}/informed")
    def inform_statement(request: Request, statement_id: str, amount: Annotated[str, Form()] = ""):
        try:
            InformStatementTotal(c.uow).execute(statement_id, _money(amount))
        except DomainError as error:
            return statement_error(request, statement_id, error)
        return statement_redirect(statement_id, "informed")

    @app.post("/statements/{statement_id}/difference")
    def post_difference(request: Request, statement_id: str):
        try:
            PostStatementDifference(c.uow).execute(statement_id, "Diferença de conferência")
        except DomainError as error:
            return statement_error(request, statement_id, error)
        return statement_redirect(statement_id, "difference")

    @app.post("/statements/{statement_id}/dates")
    def statement_dates(
        request: Request,
        statement_id: str,
        closing: Annotated[str, Form()],
        due: Annotated[str, Form()],
    ):
        try:
            SetStatementDates(c.uow).execute(
                statement_id, parse_date(closing, today()), parse_date(due, today())
            )
        except DomainError as error:
            return statement_error(request, statement_id, error)
        return statement_redirect(statement_id, "dates")

    @app.post("/entries/{transaction_id}/amount")
    def adjust_amount(request: Request, transaction_id: str, amount: Annotated[str, Form()]):
        with c.uow as work:
            entry = work.transactions.get(transaction_id)
            statement = (
                work.statements.get(entry.statement_id) if entry and entry.statement_id else None
            )
        try:
            AdjustInstallment(c.uow, c.clock).execute(transaction_id, parse_brl(amount))
        except DomainError as error:
            if statement:
                return statement_error(request, statement.id, error)
            return render(request, "cards.html", cards_context(None, None), error=error)
        assert statement is not None
        return statement_redirect(statement.id, "adjusted")

    @app.post("/plans/{plan_id}/delete")
    def delete_plan(request: Request, plan_id: str):
        try:
            DeletePurchase(c.uow, c.clock).execute(plan_id)
        except DomainError as error:
            return render(request, "cards.html", cards_context(None, None), error=error)
        return back("/cards", "plan_deleted")

    # purchase form with a live schedule

    def purchase_months() -> list[tuple[str, str]]:
        first = YearMonth.from_date(today()).add_months(-3)
        return [(str(m), format_month(m)) for m in (first.add_months(n) for n in range(0, 20))]

    def purchase_context(
        form: dict[str, str] | None = None, items: list[dict[str, str]] | None = None
    ) -> dict[str, object]:
        data = lookups()
        form = form or {}
        card_list = [
            a for a in data["accounts"] if a.kind is AccountKind.CREDIT_CARD and a.is_active
        ]
        expense_categories = [x for x in data["categories"] if x.kind is CategoryKind.EXPENSE]
        return {
            **data,
            "card_list": card_list,
            "expense_categories": expense_categories,
            "months": purchase_months(),
            "form_items": items or [],
            "merchants": ListMerchants(c.uow).execute(),
            "form": {
                "account_id": form.get(
                    "account_id", card_list[0].id if len(card_list) == 1 else ""
                ),
                "date": form.get("date", today().isoformat()),
                "description": form.get("description", ""),
                "category_id": form.get("category_id", ""),
                "amount": form.get("amount", ""),
                "amount_mode": form.get("amount_mode", "total"),
                "installments": form.get("installments", "1"),
                "current_installment": form.get("current_installment", "1"),
                "statement_month": form.get("statement_month", ""),
                "recurring": form.get("recurring", ""),
                "merchant": form.get("merchant", ""),
            },
        }

    def typed_items(data: FormData) -> list[dict[str, str]]:
        """The rows of the items editor, as typed (rows left empty are dropped)."""
        rows = zip(
            data.getlist("split_descriptions"),
            data.getlist("split_categories"),
            data.getlist("split_amounts"),
            strict=False,
        )
        return [
            {"description": str(d), "category_id": str(k), "amount": str(a)}
            for d, k, a in rows
            if str(d).strip() or str(a).strip()
        ]

    def purchase_command(
        form: dict[str, str], items: list[dict[str, str]] | None = None
    ) -> CardPurchaseCommand:
        amount = parse_brl(form.get("amount", "")) if form.get("amount", "").strip() else None
        by_installment = form.get("amount_mode") == "installment"
        count = _int(form.get("installments") or "1", "INVALID_INSTALLMENT_COUNT")
        return CardPurchaseCommand(
            account_id=form.get("account_id", ""),
            description=form.get("description", ""),
            purchased_on=parse_date(form["date"], today()) if form.get("date") else None,
            category_id=form.get("category_id") or None,
            installments=count,
            current_installment=_int(
                form.get("current_installment") or "1", "INVALID_INSTALLMENT_COUNT"
            ),
            total_cents=None if by_installment else amount,
            installment_cents=amount if by_installment else None,
            statement_month=YearMonth.parse(form["statement_month"])
            if form.get("statement_month")
            else None,
            is_recurring=bool(form.get("recurring")),
            merchant=form.get("merchant") or None,
            splits=tuple(
                SplitItem(i["description"], i["category_id"], abs(parse_brl(i["amount"])))
                for i in (items or [])
            )
            if form.get("splits_present")
            else (),
        )

    @app.get("/cards/purchase", response_class=HTMLResponse)
    def purchase_form(request: Request, card: str = ""):
        return render(
            request, "purchase.html", purchase_context({"account_id": card} if card else None)
        )

    @app.post("/cards/purchase/preview", response_class=HTMLResponse)
    async def purchase_preview(request: Request):
        form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            preview = PreviewCardPurchase(c.uow).execute(purchase_command(form))
        except (DomainError, ValueError) as error:
            text = (
                messages.render_error(error)
                if isinstance(error, DomainError)
                else "Confira os campos."
            )
            return templates.TemplateResponse(
                request, "_purchase_preview.html", {"preview": None, "problem": text}
            )
        usage = next(
            (
                v.usage
                for v in ListCards(c.uow, c.clock).execute().cards
                if v.account.id == form.get("account_id")
            ),
            None,
        )
        return templates.TemplateResponse(
            request,
            "_purchase_preview.html",
            {
                "preview": preview,
                "problem": None,
                "usage": usage,
                "limit_labels": messages.LIMIT_ALERT_LABELS,
            },
        )

    @app.post("/cards/purchase")
    async def purchase_save(request: Request):
        data = await request.form()
        form = {k: str(v) for k, v in data.items()}
        items = typed_items(data)
        try:
            result = RegisterCardPurchase(c.uow).execute(purchase_command(form, items))
        except DomainError as error:
            return render(request, "purchase.html", purchase_context(form, items), error=error)
        except ValueError:
            return render(
                request,
                "purchase.html",
                purchase_context(form, items),
                error=DomainError("INVALID_AMOUNT"),
            )
        first = result.transactions[0]
        with c.uow as work:
            statement = work.statements.get(first.statement_id or "")
        assert statement is not None
        return back("/cards", "purchase", card=statement.account_id, month=str(statement.month))

    # --- investments and net worth -------------------------------------------------------------

    def investments_context(year: int | None) -> dict[str, object]:
        chosen = year if year and 1900 <= year <= 9999 else today().year
        data = lookups()
        overview = ListInvestments(c.uow, c.clock, c.settings.valuation_stale_days).execute()
        this_month = Period.month(YearMonth.from_date(today()))
        accounts = data["accounts"]
        return {
            **data,
            "overview": overview,
            "year": chosen,
            "years": sorted({today().year - 5 + n for n in range(7)} | {chosen}),
            "year_totals": GetInvestmentPeriodTotals(c.uow).execute(Period.year(chosen)),
            "month_totals": GetInvestmentPeriodTotals(c.uow).execute(this_month),
            "month_name": _MONTH_NAMES[today().month - 1].lower(),
            "position": GetYearEndPosition(c.uow).execute(chosen),
            "investment_accounts": [a for a in accounts if a.kind is AccountKind.INVESTMENT],  # type: ignore[attr-defined]
            "checking_accounts": [
                a
                for a in accounts  # type: ignore[attr-defined]
                if a.kind is AccountKind.CHECKING and a.is_active
            ],
            "asset_classes": list(AssetClass),
            "trackings": list(InvestmentTracking),
            "holdings": ListHoldings(c.uow, c.clock, c.settings.valuation_stale_days).execute(
                include_redeemed=True
            ),
            "fixed": GetFixedIncomeOverview(
                c.uow, c.clock, c.settings.valuation_stale_days, c.settings.fgc_limit_cents
            ).execute(),
            "instrument_types": list(InstrumentType),
            "indexers": list(Indexer),
            "rate_modes": list(RateMode),
            "liquidities": list(Liquidity),
            "issuers": list(data["institutions"].values()),
            "today": today().isoformat(),
        }

    @app.get("/investments", response_class=HTMLResponse)
    def investments(request: Request, year: str = ""):
        return render(request, "investments.html", investments_context(_opt_int(year)))

    @app.post("/investments/flow")
    def investment_flow(
        request: Request,
        direction: Annotated[str, Form()],
        amount: Annotated[str, Form()],
        date: Annotated[str, Form()],
        investment_account_id: Annotated[str, Form()] = "",
        holding_id: Annotated[str, Form()] = "",
        target: Annotated[str, Form()] = "",
        other_account_id: Annotated[str, Form()] = "",
    ):
        """``target`` is ``a:<account id>`` (account level) or ``h:<holding id>`` (one holding)."""
        try:
            if target.startswith("h:"):
                with c.uow as work:
                    held = work.holdings.get(target[2:])
                if held is None:
                    raise DomainError("NOT_FOUND", entity="holding")
                investment_account_id, holding_id = held.account_id, held.id
            elif target.startswith("a:"):
                investment_account_id = target[2:]
            RegisterInvestmentFlow(c.uow).execute(
                RegisterInvestmentFlowCommand(
                    investment_account_id,
                    _enum(FlowDirection, direction),
                    parse_date(date, today()),
                    parse_brl(amount),
                    other_account_id or None,
                    holding_id=holding_id or None,
                )
            )
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        return back("/investments", "flow")

    @app.post("/investments/{account_id}/valuation")
    def investment_valuation(
        request: Request,
        account_id: str,
        date: Annotated[str, Form()],
        net: Annotated[str, Form()],
        gross: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ):
        try:
            result = RecordBalance(c.uow).execute(
                RecordBalanceCommand(
                    account_id,
                    parse_date(date, today()),
                    parse_brl(net),
                    note or None,
                    gross_balance_cents=parse_brl(gross) if gross.strip() else None,
                )
            )
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        if result.difference_cents is not None:
            return back("/investments", "valuation_yield", diff=result.difference_cents)
        return back("/investments", "valuation")

    @app.post("/investments/{account_id}/settings")
    def investment_settings(
        request: Request,
        account_id: str,
        asset_class: Annotated[str, Form()],
        emergency: Annotated[str, Form()] = "",
        tracking: Annotated[str, Form()] = "",
    ):
        try:
            SetInvestmentSettings(c.uow).execute(
                account_id,
                _enum(AssetClass, asset_class),
                bool(emergency),
                _enum(InvestmentTracking, tracking) if tracking else None,
            )
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        return back("/investments", "investment_settings")

    def _optional_date(text: str) -> dt.date | None:
        return parse_date(text, today()) if text.strip() else None

    @app.post("/investments/holdings")
    def add_holding(
        request: Request,
        account_id: Annotated[str, Form()],
        name: Annotated[str, Form()],
        instrument_type: Annotated[str, Form()],
        issuer_id: Annotated[str, Form()],
        applied_on: Annotated[str, Form()],
        principal: Annotated[str, Form()],
        liquidity: Annotated[str, Form()],
        maturity_on: Annotated[str, Form()] = "",
        liquid_from: Annotated[str, Form()] = "",
        indexer: Annotated[str, Form()] = "",
        rate_mode: Annotated[str, Form()] = "",
        rate: Annotated[str, Form()] = "",
        fgc: Annotated[str, Form()] = "",
        emergency: Annotated[str, Form()] = "",
        contribute: Annotated[str, Form()] = "",
        from_account_id: Annotated[str, Form()] = "",
    ):
        try:
            RegisterHolding(c.uow).execute(
                RegisterHoldingCommand(
                    account_id=account_id,
                    name=name,
                    instrument_type=_enum(InstrumentType, instrument_type),
                    issuer_id=issuer_id,
                    applied_on=parse_date(applied_on, today()),
                    principal_cents=parse_brl(principal),
                    liquidity=_enum(Liquidity, liquidity),
                    indexer=_enum(Indexer, indexer) if indexer else None,
                    rate_mode=_enum(RateMode, rate_mode) if rate_mode else None,
                    rate_bps=parse_percent_bps(rate) if rate.strip() else None,
                    maturity_on=_optional_date(maturity_on),
                    liquid_from=_optional_date(liquid_from),
                    fgc_covered={"yes": True, "no": False}.get(fgc),
                    is_emergency_fund=bool(emergency),
                    contribute=bool(contribute),
                    from_account_id=from_account_id or None,
                )
            )
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        return back("/investments", "holding")

    @app.post("/investments/holdings/{holding_id}/valuation")
    def holding_valuation(
        request: Request,
        holding_id: str,
        date: Annotated[str, Form()],
        net: Annotated[str, Form()],
        gross: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ):
        try:
            result = RecordHoldingValuation(c.uow).execute(
                RecordHoldingValuationCommand(
                    holding_id,
                    parse_date(date, today()),
                    parse_brl(net),
                    parse_brl(gross) if gross.strip() else None,
                    note or None,
                )
            )
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        if result.difference_cents is not None:
            return back("/investments", "valuation_yield", diff=result.difference_cents)
        return back("/investments", "valuation")

    @app.post("/investments/holdings/{holding_id}/flags")
    def holding_flags(
        request: Request,
        holding_id: str,
        fgc: Annotated[str, Form()] = "",
        emergency: Annotated[str, Form()] = "",
    ):
        try:
            SetHoldingFlags(c.uow).execute(holding_id, bool(fgc), bool(emergency))
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        return back("/investments", "holding_flags")

    @app.post("/investments/holdings/{holding_id}/redeem")
    def redeem_holding(
        request: Request,
        holding_id: str,
        date: Annotated[str, Form()],
        amount: Annotated[str, Form()],
        to_account_id: Annotated[str, Form()] = "",
    ):
        try:
            RedeemHolding(c.uow).execute(
                RedeemHoldingCommand(
                    holding_id,
                    parse_date(date, today()),
                    parse_brl(amount),
                    to_account_id or None,
                )
            )
        except DomainError as error:
            return render(request, "investments.html", investments_context(None), error=error)
        return back("/investments", "redeemed")

    @app.get("/networth", response_class=HTMLResponse)
    def networth(request: Request):
        context = {**lookups(), "net_worth": GetNetWorth(c.uow, c.clock).execute()}
        return render(request, "networth.html", context)

    @app.get("/more", response_class=HTMLResponse)
    def more(request: Request):
        return render(request, "more.html", {})

    # --- failures: what the browser reports and what the log holds -----------------------------

    @app.post("/client-errors")
    async def client_errors(request: Request) -> Response:
        """The page reports its own failures here (see ``static/app.js``). Always 204: a report
        that is rejected, too big or rate limited is simply dropped; it never shows an error."""
        body = await request.body()
        if len(body) <= 4096:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                c.failures.accept_client_report(
                    payload,  # pyright: ignore[reportUnknownArgumentType]
                    request.headers.get("user-agent", ""),
                )
        return Response(status_code=204)

    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics(request: Request):
        return render(
            request,
            "diagnostics.html",
            {
                "failures": c.failures.recent(100),
                "kind_labels": messages.FAILURE_KIND_LABELS,
                "log_path": "data/logs/failures.jsonl",
            },
        )

    # --- budget, recurring and daily flow ------------------------------------------------------

    def _sane_year(value: str) -> int:
        number = _opt_int(value)
        return number if number and 1900 <= number <= 2200 else today().year

    def budget_context(range_name: str, year: str) -> dict[str, object]:
        chosen_year = _sane_year(year)
        budget_range = BudgetRange.YEAR if range_name == "year" else BudgetRange.LAST_3_MONTHS
        view = GetBudget(c.uow, c.clock).execute(budget_range, chosen_year)
        data = lookups()
        return {
            **data,
            "view": view,
            "budget": view.budget,
            "range_name": budget_range.value,
            "year": chosen_year,
            "years": sorted({today().year - 3 + n for n in range(5)} | {chosen_year}),
            "category_by_id": {x.id: x for x in data["categories"]},
            "expense_categories": [x for x in data["categories"] if x.kind is CategoryKind.EXPENSE],
            "editing": False,
        }

    @app.get("/budget", response_class=HTMLResponse)
    def budget(request: Request, range: str = "", year: str = "", edit: str = ""):
        context = budget_context(range, year)
        context["editing"] = bool(edit)
        return render(request, "budget.html", context)

    @app.post("/budget/goals")
    async def save_goals(request: Request):
        form = {k: str(v) for k, v in (await request.form()).items()}
        range_name, year = form.get("range", ""), form.get("year", "")
        try:
            with c.uow as work:
                known = {x.id for x in work.categories.list_all()}
            goals: dict[str, int | None] = {}
            for key, text in form.items():
                category_id = key.removeprefix("goal_")
                if key.startswith("goal_") and category_id in known:
                    goals[category_id] = parse_brl(text) if text.strip() else None
            SetCategoryBudgets(c.uow).execute(goals)  # all or nothing
        except DomainError as error:
            context = budget_context(range_name, year)
            context["editing"] = True
            context["submitted"] = {k.removeprefix("goal_"): v for k, v in form.items()}
            return render(request, "budget.html", context, error=error)
        query = {k: v for k, v in (("range", range_name), ("year", year)) if v}
        return back("/budget", "budget", **query)

    @app.get("/recurring", response_class=HTMLResponse)
    def recurring(request: Request):
        view = GetRecurring(c.uow, c.clock).execute()
        data = lookups()
        return render(
            request,
            "recurring.html",
            {
                **data,
                "view": view,
                "last_closed": view.current_month.add_months(-1),
                "category_by_id": {x.id: x for x in data["categories"]},
            },
        )

    @app.get("/accounts/{account_id}/flow", response_class=HTMLResponse)
    def account_flow(request: Request, account_id: str, month: str = ""):
        try:
            ym = YearMonth.parse(month) if month else YearMonth.from_date(today())
            if not 1900 <= ym.year <= 2200:
                raise DomainError("INVALID_YEAR_MONTH")
        except DomainError:
            ym = YearMonth.from_date(today())
        try:
            flow = GetDailyFlow(c.uow).execute(account_id, Period.month(ym))
        except DomainError as error:
            return render(request, "accounts.html", accounts_context(), error=error)
        return render(
            request,
            "flow.html",
            {
                **lookups(),
                "flow": flow,
                "month_value": str(ym),
                "month_label": format_month_long(ym),
                "prev_month": str(ym.add_months(-1)),
                "next_month": str(ym.add_months(1)),
            },
        )

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
                    group=_enum(CategoryGroup, group),
                    kind=_enum(CategoryKind, kind),
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

    # --- route modules (routes/): added by the work packages, after everything above ------------

    ctx = WebContext(
        c=c,
        render=render,
        today=today,
        lookups=lookups,
        templates=templates,
        back=back,
        enum_of=_enum,
        int_of=_int,
        opt_int=_opt_int,
        money=_money,
        iso_date=_iso_date,
    )
    for module in MODULES:
        module.register(app, ctx)

    return app


_MONTH_NAMES = [n.capitalize() for n in (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)]  # fmt: skip
