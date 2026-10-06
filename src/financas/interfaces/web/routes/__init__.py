"""Route modules: how a work package adds pages and JSON endpoints without touching ``app.py``.

A route module is ``routes/<name>.py`` exposing ``register(app, ctx)``. List it in :data:`MODULES`
(import it here and append it); ``create_app`` builds a :class:`WebContext` after the built-in
routes and the exception handlers are in place, then calls ``module.register(app, ctx)`` for each
module, in list order::

    # routes/analises.py
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse

    from financas.interfaces.web import nav
    from financas.interfaces.web.routes import WebContext

    def register(app: FastAPI, ctx: WebContext) -> None:
        nav.register(nav.NavEntry(id="analises", label="Análises", icon=nav.ICONS["analises"],
                                  href="/analises", group="main", order=20))

        @app.get("/analises", response_class=HTMLResponse)
        def analises(request: Request):
            return ctx.render(request, "analises.html", {**ctx.lookups()})

    # routes/__init__.py
    from financas.interfaces.web.routes import analises
    MODULES.append(analises)        # or add it to the list literal below

Rules for a module:

* Everything is created inside ``register`` (closures over ``ctx``); no import-time side effects
  except appending to :data:`MODULES`. ``register`` may run several times in one process (tests
  build many apps), so ``nav.register`` (idempotent by id) is the only global it may touch.
* Templates go in ``templates/`` and extend ``base.html``. ``ctx.render`` adds the flash messages,
  the backup notice and the ``nav`` key (the template name: ``analises.html`` -> ``"analises"``;
  override with ``{"nav": "<entry id>"}`` in the context). Shared UI pieces are macros in
  ``templates/_ui.html``; a screen-specific stylesheet is ``static/<screen>.css`` linked from the
  page's ``{% block head %}``.
* Money is ``int`` cents at every boundary; pt-BR text lives only in templates and ``messages/``.
* Use ``ctx.c`` (the container) for use cases and queries; never import ``infrastructure``.
* Domain errors raised in a route fall to the app-wide handler (a pt-BR 400 page); to keep the
  user's form, catch ``DomainError`` and call ``ctx.render(..., error=error)``.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType
from typing import Any

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from financas.container import Container
from financas.interfaces.web.shared import Lookups

# Modules whose ``register(app, ctx)`` runs at the end of ``create_app``. Starts empty; each work
# package appends its own.
MODULES: list[ModuleType] = []


@dataclass(frozen=True)
class WebContext:
    """What a route module gets from the application factory.

    * ``c``: the :class:`~financas.container.Container` (use cases, queries, clock, settings).
    * ``render(request, template, context, *, error=None, status=200)``: renders a page inside
      ``base.html`` with the shared context (flash notice, backup notice, ``nav``).
    * ``today()``: today's date from the ``Clock`` port (never ``date.today()``).
    * ``lookups()``: institutions, accounts, categories and their looks (:class:`Lookups`).
    * ``templates``: the shared ``Jinja2Templates`` (filters and globals already installed); use
      ``templates.TemplateResponse`` directly only for HTMX fragments.
    * ``back(path, ok=None, **query)``: a 303 redirect with an optional ``?ok=<flash key>``.
    * ``enum_of(EnumClass, text)`` / ``int_of(text, code)`` / ``opt_int(text)``: form converters
      (bad input becomes ``DomainError`` or ``None``, never a 500).
    * ``money(text)``: ``"1.234,56"`` or ``""`` to cents or ``None``; ``iso_date(text)``: a date or
      ``None`` (accepts ``dd/mm/aaaa`` and ISO).
    """

    c: Container
    render: Callable[..., HTMLResponse]
    today: Callable[[], dt.date]
    lookups: Callable[[], Lookups]
    templates: Jinja2Templates
    back: Callable[..., RedirectResponse]
    enum_of: Callable[[type[StrEnum], str], Any]
    int_of: Callable[..., int]
    opt_int: Callable[[str | None], int | None]
    money: Callable[[str], int | None]
    iso_date: Callable[[str], dt.date | None]


from financas.interfaces.web.routes import charts  # noqa: E402  (after WebContext: it imports it)

MODULES.append(charts)

from financas.interfaces.web.routes import carta  # noqa: E402

MODULES.append(carta)

from financas.interfaces.web.routes import ese  # noqa: E402

MODULES.append(ese)

from financas.interfaces.web.routes import palette  # noqa: E402

MODULES.append(palette)

from financas.interfaces.web.routes import importar  # noqa: E402

MODULES.append(importar)

__all__ = [
    "MODULES",
    "Lookups",
    "WebContext",
]
