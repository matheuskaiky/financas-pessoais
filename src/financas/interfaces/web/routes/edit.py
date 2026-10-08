"""Editing what was already entered: rename a category, edit an entry, anticipate installments.

All three are HTMX-first (the form swaps over the row or the plan card) with a plain-POST fallback.
The rules live in the use cases; this module parses the form, calls them and turns the domain's
error codes into pt-BR through ``messages``.
"""

import html
import json
from decimal import Decimal
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from financas.application.queries.merchants import ListMerchants
from financas.application.queries.plan_purchases import GetPlanPurchase
from financas.application.queries.selection import ListRowSelections
from financas.application.use_cases._common import UNSET
from financas.application.use_cases.anticipation import (
    AnticipateInstallments,
    AnticipationCommand,
    AnticipationPreview,
    PreviewAnticipation,
)
from financas.application.use_cases.cards import (
    DeleteInstallmentPlan,
    UpdateInvoicePayment,
    UpdatePaymentCommand,
)
from financas.application.use_cases.catalog import RenameCategory
from financas.application.use_cases.merge import MergeCommand, MergeTransactions
from financas.application.use_cases.transactions import (
    EntryEditState,
    GetEntryEditState,
    SplitItem,
    UpdateTransaction,
    UpdateTransactionCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    CategoryGroup,
    CategoryKind,
    PaymentMethod,
    Transaction,
    TransactionKind,
)
from financas.domain.money import parse_brl
from financas.interfaces import messages
from financas.interfaces.formatting import format_decimal_comma, parse_date, parse_percent_bps
from financas.interfaces.web.payment_dates import payment_window
from financas.interfaces.web.routes import WebContext
from financas.interfaces.web.routes.selection import error_response

BPS_PER_UNIT = Decimal(10_000)
NOT_FOUND = 404
# merge failures the Selection Mode script answers by locking rows (422 + JSON)
SELECTION_CODES = frozenset(
    {"MERGE_OUTSIDE_CURRENT_MONTH", "MERGE_ITEMIZED_FORBIDDEN", "MERGE_ONLY_PLAIN_EXPENSES"}
)


