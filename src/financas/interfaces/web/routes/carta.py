"""Carta do mês: ``/carta``. The monthly letter, written by the system's fixed templates.

Facts come from the existing read queries (``GetLetterFacts``), the structured letter from
``application/letter`` and the wording from ``interfaces/messages/letter.py``. Nothing is sent
anywhere; when the assistant exists it will write the templates over the same slots and signals.
"""

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from financas.application.letter import Letter, TodoTarget, build_letter
from financas.application.queries.letter import GetLetterFacts
from financas.domain.errors import DomainError
from financas.domain.money import YearMonth
from financas.interfaces.formatting import MONTH_ABBREVIATIONS, MONTH_NAMES, format_month_long
from financas.interfaces.messages import letter as words
from financas.interfaces.web import nav
from financas.interfaces.web.routes import WebContext

MONTH_CHOICES = 12
SPARK_HEIGHT = 56
GOAL_HEIGHT = 80


@dataclass(frozen=True)
class SparkView:
    letter: str  # J, F, M...
    height: int
    tone: str  # current | recent | old
    month_label: str
    cents: int


@dataclass(frozen=True)
class GoalBarView:
    label: str
    height: int
    base: int
    excess: int
    excess_cents: int
    cents: int


@dataclass(frozen=True)
class CategoryBarView:
    name: str
    cents: int
    color: str
    width: float


@dataclass(frozen=True)
class CashStepView:
    code: str
    name: str | None
    on: str | None
    cents: int
    width: float


@dataclass(frozen=True)
class TodoView:
    title: str
    sub: str
    cta: str
    href: str | None  # None: the call to action is a button that posts (backup)


@dataclass(frozen=True)
class LetterView:
    month: YearMonth
    month_name: str
    letter: Letter
    rendered: words.RenderedLetter
    spark: tuple[SparkView, ...]
    spark_average_bottom: int
    spark_first_month: str
    spark_last_month: str
    categories: tuple[CategoryBarView, ...]
    goal_bars: tuple[GoalBarView, ...]
    goal_line_bottom: int
    cash: tuple[CashStepView, ...]
    todos: tuple[TodoView, ...]
    seal_print: str


def _month_from(text: str, latest: YearMonth) -> YearMonth:
    """A lenient ``?month=``: bad text, or a month that is not closed yet, means the latest one."""
    try:
        chosen = YearMonth.parse(text)
    except DomainError:
        return latest
    return min(chosen, latest)


def month_choices(latest: YearMonth) -> list[tuple[YearMonth, str]]:
    return [
        (m, format_month_long(m).capitalize())
        for m in (latest.add_months(-n) for n in range(MONTH_CHOICES))
    ]


def _spark(letter: Letter) -> tuple[tuple[SparkView, ...], int]:
    top = max([b.cents for b in letter.spark] + [letter.spark_average_cents or 0, 1])
    scale = SPARK_HEIGHT / top
    last = len(letter.spark) - 1
    bars = tuple(
        SparkView(
            MONTH_ABBREVIATIONS[b.month.month - 1][0].upper(),
            max(2, round(b.cents * scale)) if b.cents else 2,
            "current" if b.is_current else ("recent" if last - i <= 3 else "old"),
            f"{MONTH_ABBREVIATIONS[b.month.month - 1]}/{b.month.year}",
            b.cents,
        )
        for i, b in enumerate(letter.spark)
    )
    average_bottom = round((letter.spark_average_cents or 0) * scale)
    return bars, average_bottom


def _goal(letter: Letter) -> tuple[tuple[GoalBarView, ...], int]:
    chart = letter.goal_chart
    if chart is None:
        return (), 0
    top = max([*chart.spending_cents, chart.goal_cents, 1])
    scale = GOAL_HEIGHT / top
    bars = []
    for month, cents in zip(chart.months, chart.spending_cents, strict=True):
        height = round(cents * scale)
        base = round(min(cents, chart.goal_cents) * scale)
        bars.append(
            GoalBarView(
                MONTH_ABBREVIATIONS[month.month - 1],
                height,
                base,
                height - base,
                max(cents - chart.goal_cents, 0),
                cents,
            )
        )
    return tuple(bars), round(chart.goal_cents * scale)


