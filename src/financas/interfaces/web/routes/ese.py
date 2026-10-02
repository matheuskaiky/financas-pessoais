"""E se…: ``/ese``. A purchase simulator: one phrase in, deterministic calculation out.

The phrase is read by the regex parser (``application/phrases``), the schedule, limit and cash by
``application/whatif`` (existing domain functions: ``card_cycle``, ``installments``,
``statements``). Everything is computed on the server; the result is an HTMX fragment
(``/ese/result``). Nothing is saved and nothing leaves this computer.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from financas.application.phrases import (
    AccountRef,
    AmbiguityCode,
    AmountRole,
    FragmentKind,
    PhraseDraft,
    Vocabulary,
    parse_phrase,
)
from financas.application.queries.whatif import GetWhatIfFacts
from financas.application.whatif import (
    CardScenario,
    MonthDue,
    Purchase,
    Simulation,
    WhatIfFacts,
    monthly_dues,
    simulate,
    tightest,
)
from financas.domain.errors import DomainError
from financas.domain.models import Account, AccountKind
from financas.domain.services.present_value import annual_rate
from financas.interfaces import messages
from financas.interfaces.formatting import MONTH_ABBREVIATIONS
from financas.interfaces.web import nav
from financas.interfaces.web.routes import WebContext

CHART_HEIGHT = 240
MAX_RATE_BPS = 200  # the slider: 0 to 2,00% a month, steps of 0,05
RATE_STEP = 5
EXAMPLE = "geladeira de 4.200 em 10x de 450 no BB"


def vocabulary_of(accounts: list[Account], institutions: dict[str, str]) -> Vocabulary:
    """The closed vocabulary: the user's account and card nicknames (and their bank's name)."""
    return Vocabulary(
        tuple(
            AccountRef(
                a.id,
                a.nickname,
                a.kind is AccountKind.CREDIT_CARD,
                (institutions[a.institution_id],) if a.institution_id in institutions else (),
            )
            for a in accounts
            if a.is_active
        )
    )


@dataclass(frozen=True)
class Segment:
    """A piece of the typed phrase: ``fragment`` is set when the parser understood it."""

    text: str
    kind: FragmentKind | None
    number: int | None  # footnote number of an understood piece


def segments(draft: PhraseDraft) -> list[Segment]:
    """Cut the phrase at the fragments' edges so the page can underline what was understood."""
    out: list[Segment] = []
    position = 0
    for number, fragment in enumerate(draft.fragments, start=1):
        if fragment.start > position:
            out.append(Segment(draft.text[position : fragment.start], None, None))
        out.append(Segment(fragment.text, fragment.kind, number))
        position = fragment.end
    if position < len(draft.text):
        out.append(Segment(draft.text[position:], None, None))
    return out


def decimal_pct(rate: Decimal, places: int = 2) -> str:
    """A monthly rate as ``0,42`` (percent, comma)."""
    return f"{(rate * 100):.{places}f}".replace(".", ",")


def _nice_max(cents: int) -> int:
    """Round a chart maximum up to 1, 2, 4, 5, 8 or 10 times a power of ten (4 even ticks)."""
    if cents <= 0:
        return 100_00
    size = 1
    while size * 10 <= cents:
        size *= 10
    for factor in (1, 2, 4, 5, 8, 10):
        if factor * size >= cents:
            return factor * size
    return 10 * size


@dataclass(frozen=True)
class BarView:
    month: str
    year: str
    registered_height: float
    new_height: float
    total_cents: int
    label_cents: int
    tight: bool


@dataclass(frozen=True)
class GridView:
    cents: int
    bottom: float


@dataclass(frozen=True)
class ScenarioView:
    key: str  # "cash" or the card's account id
    is_cash: bool
    name: str
    cards: CardScenario | None
    fits: bool | None
    percent_after: float | None


@dataclass(frozen=True)
class LimitView:
    scenario: CardScenario
    percent_before: float | None
    percent_after: float | None
    percent_add: float
    unset: bool
    picked: bool


@dataclass(frozen=True)
class RateMark:
    name: str
    left: float  # 0 to 100, percent of the slider
    text: str


