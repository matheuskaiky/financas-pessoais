"""Navigation of the web UI: one data-driven list rendered by ``base.html``.

It feeds the desktop sidebar and the "Mais" page (how a phone reaches pages outside the tab bar).

Pages register themselves here. The built-in entries below are the pages that exist today; a later
package adds its own from its route module (``routes/<name>.py``), so a page that does not exist
never shows up in the menu::

    from financas.interfaces.web import nav

    def register(app, ctx):
        nav.register(nav.NavEntry(
            id="analises", label="Análises", icon=nav.ICONS["analises"], href="/analises",
            group="main", order=20,
        ))
        ...

* ``id`` is the key a page passes as ``nav`` to its template context. ``render`` derives it from the
  template name (``analises.html`` -> ``"analises"``); pass ``{"nav": "..."}`` in the context to
  override. ``aliases`` are other keys that highlight this entry (``flow.html`` -> Contas).
* ``group`` is one of :data:`GROUPS`; entries sort by ``order`` inside a group (gaps of 10 are left
  on purpose). ``badge`` draws a gold dot ("nova"). ``sidebar=False`` keeps an entry out of the
  desktop sidebar (it still appears in the "Mais" page, which is how mobile reaches it).
* ``register`` replaces an entry with the same ``id``, so registering twice (a second ``create_app``
  in the tests) is harmless.
* The group ``assistant`` stays hidden unless the template global ``assistant_enabled`` is true.

All labels are pt-BR UI text, which lives only in ``interfaces/`` (CLAUDE.md section 3).
"""

from dataclasses import dataclass, field

# (id, label shown in capitals above the group, or None for the unlabeled first group)
GROUPS: tuple[tuple[str, str | None], ...] = (
    ("main", None),
    ("movement", "MOVIMENTO"),
    ("wealth", "PATRIMÔNIO"),
    ("planning", "PLANEJAMENTO"),
    ("assistant", "ASSISTENTE"),
    ("system", "SISTEMA"),
)
ASSISTANT_GROUP = "assistant"

# 24x24 stroke paths (the boards' icon set). Entries pass one of these (or their own) as ``icon``.
ICONS: dict[str, str] = {
    "dashboard": "M4 4h7v9H4z M13 4h7v5h-7z M13 11h7v9h-7z M4 15h7v5H4z",
    "analises": "M4 20V10 M10 20V4 M16 20v-7 M21 20H3",
    "carta": (
        "M4 6.5A1.5 1.5 0 0 1 5.5 5h13A1.5 1.5 0 0 1 20 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-13"
        "A1.5 1.5 0 0 1 4 17.5z M4 8l8 5 8-5"
    ),
    "entries": "M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01",
    "cards": (
        "M3 6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15"
        "A1.5 1.5 0 0 1 3 17.5z M3 10h18 M7 15h4"
    ),
    "accounts": "M3 10l9-6 9 6 M5 10v8 M9.5 10v8 M14.5 10v8 M19 10v8 M3 20h18",
    "investments": "M3 17l6-6 4 4 8-9 M15 6h6v6",
    "networth": "M12 3l9 4.5-9 4.5-9-4.5z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5",
    "budget": (
        "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 12h.01"
    ),
    "recurring": "M17 2l3 3-3 3 M20 5H9a5 5 0 0 0-5 5 M7 22l-3-3 3-3 M4 19h11a5 5 0 0 0 5-5",
    "ese": (
        "M12 3v3 M12 18v3 M3 12h3 M18 12h3 M6 6l2 2 M16 16l2 2 M18 6l-2 2 M8 16l-2 2 "
        "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"
    ),
    "categories": "M3 12V4h8l10 10-8 8z M7.5 8.5h.01",
    "importar": "M12 15V4 M8 8l4-4 4 4 M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
    "diagnostics": "M12 8v5 M12 16.5h.01 M12 3l9.5 17h-19z",
    "pergunte": (
        "M5 5.5A1.5 1.5 0 0 1 6.5 4h11A1.5 1.5 0 0 1 19 5.5v9a1.5 1.5 0 0 1-1.5 1.5H11l-4 4v-4"
        "H6.5A1.5 1.5 0 0 1 5 14.5z"
    ),
    "assistente": (
        "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z "
        "M12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z"
    ),
}

# The tab bar (mobile) has these three pages, the central "Novo" button and "Mais". "Mais" is the
# highlighted tab for every other page.
TAB_IDS: tuple[str, ...] = ("dashboard", "entries", "cards")


