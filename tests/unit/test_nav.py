"""The data-driven navigation (``interfaces/web/nav.py``): order, groups, active page."""

from collections.abc import Iterator

import pytest

from financas.interfaces.web import nav


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    # Route modules register their pages when any test builds the app (the registry is global), so
    # these tests start from the built-in menu and put everything back afterwards.
    saved = {e.id: e for e in nav.entries()}
    for later in ("analises", "carta", "ese", "importar", "review"):
        nav.unregister(later)
    before = {e.id for e in nav.entries()}
    yield
    for entry in nav.entries():
        if entry.id not in before:
            nav.unregister(entry.id)
    for later in ("analises", "carta", "ese", "importar", "review"):
        if later in saved:
            nav.register(saved[later])


def _labels(groups: list[nav.NavGroup]) -> list[list[str]]:
    return [[item.label for item in g.items] for g in groups]


def test_built_in_menu_matches_the_pages_that_exist() -> None:
    groups = nav.groups("dashboard")
    assert [g.id for g in groups] == ["main", "movement", "wealth", "planning"]
    assert [g.label for g in groups] == [None, "MOVIMENTO", "PATRIMÔNIO", "PLANEJAMENTO"]
    assert _labels(groups) == [
        ["Painel"],
        ["Lançamentos", "Cartões", "Contas"],
        ["Investimentos", "Patrimônio"],
        ["Orçamento", "Recorrentes", "Categorias"],
    ]


def test_pages_of_later_packages_are_absent_until_they_register() -> None:
    assert not {e.id for e in nav.entries()} & {"analises", "carta", "ese", "importar"}
    nav.register(
        nav.NavEntry("analises", "Análises", nav.ICONS["analises"], "/analises", "main", 20)
    )
    nav.register(
        nav.NavEntry("carta", "Carta do mês", nav.ICONS["carta"], "/carta", "main", 30, badge=True)
    )
    nav.register(nav.NavEntry("ese", "E se…", nav.ICONS["ese"], "/ese", "planning", 30))
    groups = nav.groups("carta")
    assert _labels(groups)[0] == ["Painel", "Análises", "Carta do mês"]
    assert _labels(groups)[3] == ["Orçamento", "Recorrentes", "E se…", "Categorias"]
    carta = next(i for g in groups for i in g.items if i.entry.id == "carta")
    assert carta.badge and carta.current


def test_order_inside_a_group_then_label() -> None:
    nav.register(nav.NavEntry("zz", "Zebra", "M0 0", "/zz", "wealth", 5))
    nav.register(nav.NavEntry("aa", "Abelha", "M0 0", "/aa", "wealth", 5))
    wealth = next(g for g in nav.groups() if g.id == "wealth")
    assert [i.label for i in wealth.items] == ["Abelha", "Zebra", "Investimentos", "Patrimônio"]


def test_the_active_page_is_marked_and_aliases_resolve() -> None:
    def current(active: str | None) -> list[str]:
        return [i.entry.id for g in nav.groups(active) for i in g.items if i.current]

    assert current("entries") == ["entries"]
    assert current("purchase") == ["cards"]  # the purchase form belongs to Cartões
    assert current("flow") == ["accounts"]  # the daily flow belongs to Contas
    assert current("error") == [] and current(None) == [] and current("") == []
    assert nav.resolve("flow") == "accounts" and nav.resolve("nope") is None


def test_the_assistant_group_is_hidden_unless_enabled() -> None:
    nav.register(
        nav.NavEntry("pergunte", "Pergunte ao livro", "M0 0", "/pergunte", "assistant", 10)
    )
    assert "assistant" not in [g.id for g in nav.groups()]
    assert "assistant" in [g.id for g in nav.groups(assistant_enabled=True)]


def test_register_replaces_by_id_and_rejects_unknown_groups() -> None:
    nav.register(nav.NavEntry("x", "Um", "M0 0", "/x", "wealth", 50))
    nav.register(nav.NavEntry("x", "Dois", "M0 0", "/x", "wealth", 50))
    assert [e.label for e in nav.entries() if e.id == "x"] == ["Dois"]
    with pytest.raises(ValueError):
        nav.register(nav.NavEntry("y", "Y", "M0 0", "/y", "nowhere"))


def test_more_lists_everything_but_the_tab_pages_and_includes_sidebar_hidden_entries() -> None:
    more = nav.more_groups()
    ids = [i.entry.id for g in more for i in g.items]
    assert not set(ids) & set(nav.TAB_IDS)
    assert "diagnostics" in ids and "networth" in ids  # diagnostics is not in the sidebar
    assert "diagnostics" not in [i.entry.id for g in nav.groups() for i in g.items]


def test_more_tab_is_highlighted_for_pages_outside_the_tab_bar() -> None:
    assert nav.in_more("budget") and nav.in_more("flow") and nav.in_more("diagnostics")
    assert not any(
        nav.in_more(k) for k in ("dashboard", "entries", "cards", "purchase", "error", None)
    )