def register(app: FastAPI, ctx: WebContext) -> None:
    c = ctx.c

    def htmx(request: Request) -> bool:
        return request.headers.get("hx-request") == "true"

    def fragment(request: Request, template: str, context: dict[str, object]) -> HTMLResponse:
        return ctx.templates.TemplateResponse(request, template, context)

    def redirect(request: Request, url: str) -> Response:
        """HTMX follows ``HX-Redirect`` with a full page load; a plain form gets a 303."""
        if htmx(request):
            return Response(status_code=200, headers={"HX-Redirect": url})
        return RedirectResponse(url, status_code=303)

    # --- categories: inline rename ---------------------------------------------------------------

    def category_row(
        request: Request,
        category_id: str,
        *,
        editing: bool,
        value: str | None = None,
        error: str | None = None,
        notice: str | None = None,
    ) -> Response:
        with c.uow as work:
            category = work.categories.get(category_id)
        if category is None:
            return Response("Categoria não encontrada.", status_code=NOT_FOUND)
        response = fragment(
            request,
            "_category_row.html",
            {
                "c": category,
                "editing": editing,
                "value": value,
                "error": error,
                "category_colors": ctx.lookups()["category_colors"],
            },
        )
        if notice:  # shown by edit.js in the page's flash area, no reload
            response.headers["HX-Trigger"] = json.dumps({"fp:notice": notice})
        return response

    @app.get("/categories/{category_id}/row", response_class=HTMLResponse)
    def category_row_view(request: Request, category_id: str):
        return category_row(request, category_id, editing=False)

    @app.get("/categories/{category_id}/rename", response_class=HTMLResponse)
    def category_rename_form(request: Request, category_id: str):
        return category_row(request, category_id, editing=True)

    @app.post("/categories/{category_id}/rename")
    def category_rename(request: Request, category_id: str, name: Annotated[str, Form()]):
        try:
            RenameCategory(c.uow).execute(category_id, name)
        except DomainError as error:
            if error.code == "NOT_FOUND":
                raise
            if htmx(request):
                return category_row(
                    request,
                    category_id,
                    editing=True,
                    value=name,
                    error=messages.render_error(error),
                )
            context = {
                **ctx.lookups(),
                "groups": list(CategoryGroup),
                "kinds": list(CategoryKind),
            }
            return ctx.render(request, "categories.html", context, error=error)
        if htmx(request):
            return category_row(
                request,
                category_id,
                editing=False,
                notice=messages.FLASH_MESSAGES["category_renamed"],
            )
        return ctx.back("/categories", "category_renamed")

    # --- entries: edit in place (from the entries list, or from a statement on /cards) ---------

    def origin_of(from_: str, card: str, month: str, ano: str) -> dict[str, str]:
        """Where the edit was opened. Only the statement view is a known origin; the values are
        echoed into links and a redirect to the fixed path ``/cards``, never used as a URL."""
        if from_ != "cards":
            return {}
        given = {"from": "cards", "card": card, "month": month, "ano": ano}
        return {k: v[:40] for k, v in given.items() if v}

    # --- entries: edit in place ----------------------------------------------------------------

    def entry_form(entry: Transaction) -> dict[str, str]:
        return {
            "amount": format_decimal_comma(abs(entry.amount_cents)),
            "date": entry.posted_on.isoformat(),
            "description": entry.description,
            "category_id": entry.category_id
            or "",  # none on an itemized entry: its items have them
            "account_id": entry.account_id,
            "notes": entry.notes or "",
            "ack": "",
            "purchase_date": "",
            "propagate": "1",  # installments: spread description and category (on by default)
            "refunded": "1" if entry.is_refunded else "",
            "refund_all": "",
            "merchant": entry.merchant or "",
            "payment_method": entry.payment_method.value if entry.payment_method else "",
        }

    def edit_form(
        request: Request,
        state: EntryEditState,
        form: dict[str, str],
        error: DomainError | None = None,
        origin: dict[str, str] | None = None,
        items: list[dict[str, str]] | None = None,
    ) -> HTMLResponse:
        lookups = ctx.lookups()
        entry = state.entry
        wanted = (
            CategoryKind.NEUTRAL
            if entry.kind in {TransactionKind.REFUND, TransactionKind.TRANSFER}
            else CategoryKind(entry.kind.value)
        )
        current = next((a for a in lookups["accounts"] if a.id == entry.account_id), None)
        return fragment(
            request,
            "_entry_edit.html",
            {
                "state": state,
                "form": form,
                "error": messages.render_error(error) if error else None,
                "origin_qs": urlencode(origin or {}),
                # an installment edited from the entries list stands for its whole purchase: the
                # purchase date is editable there (the card screen keeps the plain installment)
                "consolidated": state.is_installment and not origin,
                # a statement payment's date range (the form and the server both enforce it)
                "payment_bounds": payment_window(
                    c.uow, state.payment.statement.statement, ctx.today()
                )
                if state.payment
                else None,
                "form_items": items if items is not None else saved_items(state),
                # a payment's source account: active checking accounts (and the one it has now)
                "origin_choices": [
                    a
                    for a in lookups["accounts"]
                    if a.kind is AccountKind.CHECKING
                    and (
                        a.is_active
                        or (
                            (state.payment and state.payment.origin_leg)
                            and a.id == state.payment.origin_leg.account_id
                        )
                    )
                ],
                "category_options": [x for x in lookups["categories"] if x.kind is wanted],
                # same kind only (a checking entry never becomes a card purchase)
                "accounts": [
                    a
                    for a in lookups["accounts"]
                    if current is not None
                    and a.kind is current.kind
                    and (a.is_active or a.id == entry.account_id)
                ],
            },
        )

    def payment_form(state: EntryEditState) -> dict[str, str]:
        assert state.payment is not None
        card_leg, origin_leg = state.payment.card_leg, state.payment.origin_leg
        return {
            "amount": format_decimal_comma(card_leg.amount_cents),
            "date": card_leg.posted_on.isoformat(),
            "from_account": origin_leg.account_id if origin_leg else "",
            "notes": card_leg.notes or "",
        }

    def saved_items(state: EntryEditState) -> list[dict[str, str]]:
        # an installment opens on its plan-wide items (the form's default scope); any other entry
        # on its own
        plan_wide = state.plan_items.items if state.plan_items else ()
        return [
            {
                "description": s.description,
                "category_id": s.category_id,
                "amount": format_decimal_comma(s.amount_cents),
            }
            for s in (plan_wide or state.splits)
        ]

    @app.get("/entries/{transaction_id}/row", response_class=HTMLResponse)
    def entry_row(
        request: Request,
        transaction_id: str,
        from_: Annotated[str, Query(alias="from")] = "",
        card: str = "",
        month: str = "",
        ano: str = "",
    ):
        state = GetEntryEditState(c.uow, c.clock).execute(transaction_id)
        lookups = ctx.lookups()
        origin = origin_of(from_, card, month, ano)
        context: dict[str, object] = {
            "t": state.entry,
            "accounts": lookups["accounts"],
            "category_by_id": {x.id: x for x in lookups["categories"]},
            "category_colors": lookups["category_colors"],
            "splits": {state.entry.id: list(state.splits)} if state.splits else {},
        }
        purchase = (
            GetPlanPurchase(c.uow).execute(state.entry.plan_id) if state.entry.plan_id else None
        )
        by_plan = {purchase.plan.id: purchase} if purchase is not None else {}
        if not origin:
            if purchase is not None:  # an installment is listed as its whole purchase here
                context["t"] = purchase.as_entry()
                context["plan_purchases"] = {purchase.anchor.id: purchase}
            shown = purchase.as_entry() if purchase is not None else state.entry
            context["selections"] = ListRowSelections(c.uow, c.clock).execute(
                [shown], by_plan, {state.entry.id: list(state.splits)} if state.splits else {}
            )
            return fragment(request, "_entry_row.html", context)
        with c.uow as work:
            plans = {p.id: p for p in work.plans.list_all()}
        rows: list[Transaction] = [] if state.entry.transfer_id else [state.entry]
        context["selections"] = ListRowSelections(c.uow, c.clock).execute(
            rows,
            by_plan,
            {state.entry.id: list(state.splits)} if state.splits else {},
        )
        feed: dict[str, object] = {}
        if origin.get("card") == "all":  # the unified feed: the row keeps its date and card columns
            mine = next((a for a in lookups["accounts"] if a.id == state.entry.account_id), None)
            if mine is not None:
                feed = {
                    "show_card_tag": True,
                    "card_tags": {
                        mine.id: {
                            "name": mine.nickname,
                            "color": lookups["account_looks"][mine.id].color,
                        }
                    },
                }
        return fragment(
            request,
            "_card_entry_row.html",
            {
                **context,
                **feed,
                "plans": plans,
                "locked": state.is_locked,
                "origin_qs": urlencode(origin),
            },
        )

    @app.get("/entries/{transaction_id}/edit", response_class=HTMLResponse)
    def entry_edit_form(
        request: Request,
        transaction_id: str,
        from_: Annotated[str, Query(alias="from")] = "",
        card: str = "",
        month: str = "",
        ano: str = "",
    ):
        state = GetEntryEditState(c.uow, c.clock).execute(transaction_id)
        origin = origin_of(from_, card, month, ano)
        form = payment_form(state) if state.is_payment else entry_form(state.entry)
        if state.is_installment and not origin:
            purchase = GetPlanPurchase(c.uow).execute(state.entry.plan_id or "")
            if purchase is not None:
                form["purchase_date"] = purchase.purchased_on.isoformat()
        return edit_form(request, state, form, origin=origin)

    def list_url(request: Request, ok: str, extra: dict[str, str] | None = None) -> str:
        """Back to the list the user was on (same month and filters), with the flash message."""
        origin = urlsplit(
            request.headers.get("hx-current-url") or request.headers.get("referer", "")
        )
        kept = (
            [
                (k, v)
                for k, v in parse_qsl(origin.query)
                if k not in {"ok", "err", "fill"} and not k.startswith("f_")
            ]
            if origin.path == "/entries"
            else []
        )
        flash = list(extra.items()) if extra else [("ok", ok)]
        return "/entries?" + urlencode([*kept, *flash])

    @app.post("/entries/{transaction_id}/edit")
    def entry_edit(
        request: Request,
        transaction_id: str,
        amount: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        from_account: Annotated[str, Form()] = "",
        date: Annotated[str, Form()] = "",
        category_id: Annotated[str, Form()] = "",
        account_id: Annotated[str, Form()] = "",
        notes: Annotated[str, Form()] = "",
        ack: Annotated[str, Form()] = "",
        purchase_date: Annotated[str, Form()] = "",
        propagate: Annotated[str, Form()] = "",
        refunded: Annotated[str, Form()] = "",
        refund_all: Annotated[str, Form()] = "",
        splits_present: Annotated[str, Form()] = "",
        merchant: Annotated[str, Form()] = "",
        payment_method: Annotated[str, Form()] = "",
        item_description: Annotated[list[str] | None, Form()] = None,
        item_category: Annotated[list[str] | None, Form()] = None,
        item_amount: Annotated[list[str] | None, Form()] = None,
        from_: Annotated[str, Query(alias="from")] = "",
        card: str = "",
        month: str = "",
        ano: str = "",
    ):
        origin = origin_of(from_, card, month, ano)
        form = {
            "amount": amount,
            "date": date,
            "description": description,
            "category_id": category_id,
            "account_id": account_id,
            "notes": notes,
            "ack": ack,
            "purchase_date": purchase_date,
            "propagate": propagate,
            "refunded": refunded,
            "refund_all": refund_all,
            "merchant": merchant,
            "payment_method": payment_method,
        }
        state = GetEntryEditState(c.uow, c.clock).execute(transaction_id)
        if state.is_payment:
            return save_payment(request, state, amount, date, from_account, notes, origin)
        installment = state.is_installment  # its date and card are not on the form
        typed = [
            {"description": d, "category_id": k, "amount": a}
            for d, k, a in zip(
                item_description or [], item_category or [], item_amount or [], strict=False
            )
            if d.strip() or a.strip()
        ]
        try:
            if not installment and not date:
                raise DomainError("INVALID_DATE")
            UpdateTransaction(c.uow, c.clock).execute(
                UpdateTransactionCommand(
                    transaction_id=transaction_id,
                    posted_on=state.entry.posted_on
                    if installment
                    else parse_date(date, ctx.today()),
                    amount_cents=abs(parse_brl(amount)),  # the sign comes from the kind
                    description=description,
                    category_id=category_id or None,
                    account_id=None if installment else (account_id or None),
                    notes=notes or None,
                    acknowledge_closed=bool(ack),
                    propagate_plan_metadata=bool(propagate),
                    is_refunded=bool(refunded),
                    refund_pending_installments=bool(refund_all),
                    merchant=merchant,
                    payment_method=(
                        ctx.enum_of(PaymentMethod, payment_method) if payment_method else UNSET
                    ),
                    purchase_date=(
                        parse_date(purchase_date, ctx.today())
                        if purchase_date and installment and not origin
                        else None
                    ),
                    # the form always says whether it carries the items (an empty list removes them)
                    splits=(
                        tuple(
                            SplitItem(
                                t["description"], t["category_id"], abs(parse_brl(t["amount"]))
                            )
                            for t in typed
                        )
                        if splits_present
                        else None
                    ),
                )
            )
        except DomainError as error:
            if error.code == "NOT_FOUND":
                raise
            state = GetEntryEditState(c.uow, c.clock).execute(transaction_id)
            return edit_form(request, state, form, error, origin, typed if splits_present else None)
        if origin:  # back to the statement the user was looking at (same year and month)
            back_to = {k: v for k, v in origin.items() if k != "from"}
            return redirect(request, "/cards?" + urlencode({**back_to, "ok": "entry_updated"}))
        return redirect(request, list_url(request, "entry_updated"))

    def save_payment(
        request: Request,
        state: EntryEditState,
        amount: str,
        date: str,
        from_account: str,
        notes: str,
        origin: dict[str, str],
    ) -> Response:
        """A statement payment: both legs move together (amount, date, source account, notes)."""
        form = {"amount": amount, "date": date, "from_account": from_account, "notes": notes}
        try:
            if not date:
                raise DomainError("INVALID_DATE")
            UpdateInvoicePayment(c.uow, c.clock).execute(
                UpdatePaymentCommand(
                    transaction_id=state.entry.id,
                    paid_on=parse_date(date, ctx.today()),
                    amount_cents=parse_brl(amount),
                    from_account_id=from_account or None,  # blank: an account not tracked here
                    notes=notes or None,
                )
            )
        except DomainError as error:
            if error.code == "NOT_FOUND":
                raise
            fresh = GetEntryEditState(c.uow, c.clock).execute(state.entry.id)
            return edit_form(request, fresh, form, error, origin)
        if origin:
            back_to = {k: v for k, v in origin.items() if k != "from"}
            return redirect(request, "/cards?" + urlencode({**back_to, "ok": "payment_updated"}))
        return redirect(request, list_url(request, "payment_updated"))

    # --- installments: anticipate ----------------------------------------------------------------

    def anticipation_command(
        plan_id: str, numbers: list[str], rate: str, discount: str, description: str = ""
    ) -> AnticipationCommand:
        return AnticipationCommand(
            plan_id,
            installment_numbers=tuple(ctx.int_of(n) for n in numbers),
            monthly_rate=(
                Decimal(parse_percent_bps(rate)) / BPS_PER_UNIT if rate.strip() else None
            ),
            discount_cents=parse_brl(discount) if discount.strip() else None,
            discount_description=description,
        )

    def try_preview(
        cmd: AnticipationCommand,
    ) -> tuple[AnticipationPreview | None, DomainError | None]:
        try:
            return PreviewAnticipation(c.uow, c.clock).execute(cmd), None
        except DomainError as error:
            return None, error

    def plan_of(plan_id: str):
        with c.uow as work:
            plan = work.plans.get(plan_id)
        if plan is None:
            raise DomainError("NOT_FOUND", entity="plan")
        return plan

    def anticipate_form(
        request: Request,
        plan_id: str,
        preview: AnticipationPreview | None,
        error: DomainError | None,
        form: dict[str, str],
    ) -> HTMLResponse:
        return fragment(
            request,
            "_anticipate.html",
            {
                "plan": plan_of(plan_id),
                "preview": preview,
                "error": messages.render_error(error) if error else None,
                "form": form,
            },
        )

    @app.get("/plans/{plan_id}/anticipate", response_class=HTMLResponse)
    def anticipate_open(request: Request, plan_id: str):
        preview, error = try_preview(AnticipationCommand(plan_id))
        return anticipate_form(request, plan_id, preview, error, {"rate": "", "discount": ""})

    @app.get("/plans/{plan_id}/anticipate/close", response_class=HTMLResponse)
    def anticipate_close(plan_id: str):
        return HTMLResponse("")

    @app.post("/plans/{plan_id}/anticipate/preview", response_class=HTMLResponse)
    def anticipate_preview(
        request: Request,
        plan_id: str,
        n: Annotated[list[str] | None, Form()] = None,
        rate: Annotated[str, Form()] = "",
        discount: Annotated[str, Form()] = "",
    ):
        try:
            preview, error = try_preview(anticipation_command(plan_id, n or [], rate, discount))
        except DomainError as invalid:  # a typo in the rate or the amount
            preview, error = None, invalid
        return fragment(
            request,
            "_anticipate_totals.html",
            {"preview": preview, "error": messages.render_error(error) if error else None},
        )

    @app.post("/plans/{plan_id}/anticipate")
    def anticipate(
        request: Request,
        plan_id: str,
        n: Annotated[list[str] | None, Form()] = None,
        rate: Annotated[str, Form()] = "",
        discount: Annotated[str, Form()] = "",
    ):
        plan = plan_of(plan_id)
        form = {"rate": rate, "discount": discount}
        try:
            AnticipateInstallments(c.uow, c.clock).execute(
                anticipation_command(
                    plan_id,
                    n or [],
                    rate,
                    discount,
                    f"Desconto na antecipação de parcelas: {plan.description}"[:300],
                )
            )
        except DomainError as error:
            preview, _ = try_preview(
                AnticipationCommand(plan_id, tuple(ctx.int_of(x) for x in n or []))
            )
            return anticipate_form(request, plan_id, preview, error, form)
        return redirect(
            request, f"/cards?{urlencode({'ok': 'anticipated', 'card': plan.account_id})}"
        )

    # --- installments: delete what is still pending ----------------------------------------------

    @app.delete("/installments/plan/{plan_id}")
    def delete_installment_plan(request: Request, plan_id: str):
        """Removes the plan's installments that are not on a paid statement (those are history)."""
        plan = plan_of(plan_id)
        origin = urlsplit(
            request.headers.get("hx-current-url") or request.headers.get("referer", "")
        )
        try:
            result = DeleteInstallmentPlan(c.uow, c.clock).execute(plan_id)
        except DomainError as error:
            if error.code == "NOT_FOUND":
                raise
            ok = {"err": error.code}
        else:
            ok = {"ok": "plan_deleted" if result.plan_removed else "plan_pending_deleted"}
        if origin.path == "/entries":
            return redirect(request, list_url(request, "", extra=ok))
        return redirect(request, "/cards?" + urlencode({"card": plan.account_id, **ok}))

    # --- merge several expenses into one itemized entry ---------------------------------------

    @app.post("/entries/merge")
    def merge_entries(
        request: Request,
        description: Annotated[str, Form()],
        date: Annotated[str, Form()],
        ids: Annotated[list[str] | None, Form()] = None,
        ack: Annotated[str, Form()] = "",
    ):
        try:
            MergeTransactions(c.uow, c.clock).execute(
                MergeCommand(
                    tuple(ids or []),
                    description,
                    parse_date(date, ctx.today()),
                    acknowledge_closed=bool(ack),
                )
            )
        except DomainError as error:
            if error.code == "NOT_FOUND":
                raise
            if htmx(request) and error.code in SELECTION_CODES:
                # 422 + JSON: the Selection Mode script locks the offending rows (HTMX swaps no 4xx)
                return error_response(error)
            if htmx(request):  # the dialog shows the message; HTMX does not swap 4xx pages
                return HTMLResponse(
                    f'<p class="hint" data-tone="error" role="alert">'
                    f"{html.escape(messages.render_error(error))}</p>"
                )
            return RedirectResponse(f"/entries?{urlencode({'err': error.code})}", status_code=303)
        origin = urlsplit(
            request.headers.get("hx-current-url") or request.headers.get("referer", "")
        )
        if origin.path == "/cards":
            kept = [(k, v) for k, v in parse_qsl(origin.query) if k in {"card", "month", "ano"}]
            return redirect(request, "/cards?" + urlencode([*kept, ("ok", "merged")]))
        return redirect(request, list_url(request, "merged"))

    # --- merchants: autocomplete ----------------------------------------------------------

    @app.get("/merchants/suggestions", response_class=HTMLResponse)
    def merchant_suggestions(request: Request, account: str = ""):
        """The ``<option>`` list behind the merchant fields' ``<datalist>`` (every name typed so
        far, once, sorted ignoring accents); ``?account=`` narrows it to one account."""
        names = ListMerchants(c.uow).execute(account or None)
        return fragment(request, "_merchant_options.html", {"merchants": names})
