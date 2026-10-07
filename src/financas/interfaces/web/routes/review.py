"""Revisão Rápida (``/revisar``): one expense at a time, to fill in the merchant and the category.

The deck is plain HTMX: the card is a fragment that replaces itself; "Pular" asks for the next
one (the skipped ids travel in the request, nothing is kept on the server) and "Salvar e avançar"
saves the card through ``EnrichTransaction`` and answers with the next one. Shortcuts live in
``static/app.js`` (← skip, → or Enter save).
"""

from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from financas.application.queries.merchants import ListMerchants
from financas.application.queries.review import UNCATEGORIZED_SLUG, GetReviewDeck
from financas.application.use_cases.merchants import EnrichCommand, EnrichTransaction
from financas.domain.errors import DomainError
from financas.domain.models import CategoryKind
from financas.interfaces import messages
from financas.interfaces.web import nav
from financas.interfaces.web.routes import WebContext

MAX_SKIPPED = 500  # a pass over a very long deck: keep the query string bounded


def register(app: FastAPI, ctx: WebContext) -> None:
    nav.register(
        nav.NavEntry(
            id="review",
            label="Revisar",
            icon=nav.ICONS["review"],
            href="/revisar",
            group="movement",
            order=15,
            hint="estabelecimento e categoria, um lançamento por vez",
            count_key="review",
        )
    )
    c = ctx.c

    def skipped_of(csv: str) -> list[str]:
        ids = [part for part in csv.split(",") if part.isalnum()]
        return ids[-MAX_SKIPPED:]

    def context(skipped: list[str], error: DomainError | None = None) -> dict[str, object]:
        deck = GetReviewDeck(c.uow).execute(skipped)
        data = ctx.lookups()
        pills = [
            x
            for x in data["categories"]
            if x.kind is CategoryKind.EXPENSE and x.slug != UNCATEGORIZED_SLUG
        ]
        card = deck.card
        plan = None
        if card and card.entry.plan_id:
            with c.uow as work:
                plan = work.plans.get(card.entry.plan_id)
        return {
            **data,
            "deck": deck,
            "skipped_csv": ",".join(skipped),
            "pills": pills,
            "plan": plan,
            "account_names": {a.id: a.nickname for a in data["accounts"]},
            "merchants": ListMerchants(c.uow).execute(),
            "error": messages.render_error(error) if error else None,
            "nav": "review",
        }

    @app.get("/revisar", response_class=HTMLResponse)
    def review_page(request: Request, skipped: str = ""):
        return ctx.render(request, "revisar.html", context(skipped_of(skipped)))

    @app.get("/revisar/next", response_class=HTMLResponse)
    def review_next(request: Request, skip_id: str = "", skipped: str = ""):
        ids = skipped_of(skipped)
        if skip_id.isalnum() and skip_id not in ids:
            ids.append(skip_id)
        return ctx.templates.TemplateResponse(request, "_review_card.html", context(ids))

    @app.post("/entries/{transaction_id}/quick-fill", response_class=HTMLResponse)
    def quick_fill(
        request: Request,
        transaction_id: str,
        merchant: Annotated[str, Form()] = "",
        category_id: Annotated[str, Form()] = "",
        skipped: Annotated[str, Form()] = "",
    ):
        ids = skipped_of(skipped)
        try:
            EnrichTransaction(c.uow).execute(
                EnrichCommand(transaction_id, merchant or None, category_id or None)
            )
        except DomainError as error:
            if error.code == "NOT_FOUND":
                raise
            # the same card again, with the reason; nothing was saved
            return ctx.templates.TemplateResponse(request, "_review_card.html", context(ids, error))
        if transaction_id.isalnum() and transaction_id not in ids:
            ids.append(transaction_id)  # saved: not shown again in this pass, even if still pending
        return ctx.templates.TemplateResponse(request, "_review_card.html", context(ids))
