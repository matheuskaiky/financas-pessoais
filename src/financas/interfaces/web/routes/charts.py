"""Painel (``/``), Análises (``/analises``) and the JSON behind their charts (design v3, package 2).

JSON endpoints (``/api/charts/...``) return only integer cents and ISO dates; the pages draw them
with ``static/charts.js``. Everything is computed by the queries in
``application/queries/charts.py``; this module only shapes the answers and the page contexts.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from financas.application.queries.balances import ListAccountBalances
from financas.application.queries.cards import ListCards, ListMonthPurchases
from financas.application.queries.charts import (
    CategoryBreakdown,
    CategorySlice,
    GetCashFlow,
    GetMonthPace,
    GetNetWorthSeries,
    breakdown_of,
    pending_items,
)
from financas.application.queries.investments import GetNetWorth, ListInvestments
from financas.application.queries.planning import GetRecurring
from financas.application.queries.summary import GetSummary, Period, Summary
from financas.domain.errors import DomainError
from financas.domain.models import StatementStatus, TransactionKind
from financas.domain.money import YearMonth, format_brl
from financas.interfaces import messages
from financas.interfaces.formatting import (
    format_date,
    format_month,
    format_month_long,
    format_percent,
)
from financas.interfaces.web import nav
from financas.interfaces.web.routes import WebContext
from financas.interfaces.web.shared import Lookups

_GROUP_COLORS = {
    "essential": "#0A2E24",
    "non_essential": "#8C9A92",
    "charges": "#A94A58",
    "review": "#8A5A00",
}
_MONTH_NAMES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


def parse_month(month: str | None, year: str | None, default: YearMonth) -> YearMonth:
    """``?month=2026-09`` (also the old ``?year=2026&month=9``); anything else is the default."""
    try:
        if month and "-" in month:
            return YearMonth.parse(month.strip())
        if month and month.strip().isdigit() and year and year.strip().isdigit():
            return YearMonth(int(year), int(month))
    except DomainError:
        pass
    return default


def parse_year(year: str | None, default: int) -> int:
    try:
        value = int(year or "")
    except ValueError:
        return default
    return value if 1900 <= value <= 9999 else default


def _slice_json(row: CategorySlice, lookups: Lookups, names: dict[str, Any]) -> dict[str, Any]:
    category = names[row.category_id]
    return {
        "key": category.slug,
        "name": category.name,
        "group": row.group.value,
        "group_label": messages.CATEGORY_GROUP_LABELS[row.group],
        "count": row.count,
        "cents": row.total_cents,
        "color": lookups["category_colors"][row.category_id],
    }


def categories_json(
    breakdown: CategoryBreakdown, month: YearMonth, lookups: Lookups
) -> dict[str, Any]:
    names = {c.id: c for c in lookups["categories"]}
    rest = (
        {
            "categories": len(breakdown.rest),
            "entries": breakdown.rest_count,
            "cents": breakdown.rest_total_cents,
            "items": [_slice_json(r, lookups, names) for r in breakdown.rest],
        }
        if breakdown.rest
        else None
    )
    return {
        "month": str(month),
        "total_cents": breakdown.total_cents,
        "entry_count": breakdown.entry_count,
        "category_count": len(breakdown.top) + len(breakdown.rest),
        "slices": [_slice_json(r, lookups, names) for r in breakdown.top],
        "rest": rest,
    }


def register(app: FastAPI, ctx: WebContext) -> None:
    c = ctx.c

    nav.register(
        nav.NavEntry(
            id="analises",
            label="Análises",
            icon=nav.ICONS["analises"],
            href="/analises",
            group="main",
            order=20,
            hint="Patrimônio, ritmo do mês, fluxo de caixa e categorias",
        )
    )

    def nav_entry(entry_id: str) -> nav.NavEntry | None:
        return next((e for e in nav.entries() if e.id == entry_id), None)

    # Package 4 registers "carta"; the Painel shows its pill only while that entry exists.
    ctx.templates.env.globals["nav_entry"] = nav_entry

    def current_month() -> YearMonth:
        return YearMonth.from_date(ctx.today())

    # --- JSON ------------------------------------------------------------------------------------

    @app.get("/api/charts/net-worth")
    def net_worth_json() -> JSONResponse:
        result = GetNetWorthSeries(c.uow, c.clock).execute()
        return JSONResponse(
            {
                "today": ctx.today().isoformat(),
                "first_date": result.first_date.isoformat() if result.first_date else None,
                "points": [{"date": p.on.isoformat(), "cents": p.cents} for p in result.points],
                "partial": result.partial,
                "pending": [{"id": p.id, "name": p.name, "kind": p.kind} for p in result.pending],
                "future_installments_cents": result.future_installments_cents,
                "min_points": 8,
            }
        )

    @app.get("/api/charts/net-worth-composition")
    def composition_json() -> JSONResponse:
        view = GetNetWorth(c.uow, c.clock).execute()
        return JSONResponse(
            {
                "net_worth_cents": view.net_worth_cents,
                "cash_cents": view.cash_cents,
                "investments_cents": view.investments_cents,
                "statements_cents": view.closed_statements_cents + view.open_statements_cents,
                "closed_statements_cents": view.closed_statements_cents,
                "open_statements_cents": view.open_statements_cents,
                "future_installments_cents": view.future_installments_cents,
                "partial": view.is_partial,
                "pending": [
                    {"id": p.id, "name": p.name, "kind": p.kind} for p in pending_items(view)
                ],
            }
        )

    @app.get("/api/charts/pace")
    def pace_json(month: str = "", year: str = "") -> JSONResponse:
        chosen = parse_month(month, year, current_month())
        pace = GetMonthPace(c.uow, c.clock).execute(chosen)
        return JSONResponse(
            {
                "month": str(pace.month),
                "days": pace.days,
                "elapsed_days": pace.elapsed_days,
                "daily_cents": pace.daily_cents,
                "daily_counts": pace.daily_counts,
                "cumulative_cents": pace.cumulative_cents,
                "average_cents": pace.average_cents or None,
                "average_months": [str(m) for m in pace.average_months],
                "ceiling_cents": pace.ceiling_cents,
                "crossed_day": pace.crossed_day,
                "total_cents": pace.total_cents,
                "entry_count": pace.entry_count,
            }
        )

    @app.get("/api/charts/cash-flow")
    def cash_flow_json(year: str = "") -> JSONResponse:
        chosen = parse_year(year, ctx.today().year)
        flow = GetCashFlow(c.uow, c.clock).execute(chosen)
        return JSONResponse(
            {
                "year": flow.year,
                "months": [
                    {
                        "month": str(m.month),
                        "income_cents": m.income_cents,
                        "expenses_cents": m.expenses_cents,
                        "refunds_cents": m.refunds_cents,
                        "balance_cents": m.balance_cents,
                    }
                    for m in flow.months
                ],
            }
        )

    @app.get("/api/charts/categories")
    def categories_endpoint(month: str = "", year: str = "") -> JSONResponse:
        chosen = parse_month(month, year, current_month())
        summary = GetSummary(c.uow).execute(Period.month(chosen))
        return JSONResponse(categories_json(breakdown_of(summary), chosen, ctx.lookups()))

    # --- pages -----------------------------------------------------------------------------------

    def attention_items() -> list[dict[str, str]]:
        """What asks for attention now: text, a second line and where to go (design: "Atenção")."""
        items: list[dict[str, str]] = []
        last = c.last_backup_date()
        today = ctx.today()
        if last is None:
            items.append(
                {
                    "tag": "Backup",
                    "text": "Você ainda não fez backup. Use o botão “Fazer backup”.",
                    "meta": "Os dados ficam só neste computador",
                    "href": "/#backup",
                }
            )
        elif (age := (today - last).days) > c.settings.backup_warn_days:
            items.append(
                {
                    "tag": "Backup",
                    "text": f"O último backup foi há {age} dias. Faça um novo backup.",
                    "meta": "Os dados ficam só neste computador",
                    "href": "/#backup",
                }
            )
        names = {a.id: a.nickname for a in ctx.lookups()["accounts"]}
        for b in ListAccountBalances(c.uow).execute(today):
            if b.balance_cents is None:
                items.append(
                    {
                        "tag": "Saldo",
                        "text": f"{names.get(b.account_id, '')}: saldo indisponível",
                        "meta": "Informe um saldo para ver o valor.",
                        "href": "/accounts",
                    }
                )
        for row in (
            ListInvestments(c.uow, c.clock, c.settings.valuation_stale_days).execute().accounts
        ):
            if row.stale and row.age_days is not None:
                items.append(
                    {
                        "tag": "Avaliação",
                        "text": f"{row.account.nickname} sem avaliação há {row.age_days} dias",
                        "meta": "Atualize o valor para o patrimônio ficar em dia",
                        "href": "/investments",
                    }
                )
        alerts = GetRecurring(c.uow, c.clock).execute().alerts
        if alerts:
            items.append(
                {
                    "tag": "Recorrentes",
                    "text": f"{len(alerts)} alerta(s) nas despesas recorrentes.",
                    "meta": "Veja o que sumiu, mudou de valor ou apareceu",
                    "href": "/recurring",
                }
            )
        for view in ListCards(c.uow, c.clock).execute().cards:
            usage = view.usage
            if usage.alert.value in {"warning", "exceeded"} and usage.limit_cents is not None:
                ratio = usage.percent / 100 if usage.percent is not None else None
                items.append(
                    {
                        "tag": "Limite",
                        "text": (
                            f"{view.account.nickname}: {format_percent(ratio)}"
                            " do limite comprometido."
                        ),
                        "meta": (
                            f"{format_brl(usage.committed_cents)} de "
                            f"{format_brl(usage.limit_cents)}"
                        ),
                        "href": f"/cards?card={view.account.id}",
                    }
                )
            for st in view.statements:
                if st.status is StatementStatus.CLOSED and st.days_to_due <= 7:
                    when = (
                        f"venceu há {-st.days_to_due} dia(s)"
                        if st.days_to_due < 0
                        else f"vence em {format_date(st.statement.due_date)}"
                    )
                    items.append(
                        {
                            "tag": "Vence",
                            "text": (
                                f"Fatura {view.account.nickname} "
                                f"{format_month(st.statement.month)} {when}"
                            ),
                            "meta": f"{format_brl(st.outstanding_cents)}",
                            "href": f"/cards?card={view.account.id}&month={st.statement.month}",
                        }
                    )
        return items

    def month_nav(month: YearMonth, path: str) -> dict[str, Any]:
        newest = current_month()
        previous, following = month.add_months(-1), month.add_months(1)
        return {
            "label": f"{_MONTH_NAMES[month.month - 1]} {month.year}",
            "previous": f"{path}?month={previous}",
            "next": f"{path}?month={following}" if month < newest else None,
            "is_current": month == newest,
            "path": path,
        }

    def letter_pill() -> dict[str, str] | None:
        entry = nav_entry("carta")
        if entry is None:
            return None
        previous = current_month().add_months(-1)
        return {"href": entry.href, "month": _MONTH_NAMES[previous.month - 1]}

    def donut_context(summary: Summary, data: Lookups) -> dict[str, Any]:
        """The server-printed list beside the ring (the ring itself is drawn from the JSON)."""
        breakdown = breakdown_of(summary)
        names = {x.id: x for x in data["categories"]}
        expenses = summary.expenses_cents

        def share(cents: int) -> float:
            return cents / expenses if expenses else 0

        return {
            "breakdown": breakdown,
            "rows": [
                {
                    "key": names[r.category_id].slug,
                    "name": names[r.category_id].name,
                    "group_label": messages.CATEGORY_GROUP_LABELS[r.group],
                    "color": data["category_colors"][r.category_id],
                    "count": r.count,
                    "cents": r.total_cents,
                    "share": share(r.total_cents),
                }
                for r in breakdown.top
            ],
            "rest_rows": [
                {
                    "name": names[r.category_id].name,
                    "color": data["category_colors"][r.category_id],
                    "cents": r.total_cents,
                    "share": share(r.total_cents),
                }
                for r in breakdown.rest
            ],
            "rest_share": share(breakdown.rest_total_cents),
            "top_share": share(sum(r.total_cents for r in breakdown.top)),
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, month: str = "", year: str = "") -> HTMLResponse:
        chosen = parse_month(month, year, current_month())
        period = Period.month(chosen)
        queries = GetSummary(c.uow)
        summary = queries.execute(period)
        before = queries.execute(Period.month(chosen.add_months(-1)))
        data = ctx.lookups()
        with c.uow as work:
            recurring = [
                t
                for t in work.transactions.list_for_competence(period.start, period.end)
                if t.kind is TransactionKind.EXPENSE and t.is_recurring
            ]
        recurring.sort(key=lambda t: t.amount_cents)  # most negative (biggest) first
        net_worth = GetNetWorth(c.uow, c.clock).execute()
        context: dict[str, Any] = {
            **data,
            **donut_context(summary, data),
            "summary": summary,
            "title": format_month_long(chosen),
            "month_name": _MONTH_NAMES[chosen.month - 1],
            "previous_name": _MONTH_NAMES[chosen.add_months(-1).month - 1],
            "month_key": str(chosen),
            "year": chosen.year,
            "month_nav": month_nav(chosen, "/"),
            "letter": letter_pill(),
            "balance_delta": (
                summary.balance_cents - before.balance_cents if before.entry_count else None
            ),
            "purchases": ListMonthPurchases(c.uow).execute(period.start, period.end),
            "net_worth": net_worth,
            "pending": pending_items(net_worth),
            "recurring": recurring[:4],
            "recurring_count": len(recurring),
            "attention": attention_items(),
            "group_colors": _GROUP_COLORS,
        }
        return ctx.render(request, "dashboard.html", context)

    @app.get("/analises", response_class=HTMLResponse)
    def analyses(request: Request, month: str = "", year: str = "") -> HTMLResponse:
        chosen = parse_month(month, year, current_month())
        summary = GetSummary(c.uow).execute(Period.month(chosen))
        data = ctx.lookups()
        context: dict[str, Any] = {
            "title": format_month_long(chosen),
            "month_name": _MONTH_NAMES[chosen.month - 1],
            "month_key": str(chosen),
            "year": chosen.year,
            "month_nav": month_nav(chosen, "/analises"),
            "summary": summary,
            **donut_context(summary, data),
        }
        return ctx.render(request, "analises.html", context)