@dataclass(frozen=True)
class EseView:
    q: str
    draft: PhraseDraft
    segments: list[Segment]
    purchase: Purchase | None
    simulation: Simulation | None
    scenarios: list[ScenarioView]
    pick: str
    picked: ScenarioView | None
    dues: list[MonthDue]
    bars: list[BarView]
    grid: list[GridView]
    tight: MonthDue | None
    tight_before_cents: int
    limits: list[LimitView]
    rate_bps: int
    rate_text: str
    rate_annual_text: str
    rate_marks: list[RateMark]
    implied_text: str
    implied_annual_text: str
    cheaper: str  # cash | card | equal | unknown
    difference_cents: int
    difference_percent_text: str
    first_account_name: str
    asks_role: bool
    role: str
    error: str | None
    facts: WhatIfFacts
    explanation: str
    role_alt_each_total_cents: int | None
    dense: bool
    last_days: int | None
    last_due: dt.date | None


def build(ctx: WebContext, q: str, pick: str, role: str, rate_bps: int, q0: str) -> EseView:
    facts = GetWhatIfFacts(ctx.c.uow, ctx.c.clock).execute()
    look = ctx.lookups()
    institutions = {k: v.name for k, v in look["institutions"].items()}
    vocabulary = vocabulary_of(look["accounts"], institutions)
    role_answer = AmountRole(role) if role in {r.value for r in AmountRole} else None
    draft = parse_phrase(q, vocabulary, ctx.today(), amount_role=role_answer)
    rate = Decimal(rate_bps) / 10_000
    error: str | None = None
    purchase: Purchase | None = None
    simulation: Simulation | None = None
    if draft.price_cents is not None or draft.installment_cents is not None:
        purchase = Purchase(
            draft.description or "Compra",
            draft.installments or 1,
            draft.on_date or ctx.today(),
            draft.price_cents,
            draft.installment_cents,
        )
        try:
            simulation = simulate(purchase, facts, rate)
        except DomainError as exc:
            error = messages.render_error(exc)
    scenarios: list[ScenarioView] = []
    if simulation is not None:
        if simulation.cash.price_cents is not None:
            scenarios.append(
                ScenarioView("cash", True, "À vista", None, simulation.cash.fits, None)
            )
        for card in simulation.cards:
            scenarios.append(
                ScenarioView(
                    card.card.account_id,
                    False,
                    card.card.name,
                    card,
                    card.fits_limit,
                    card.usage_after.percent,
                )
            )
    keys = {s.key for s in scenarios}
    wanted = pick if (pick in keys and q == q0) else ""
    if not wanted and draft.account_id in keys:
        wanted = draft.account_id or ""
    if not wanted and scenarios:
        wanted = next((s.key for s in scenarios if not s.is_cash), scenarios[0].key)
    picked = next((s for s in scenarios if s.key == wanted), None)
    card_picked = picked.cards if picked else None

    dues = monthly_dues(facts, card_picked) if simulation is not None else []
    top = _nice_max(max([d.total_cents for d in dues] + [1]))
    scale = CHART_HEIGHT / top
    tight = tightest(dues) if dues else None
    bars: list[BarView] = []
    for i, due in enumerate(dues):
        bars.append(
            BarView(
                MONTH_ABBREVIATIONS[due.month.month - 1],
                str(due.month.year)[2:] if (i == 0 or due.month.month == 1) else "",
                round(due.registered_cents * scale, 1),
                round(due.new_cents * scale, 1),
                due.total_cents,
                due.total_cents,
                tight is not None and due.month == tight.month,
            )
        )
    grid = [GridView(top * k // 4, round(top * k / 4 * scale + 3, 1)) for k in (1, 2, 3, 4)]
    tight_before = (
        next((d.registered_cents for d in dues if tight and d.month == tight.month), 0)
        if tight
        else 0
    )

    limits: list[LimitView] = []
    if simulation is not None:
        for card in simulation.cards:
            before = card.usage_before.percent
            after = card.usage_after.percent
            is_picked = picked is not None and picked.key == card.card.account_id
            limits.append(
                LimitView(
                    card,
                    before,
                    after if is_picked else before,
                    ((after or 0) - (before or 0)) if is_picked else 0.0,
                    card.card.limit_cents is None,
                    is_picked,
                )
            )

    marks: list[RateMark] = []
    if simulation is not None and purchase is not None and purchase.price_cents:
        for card in [card_picked] if card_picked is not None else []:
            if (
                card.implied_rate is not None
                and 0 < card.implied_rate <= Decimal(MAX_RATE_BPS) / 10_000
            ):
                marks.append(
                    RateMark(
                        card.card.name,
                        float(card.implied_rate * 100 / (Decimal(MAX_RATE_BPS) / 100) * 100),
                        decimal_pct(card.implied_rate),
                    )
                )
    cheaper = "unknown"
    difference = 0
    diff_percent = ""
    implied_text = implied_annual = ""
    if card_picked is not None and card_picked.difference_cents is not None and purchase:
        difference = card_picked.difference_cents
        cheaper = "cash" if difference > 0 else ("card" if difference < 0 else "equal")
        if purchase.price_cents:
            diff_percent = f"{abs(difference) / purchase.price_cents * 100:.1f}".replace(".", ",")
        if card_picked.implied_rate is not None:
            implied_text = decimal_pct(card_picked.implied_rate, 4)
            implied_annual = decimal_pct(annual_rate(card_picked.implied_rate))
    asks = draft.has_ambiguity(AmbiguityCode.AMOUNT_ROLE)
    alt_total = None
    if asks and draft.price_cents is not None and draft.installments:
        alt_total = draft.price_cents * draft.installments
    last_due = card_picked.lines[-1].due if card_picked else None
    return EseView(
        q=q,
        draft=draft,
        segments=segments(draft),
        purchase=purchase,
        simulation=simulation,
        scenarios=scenarios,
        pick=wanted,
        picked=picked,
        dues=dues,
        bars=bars,
        grid=grid,
        tight=tight,
        tight_before_cents=tight_before,
        limits=limits,
        rate_bps=rate_bps,
        rate_text=decimal_pct(rate),
        rate_annual_text=decimal_pct(annual_rate(rate)),
        rate_marks=marks,
        implied_text=implied_text,
        implied_annual_text=implied_annual,
        cheaper=cheaper,
        difference_cents=abs(difference),
        difference_percent_text=diff_percent,
        first_account_name=simulation.cards[0].card.name if simulation and simulation.cards else "",
        asks_role=asks,
        role=(draft.amount_role.value if draft.amount_role else "total"),
        error=error,
        facts=facts,
        explanation=messages.explain_assignment(card_picked.assignment) if card_picked else "",
        role_alt_each_total_cents=alt_total,
        dense=len(dues) > 14,
        last_days=card_picked.lines[-1].days if card_picked else None,
        last_due=last_due,
    )


def _rate_of(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        return 0
    return min(max(value // RATE_STEP * RATE_STEP, 0), MAX_RATE_BPS)


def register(app: FastAPI, ctx: WebContext) -> None:
    nav.register(
        nav.NavEntry(
            id="ese",
            label="E se…",
            icon=nav.ICONS["ese"],
            href="/ese",
            group="planning",
            order=30,
            hint="simule uma compra antes de fazer",
        )
    )

    def context(q: str, pick: str, role: str, rate: str, q0: str) -> dict[str, object]:
        view = build(ctx, q.strip(), pick, role, _rate_of(rate), q0.strip())
        return {
            "view": view,
            "example": EXAMPLE,
            "fragment_kinds": FragmentKind,
            "max_rate": MAX_RATE_BPS,
            "rate_step": RATE_STEP,
        }

    @app.get("/ese", response_class=HTMLResponse)
    def ese(
        request: Request, q: str = "", pick: str = "", role: str = "", rate: str = "", q0: str = ""
    ) -> HTMLResponse:
        return ctx.render(request, "ese.html", context(q, pick, role, rate, q0))

    @app.get("/ese/result", response_class=HTMLResponse)
    def ese_result(
        request: Request, q: str = "", pick: str = "", role: str = "", rate: str = "", q0: str = ""
    ) -> HTMLResponse:
        return ctx.templates.TemplateResponse(
            request, "_ese_result.html", context(q, pick, role, rate, q0)
        )
