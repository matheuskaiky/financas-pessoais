"""Selection Mode: the batch-delete confirmation and the batch delete itself.

``GET /entries/batch-delete/confirm`` renders the destructive dialog (it changes nothing) and
``POST /entries/batch-delete`` deletes every selected entry and plan in one unit of work. Both
take typed ids (``entry:<id>`` / ``plan:<id>``). Failures are 422 with a small JSON body
(``code``, pt-BR ``message``, offending ``ids``) that ``static/selection-mode.js`` shows in place.
"""

import json
import logging
from typing import Annotated

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from financas.application.use_cases.batch_delete import (
    BatchDelete,
    PreviewBatchDelete,
    parse_selection_ids,
)
from financas.domain.errors import DomainError
from financas.interfaces import messages
from financas.interfaces.web.routes import WebContext

NOT_FOUND = 404
UNPROCESSABLE = 422
MAX_PLANS_LISTED = 3

_log = logging.getLogger("financas.selection")

# the contract's codes (lower snake case) for the domain's
_HTTP_CODES = {
    "BAD_SELECTION_ID": "bad_id",
    "TOO_MANY_IDS": "too_many_ids",
    "DELETE_OUTSIDE_CURRENT_MONTH": "delete_outside_current_month",
    "MERGE_OUTSIDE_CURRENT_MONTH": "merge_outside_current_month",
    "MERGE_ITEMIZED_FORBIDDEN": "merge_not_allowed",
    "MERGE_ONLY_PLAIN_EXPENSES": "merge_not_allowed",
}


def error_response(error: DomainError) -> JSONResponse:
    """A failed selection request: 404 when nothing resolves, otherwise 422 with the offenders."""
    status = NOT_FOUND if error.code == "NOT_FOUND" else UNPROCESSABLE
    offenders = [i for i in str(error.params.get("ids", "")).split(",") if i]
    return JSONResponse(
        {
            "code": _HTTP_CODES.get(error.code, error.code.lower()),
            "message": messages.render_error(error),
            "ids": offenders,
        },
        status_code=status,
    )


def register(app: FastAPI, ctx: WebContext) -> None:
    c = ctx.c

    @app.get("/entries/batch-delete/confirm", response_class=HTMLResponse)
    def batch_delete_confirm(request: Request, ids: Annotated[list[str] | None, Query()] = None):
        try:
            preview = PreviewBatchDelete(c.uow, c.clock).execute(parse_selection_ids(ids or []))
        except DomainError as error:
            return error_response(error)
        return ctx.templates.TemplateResponse(
            request,
            "_batch_delete_dialog.html",
            {
                "preview": preview,
                "listed": preview.plans[:MAX_PLANS_LISTED],
                "more": max(0, len(preview.plans) - MAX_PLANS_LISTED),
            },
        )

    @app.post("/entries/batch-delete")
    def batch_delete(ids: Annotated[list[str] | None, Form()] = None) -> Response:
        try:
            result = BatchDelete(c.uow, c.clock).execute(parse_selection_ids(ids or []))
        except DomainError as error:
            return error_response(error)
        _log.info("batch delete: %d deleted, %d skipped", result.deleted, result.skipped)
        trigger = {"selection:deleted": {"deleted": result.deleted, "skipped": result.skipped}}
        return Response(status_code=200, headers={"HX-Trigger": json.dumps(trigger)})
