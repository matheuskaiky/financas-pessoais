"""Command palette (⌘K): the server side of "registrar por frase".

The markup is ``templates/_palette.html`` (included by ``base.html``), the behavior is
``static/palette.js``. Typing a phrase such as "café 12,50 hoje nubank" asks ``/palette/parse``
(HTMX), which reads it with the deterministic regex parser (``application/phrases``) and answers
with a preview: amount, date, account, a *suggested* category (``SuggestCategory``) and a button
that opens the quick entry form pre-filled. The kind of the entry (expense, income, refund,
transfer) is ALWAYS the user's choice: the preview shows a control, never an inferred value
(CLAUDE.md 9.1). Nothing is saved here and nothing leaves this computer.
"""

import datetime as dt
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from financas.application.phrases import (
    AmbiguityCode,
    AmountRole,
    FragmentKind,
    PhraseDraft,
    parse_phrase,
)
from financas.application.queries.whatif import GetWhatIfFacts
from financas.application.use_cases.transactions import SuggestCategory
from financas.application.whatif import CardScenario, Purchase, simulate
from financas.domain.errors import DomainError
from financas.domain.models import Account, AccountKind, TransactionKind
from financas.interfaces import messages
from financas.interfaces.formatting import format_decimal_comma
from financas.interfaces.web.routes import WebContext
from financas.interfaces.web.routes.ese import segments, vocabulary_of

ENTRY_KINDS = (
    TransactionKind.EXPENSE,
    TransactionKind.INCOME,
    TransactionKind.REFUND,
    TransactionKind.TRANSFER,
)
KIND_LABELS = {
    TransactionKind.EXPENSE: "Despesa",
    TransactionKind.INCOME: "Receita",
    TransactionKind.REFUND: "Estorno",
    TransactionKind.TRANSFER: "Transferência",
}


def kind_of(text: str) -> TransactionKind:
    """The user's pick; bad or missing text means Despesa (the quick form's own default)."""
    try:
        chosen = TransactionKind(text)
    except ValueError:
        return TransactionKind.EXPENSE
    return chosen if chosen in ENTRY_KINDS else TransactionKind.EXPENSE


def fill_query(
    draft: PhraseDraft,
    kind: TransactionKind,
    day: dt.date,
    category_id: str | None,
    amount_cents: int | None,
) -> str:
    """The query string that pre-fills the quick entry form (``/entries?fill=1&f_...``)."""
    values: dict[str, str] = {"fill": "1", "f_kind": kind.value, "f_date": day.isoformat()}
    if amount_cents is not None:
        values["f_amount"] = format_decimal_comma(amount_cents)
    if draft.description:
        values["f_description"] = draft.description
    if draft.account_id:
        values["f_account"] = draft.account_id
    if category_id:
        values["f_category"] = category_id
    return urlencode(values)


def register(app: FastAPI, ctx: WebContext) -> None:
    def card_scenario(draft: PhraseDraft, day: dt.date) -> CardScenario | None:
        """The schedule of an installment purchase on the card the phrase names (rate zero)."""
        if draft.account_id is None or draft.amount_cents is None:
            return None
        facts = GetWhatIfFacts(ctx.c.uow, ctx.c.clock).execute()
        if not any(c.account_id == draft.account_id for c in facts.cards):
            return None
        purchase = Purchase(
            draft.description or "Compra",
            draft.installments or 1,
            day,
            draft.price_cents,
            draft.installment_cents,
        )
        try:
            sim = simulate(purchase, facts, Decimal(0))
        except DomainError:
            return None
        return next((s for s in sim.cards if s.card.account_id == draft.account_id), None)

    @app.get("/palette/parse", response_class=HTMLResponse)
    def parse(request: Request, q: str = "", kind: str = "", role: str = "") -> HTMLResponse:
        text = q.strip()
        chosen_kind = kind_of(kind)
        context: dict[str, object] = {"q": text, "kind": chosen_kind, "kinds": ENTRY_KINDS}
        if not text:
            return ctx.templates.TemplateResponse(request, "_palette_preview.html", context)
        look = ctx.lookups()
        institutions = {k: v.name for k, v in look["institutions"].items()}
        accounts: list[Account] = [
            a
            for a in look["accounts"]
            if a.is_active and a.kind in (AccountKind.CHECKING, AccountKind.CREDIT_CARD)
        ]
        vocabulary = vocabulary_of(accounts, institutions)
        answer = AmountRole(role) if role in {r.value for r in AmountRole} else None
        draft = parse_phrase(text, vocabulary, ctx.today(), amount_role=answer)
        by_id = {a.id: a for a in accounts}
        day = draft.on_date or ctx.today()
        installment_purchase = draft.is_installment_purchase
        suggestion = None
        if draft.description and not installment_purchase and not draft.what_if:
            suggestion = SuggestCategory(ctx.c.uow).execute(draft.description, chosen_kind)
        amount = draft.amount_cents
        scenario = card_scenario(draft, day) if installment_purchase else None
        date_fragment = next((f for f in draft.fragments if f.kind is FragmentKind.DATE), None)
        context.update(
            {
                "draft": draft,
                "segments": segments(draft),
                "account": by_id.get(draft.account_id or ""),
                "candidates": [by_id[i] for i in draft.account_candidates if i in by_id],
                "day": day,
                "today": ctx.today(),
                "date_text": date_fragment.text if date_fragment else None,
                "amount_cents": amount,
                "category": suggestion,
                "fill": fill_query(
                    draft, chosen_kind, day, suggestion.id if suggestion else None, amount
                ),
                "installment_purchase": installment_purchase,
                "scenario": scenario,
                "explanation": messages.explain_assignment(scenario.assignment) if scenario else "",
                "asks_role": draft.has_ambiguity(AmbiguityCode.AMOUNT_ROLE),
                "ese_query": urlencode({"q": text}),
                "kind_labels": KIND_LABELS,
                "role": draft.amount_role.value if draft.amount_role else "total",
            }
        )
        return ctx.templates.TemplateResponse(request, "_palette_preview.html", context)