@dataclass(frozen=True)
class NavEntry:
    id: str
    label: str
    icon: str  # SVG path data, 24x24 viewBox
    href: str
    group: str = "main"
    order: int = 100
    badge: bool = False
    sidebar: bool = True
    hint: str = ""  # one line shown next to the entry on the "Mais" page
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class NavItem:
    """An entry as the template sees it: the entry plus whether it is the current page."""

    entry: NavEntry
    current: bool

    @property
    def label(self) -> str:
        return self.entry.label

    @property
    def href(self) -> str:
        return self.entry.href

    @property
    def icon(self) -> str:
        return self.entry.icon

    @property
    def badge(self) -> bool:
        return self.entry.badge

    @property
    def hint(self) -> str:
        return self.entry.hint


@dataclass(frozen=True)
class NavGroup:
    id: str
    label: str | None
    items: list[NavItem] = field(default_factory=lambda: [])


_registry: dict[str, NavEntry] = {}


def register(entry: NavEntry) -> None:
    """Add (or replace, by ``id``) a navigation entry."""
    if entry.group not in {g for g, _ in GROUPS}:
        raise ValueError(f"unknown navigation group: {entry.group}")
    _registry[entry.id] = entry


def unregister(entry_id: str) -> None:
    """Remove an entry (used by tests that register a temporary page)."""
    _registry.pop(entry_id, None)


def entries() -> list[NavEntry]:
    """Every registered entry in display order (group, then ``order``, then label)."""
    group_rank = {g: n for n, (g, _) in enumerate(GROUPS)}
    return sorted(_registry.values(), key=lambda e: (group_rank[e.group], e.order, e.label))


def resolve(key: str | None) -> str | None:
    """The entry id a page key belongs to (``"flow"`` -> ``"accounts"``); ``None`` if none does."""
    if not key:
        return None
    if key in _registry:
        return key
    for entry in _registry.values():
        if key in entry.aliases:
            return entry.id
    return None


def groups(
    active: str | None = None,
    assistant_enabled: bool = False,
    *,
    sidebar_only: bool = True,
    exclude: tuple[str, ...] = (),
) -> list[NavGroup]:
    """The menu as groups of items, ``active`` (a page key) marked as current. Empty groups and the
    assistant group (unless enabled) are left out."""
    current = resolve(active)
    labels = dict(GROUPS)
    by_group: dict[str, list[NavItem]] = {}
    for entry in entries():
        if (sidebar_only and not entry.sidebar) or entry.id in exclude:
            continue
        if entry.group == ASSISTANT_GROUP and not assistant_enabled:
            continue
        by_group.setdefault(entry.group, []).append(NavItem(entry, entry.id == current))
    return [NavGroup(g, labels[g], by_group[g]) for g, _ in GROUPS if g in by_group]


def more_groups(assistant_enabled: bool = False) -> list[NavGroup]:
    """What the "Mais" page lists: every registered entry except the tab bar pages (so a page added
    by a later package is reachable on a phone as soon as it registers)."""
    return groups(None, assistant_enabled, sidebar_only=False, exclude=TAB_IDS)


def in_more(active: str | None) -> bool:
    """Whether the mobile "Mais" tab is the highlighted one (the page is not a tab page)."""
    current = resolve(active)
    return current is not None and current not in TAB_IDS


def _builtin() -> None:
    for entry in (
        NavEntry("dashboard", "Painel", ICONS["dashboard"], "/", "main", 10),
        NavEntry("entries", "Lançamentos", ICONS["entries"], "/entries", "movement", 10),
        NavEntry(
            "cards", "Cartões", ICONS["cards"], "/cards", "movement", 20, aliases=("purchase",)
        ),
        NavEntry(
            "accounts", "Contas", ICONS["accounts"], "/accounts", "movement", 30,
            hint="saldos informados e instituições", aliases=("flow",),
        ),
        NavEntry(
            "investments", "Investimentos", ICONS["investments"], "/investments", "wealth", 10,
            hint="avaliações, aportes, rendimento e alocação",
        ),
        NavEntry(
            "networth", "Patrimônio", ICONS["networth"], "/networth", "wealth", 20,
            hint="caixa + investimentos − faturas",
        ),
        NavEntry(
            "budget", "Orçamento", ICONS["budget"], "/budget", "planning", 10,
            hint="metas por categoria × média dos meses",
        ),
        NavEntry(
            "recurring", "Recorrentes", ICONS["recurring"], "/recurring", "planning", 20,
            hint="assinaturas, custos fixos e alertas",
        ),
        NavEntry(
            "categories", "Categorias", ICONS["categories"], "/categories", "planning", 40,
            hint="grupos, cores e orçamento",
        ),
        NavEntry(
            "diagnostics", "Diagnóstico", ICONS["diagnostics"], "/diagnostics", "system", 10,
            sidebar=False, hint="falhas registradas neste computador",
        ),
    ):  # fmt: skip
        register(entry)


_builtin()