def build_view(
    letter: Letter, category_colors: dict[str, str], todo_href: dict[int, str | None]
) -> LetterView:
    rendered = words.render_letter(letter)
    spark, average_bottom = _spark(letter)
    goal_bars, goal_line = _goal(letter)
    biggest = max([c.cents for c in letter.top_categories] + [1])
    categories = tuple(
        CategoryBarView(
            c.name, c.cents, category_colors.get(c.category_id, "#0F5C45"), c.cents / biggest * 100
        )
        for c in letter.top_categories
    )
    top_cash = max([s.cents for s in letter.cash_steps] + [1])
    cash = tuple(
        CashStepView(
            s.code,
            s.name,
            s.on.strftime("%d/%m") if s.on else None,
            s.cents,
            max(s.cents, 0) / top_cash * 100,
        )
        for s in letter.cash_steps
    )
    todos = tuple(
        TodoView(t.title, t.sub, t.cta, todo_href.get(i)) for i, t in enumerate(rendered.todos)
    )
    code = letter.fingerprint
    return LetterView(
        letter.month,
        MONTH_NAMES[letter.month.month - 1],
        letter,
        rendered,
        spark,
        average_bottom,
        MONTH_NAMES[letter.spark[0].month.month - 1] if letter.spark else "",
        MONTH_NAMES[letter.month.month - 1],
        categories,
        goal_bars,
        goal_line,
        cash,
        todos,
        f"{code[:4]}·{code[4:]}",
    )


def register(app: FastAPI, ctx: WebContext) -> None:
    # The dot says there is a closed month to read; it is static ("keep it simple").
    nav.register(
        nav.NavEntry(
            id="carta",
            label="Carta do mês",
            icon=nav.ICONS["carta"],
            href="/carta",
            group="main",
            order=30,
            badge=True,
            hint="o mês fechado em texto, com as contas à margem",
        )
    )
    settings = ctx.c.settings

    def resolve_hrefs(letter: Letter) -> dict[int, str | None]:
        month = str(letter.month)
        hrefs: dict[int, str | None] = {}
        for i, todo in enumerate(letter.todos):
            match todo.target:
                case TodoTarget.UNCATEGORIZED_ENTRIES:
                    extra = f"&category={todo.target_id}" if todo.target_id else ""
                    hrefs[i] = f"/entries?month={month}{extra}"
                case TodoTarget.STATEMENT:
                    href = "/cards"
                    if todo.target_id:
                        with ctx.c.uow as work:
                            statement = work.statements.get(todo.target_id)
                        if statement is not None:
                            href = f"/cards?card={statement.account_id}&month={statement.month}"
                    hrefs[i] = href
                case TodoTarget.ACCOUNTS:
                    hrefs[i] = "/accounts"
                case TodoTarget.INVESTMENTS:
                    hrefs[i] = "/investments"
                case TodoTarget.BACKUP:
                    hrefs[i] = None
        return hrefs

    @app.get("/carta", response_class=HTMLResponse)
    def carta(request: Request, month: str = "") -> HTMLResponse:
        latest = YearMonth.from_date(ctx.today()).add_months(-1)
        chosen = _month_from(month, latest)
        facts = GetLetterFacts(ctx.c.uow, ctx.c.clock, settings.valuation_stale_days).execute(
            chosen, ctx.c.last_backup_date(), settings.backup_warn_days
        )
        letter = build_letter(facts)
        look = ctx.lookups()
        view = build_view(letter, look["category_colors"], resolve_hrefs(letter))
        return ctx.render(
            request,
            "carta.html",
            {
                "view": view,
                "months": month_choices(latest),
                "is_latest": chosen == latest,
                "copy": {
                    "notice": words.NOTICE_OFF,
                    "status": words.STATUS_OFF,
                    "how": words.HOW_WRITTEN,
                    "margin_title": words.SENT_NOTE,
                    "margin_help": words.SENT_HELP,
                },
            },
        )
