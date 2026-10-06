"""Front v3 foundation: layout, navigation, route-module mechanism, tokens and shared UI macros."""

import json
import re
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from financas.container import Container
from financas.infrastructure.db.seed import seed_categories
from financas.infrastructure.settings import Settings
from financas.interfaces.web import nav
from financas.interfaces.web.app import create_app
from financas.interfaces.web.routes import MODULES, WebContext

HEADERS = {"host": "localhost", "origin": "http://localhost"}


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'data' / 'f.db'}",
        data_dir=tmp_path / "data",
        _env_file=None,  # type: ignore[call-arg]
    )
    c = Container(settings)
    c.migrate()
    seed_categories(c.uow)
    return c


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(
        create_app(container), base_url="http://localhost", follow_redirects=False, headers=HEADERS
    )


@pytest.fixture
def macro_client(container: Container) -> Iterator[TestClient]:
    """An app with one extra route module that renders a template string with the real Jinja env."""

    def register(app: FastAPI, ctx: WebContext) -> None:
        @app.post("/_test/render", response_class=HTMLResponse)
        async def render(src: str) -> HTMLResponse:
            return HTMLResponse(ctx.templates.env.from_string(src).render())

    module = types.ModuleType("probe")
    module.register = register  # type: ignore[attr-defined]
    MODULES.append(module)
    try:
        yield TestClient(
            create_app(container),
            base_url="http://localhost",
            follow_redirects=False,
            headers=HEADERS,
        )
    finally:
        MODULES.remove(module)


def rendered(client: TestClient, source: str) -> str:
    response = client.post("/_test/render", params={"src": '{% from "_ui.html" import ' + source})
    assert response.status_code == 200, response.text
    return response.text


# --- layout ---------------------------------------------------------------------------------------


def test_sidebar_is_rendered_from_the_navigation_list(client: TestClient) -> None:
    page = client.get("/entries").text
    sidebar = page[page.index('<nav class="sidebar ink"') : page.index("</nav>")]
    expected = [e for e in nav.entries() if e.sidebar]
    for entry in expected:
        assert f'href="{entry.href}"' in sidebar, entry.id
        assert entry.label in sidebar
    # nothing else: a page that is not registered is not in the menu
    assert set(re.findall(r'class="item" href="([^"]+)"', sidebar)) == {e.href for e in expected}
    assert "Diagnóstico" not in sidebar  # sidebar=False: reachable from "Mais"
    for label in ("MOVIMENTO", "PATRIMÔNIO", "PLANEJAMENTO"):
        assert label in sidebar
    assert "finanças" in sidebar and "PESSOAIS" in sidebar and "Novo lançamento" in sidebar
    assert "Dados guardados só neste computador" in sidebar
    assert "Assistente desligado: nada sai deste computador" in sidebar


def test_the_current_page_is_highlighted(client: TestClient) -> None:
    page = client.get("/entries").text
    assert page.count('aria-current="page"') == 2  # sidebar item and tab
    assert re.search(r'href="/entries"\s+aria-current="page"', page)
    purchase = client.get("/cards/purchase").text
    assert re.search(r'class="item" href="/cards"\s+aria-current="page"', purchase)


def test_assistant_group_is_not_shown(client: TestClient) -> None:
    page = client.get("/").text
    for text in ("ASSISTENTE", "Pergunte ao livro", "Transparência"):
        assert text not in page


def test_command_trigger_is_a_harmless_keyboard_reachable_button(client: TestClient) -> None:
    page = client.get("/").text
    match = re.search(r"<button[^>]*data-palette-open[^>]*>", page)
    assert match is not None
    tag = match.group(0)
    assert 'type="button"' in tag and "aria-label" in tag and 'tabindex="-1"' not in tag
    assert "Perguntar ou registrar" in page and "⌘K" in page


def test_tab_bar_has_the_five_destinations(client: TestClient) -> None:
    page = client.get("/budget").text
    tabbar = page[page.index('<nav class="tabbar"') :]
    tabbar = tabbar[: tabbar.index("</nav>")]
    for label in ("Painel", "Lançamentos", "Cartões", "Mais"):
        assert f"<span>{label}</span>" in tabbar
    assert 'aria-label="Novo lançamento"' in tabbar
    assert re.search(r'href="/more"\s+aria-current="page"', tabbar)  # Orçamento lives under "Mais"


def test_more_page_lists_registered_pages_and_diagnostics(client: TestClient) -> None:
    page = client.get("/more").text
    for label in (
        "Investimentos",
        "Patrimônio",
        "Orçamento",
        "Recorrentes",
        "Contas",
        "Diagnóstico",
    ):
        assert label in page


def test_pages_have_no_inline_handlers_and_load_tokens_before_the_app_css(
    client: TestClient,
) -> None:
    for path in ("/", "/entries", "/cards", "/more", "/missing"):
        text = client.get(path).text
        assert not re.search(r"\son[a-z]+=", text), path
        assert "<script>" not in text, path
        assert text.index("/static/tokens.css") < text.index("/static/app.css"), path
        assert re.search(r'<script src="/static/ui\.js" defer>', text), path


def test_head_follows_the_front_v3_handoff_contract(client: TestClient) -> None:
    """snippets/base-head.html: data block, fonts, CSS, the blocking privacy script, the modules."""
    for path in ("/", "/entries", "/more", "/missing"):
        response = client.get(path)
        text = response.text
        head = text[: text.index("</head>")]
        config = re.search(r'<script type="application/json" id="app-config">(.*?)</script>', head)
        assert config and json.loads(config[1]) == {"locale": "pt-BR", "currency": "BRL"}, path
        order = [
            "data-fp-theme-color>",
            '<script src="/static/fp-theme.js"></script>',  # blocking classic, first
            'id="app-config"',
            "/static/fonts/fonts.css",
            "/static/tokens.css",
            "/static/components.css",
            "/static/charts.css",
            "/static/charts-extra.css",
            "/static/fp-privacy.css",
            "/static/fp-theme.css",
            "/static/cards.css",
            '<script src="/static/fp-privacy.js"></script>',  # classic, no defer/async/module
            '<script type="module" src="/static/fp-money.js">',
            '<script type="module" src="/static/fp-ui.js">',
            '<script type="module" src="/static/fp-widgets.js">',
            '<script type="module" src="/static/charts.js">',
            '<script type="module" src="/static/fp-scrub.js">',
            "/static/app.css",
        ]
        positions = [head.index(item) for item in order]
        assert positions == sorted(positions), path
        for font in ("public-sans-latin-wght-normal", "source-serif-4-latin-opsz-normal"):
            assert re.search(
                rf'<link rel="preload" href="/static/fonts/{font}\.woff2"[^>]*\bcrossorigin', head
            )
        assert "?v=" not in head  # one URL per module: a second copy would format twice
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert (
            "'unsafe-inline'"
            not in response.headers["content-security-policy"].split("script-src")[1].split(";")[0]
        )


def test_privacy_controls_are_in_the_shell_and_on_the_more_page(client: TestClient) -> None:
    home = client.get("/").text
    top = home[home.index('class="side-actions"') : home.index('class="nav-groups"')]
    assert "data-fp-privacy" not in top and "data-fp-theme-group" not in top
    side = home[home.index('class="side-utilities"') : home.index("</nav>")]
    assert 'data-fp-privacy="toggle"' in side and 'data-fp-privacy="settings"' in side
    assert 'aria-keyshortcuts="P"' in side and 'aria-label="Ocultar valores"' in side
    dialog = home[home.index('<dialog class="fp-priv-panel') :]
    assert dialog.index("</dialog>") < dialog.index("</body>")
    assert len(re.findall(r"data-fp-privacy-group=", dialog)) == 5
    more = client.get("/more").text
    assert 'data-fp-privacy="toggle"' in more[more.index("<main>") :]  # phones have no sidebar


def test_theme_switcher_is_in_the_sidebar_and_on_the_more_page(client: TestClient) -> None:
    """Patch 3.1/3.2: theme radiogroup in the sidebar footer with the privacy group; «Aparência»."""
    home = client.get("/").text
    side = home[home.index('class="side-utilities"') : home.index("</nav>")]
    assert home.index('class="backup-box"') < home.index('class="side-utilities"')
    assert side.index("data-fp-privacy") < side.index("data-fp-theme-group")
    for mode in ("light", "dark", "system"):
        assert f'data-fp-theme-option="{mode}"' in side
    assert 'role="radiogroup" aria-label="Tema"' in side and side.count('aria-checked="true"') >= 1
    more = client.get("/more").text
    body = more[more.index("<main>") :]
    assert "Aparência" in body and body.count("data-fp-theme-group") == 1


def test_theme_script_is_blocking_and_first_in_head(client: TestClient) -> None:
    head = (text := client.get("/").text)[: text.index("</head>")]
    tag = re.search(r'<script src="/static/fp-theme\.js"[^>]*>', head)
    assert tag and not re.search(r"defer|async|module", tag[0])
    assert head.index("fp-theme.js") < head.index(".css")
    script = client.get("/static/fp-theme.js").text
    assert (
        "fp_theme" in script and "data-theme-pref" in script
    )  # the stored key and the pre-paint attribute


def test_files_stay_csp_clean(client: TestClient) -> None:
    for name in ("fp-theme.js", "fp-ui.js", "fp-widgets.js", "fp-scrub.js"):
        js = client.get(f"/static/{name}").text
        assert not re.search(r"\beval\(|new Function\(|hx-on:", js), name


def test_static_files_have_the_mime_types_the_modules_need(client: TestClient) -> None:
    for name, mime in (
        ("fp-money.js", "text/javascript"),
        ("fp-ui.js", "text/javascript"),
        ("charts.js", "text/javascript"),
        ("fp-privacy.js", "text/javascript"),
        ("fp-theme.js", "text/javascript"),
        ("fp-widgets.js", "text/javascript"),
        ("fp-scrub.js", "text/javascript"),
        ("fp-privacy.css", "text/css"),
        ("fp-theme.css", "text/css"),
        ("cards.css", "text/css"),
        ("components.css", "text/css"),
        ("charts-extra.css", "text/css"),
        ("fonts/fonts.css", "text/css"),
    ):
        response = client.get(f"/static/{name}")
        assert response.status_code == 200 and response.headers["content-type"].startswith(mime), (
            name
        )
        assert response.headers["x-content-type-options"] == "nosniff", name


def test_tokens_and_ui_script_are_served(client: TestClient) -> None:
    tokens = client.get("/static/tokens.css")
    assert tokens.status_code == 200 and "--sp-press" in tokens.text and "--sh-3" in tokens.text
    assert ".dl-pos" in tokens.text and "--dv-ink" in tokens.text
    script = client.get("/static/ui.js")
    assert script.status_code == 200 and "FinUI" in script.text
    css = client.get("/static/app.css").text
    assert "fonts.googleapis" not in css and "--line:" not in css  # --line is owned by tokens.css
    assert "--text:" not in css and "--sans:" not in css  # the roles are the tokens


def test_dashboard_hero_uses_the_odometer(client: TestClient) -> None:
    page = client.get("/").text
    assert 'data-odometer="0"' in page and 'class="od od-dark"' in page


# --- route modules -------------------------------------------------------------------------


def test_route_modules_are_registered_with_the_context_and_can_add_nav_entries(
    container: Container,
) -> None:
    seen: list[WebContext] = []

    def register(app: FastAPI, ctx: WebContext) -> None:
        seen.append(ctx)
        nav.register(
            nav.NavEntry("probe", "Sonda", nav.ICONS["analises"], "/probe", "main", 25, badge=True)
        )

        @app.get("/probe", response_class=HTMLResponse)
        def probe() -> HTMLResponse:
            return HTMLResponse(f"{ctx.today().isoformat()}|{ctx.opt_int('7')}|{ctx.money('1,50')}")

    module = types.ModuleType("probe")
    module.register = register  # type: ignore[attr-defined]
    MODULES.append(module)
    try:
        client = TestClient(create_app(container), base_url="http://localhost", headers=HEADERS)
        assert client.get("/probe").text.endswith("|7|150")
        ctx = seen[0]
        assert ctx.c is container and callable(ctx.render) and callable(ctx.lookups)
        assert ctx.templates is not None and ctx.back("/x").status_code == 303
        page = client.get("/").text
        assert 'href="/probe"' in page and "Sonda" in page and "nav-badge" in page
        assert "Sonda" in client.get("/more").text  # reachable from a phone
    finally:
        MODULES.remove(module)
        nav.unregister("probe")
    plain = TestClient(create_app(container), base_url="http://localhost", headers=HEADERS)
    assert "Sonda" not in plain.get("/").text and plain.get("/probe").status_code == 404


# --- shared UI macros ----------------------------------------------------------------------


def test_odometer_prints_the_final_digits_and_the_cents_tail(macro_client: TestClient) -> None:
    html = rendered(macro_client, "odometer %}{{ odometer(343742, 56) }}")
    assert 'aria-label="R$ 3.437,42"' in html and 'data-odometer="343742"' in html
    assert html.count('class="od-d"') == 6 and 'class="od-tail"' in html
    assert "translateY(-3em)" in html and "translateY(-7em)" in html
    assert "--od-size: 56px" in html and "od-dark" not in html


def test_odometer_negative_dark_and_without_prefix(macro_client: TestClient) -> None:
    html = rendered(
        macro_client, "odometer %}{{ odometer(-1234, 'clamp(54px, 9vw, 84px)', dark=True) }}"
    )
    assert 'class="od-sign"' in html and "od-dark" in html and "clamp(54px, 9vw, 84px)" in html
    assert 'aria-label="−R$ 12,34"' in html or 'aria-label="-R$ 12,34"' in html
    bare = rendered(macro_client, "odometer %}{{ odometer(5, 20, prefix=False) }}")
    assert "od-cur" not in bare and 'aria-label="0,05"' in bare and "od-sm" in bare


def test_stroke_gauge_tones_and_labels(macro_client: TestClient) -> None:
    def gauge(call: str) -> str:
        return rendered(macro_client, "stroke_gauge %}{{ stroke_gauge(" + call + ") }}")

    ok = gauge("51.3")
    assert "sg-forest" in ok and "--sg-w: 51.30%" in ok and 'aria-label="Comprometido 51,3%"' in ok
    warn = gauge("81.9")
    assert "sg-gold" in warn and "atenção" in warn
    over = gauge("120")
    assert "sg-negative" in over and "--sg-w: 100.00%" in over and "acima do limite" in over
    unset = gauge("0, unset=True")
    assert "sg-unset" in unset and 'aria-label="Limite não informado"' in unset
    assert "sg-fill" not in unset
    custom = gauge("40, tone='gold', label='Meta de poupança'")
    assert "sg-gold" in custom and 'aria-label="Meta de poupança"' in custom
    extra = gauge("60, add=10")
    assert "--sg-add: 10.00%" in extra
    capped = gauge("95, add=20")
    assert "--sg-add: 5.00%" in capped  # the extra segment never leaves the bar


def test_delta_chip_skeleton_and_loading(macro_client: TestClient) -> None:
    chip = rendered(macro_client, "delta_chip %}{{ delta_chip('+67,42 vs agosto', 'pos', 'up') }}")
    assert 'class="dl dl-chip dl-pos dl-up"' in chip and "+67,42 vs agosto" in chip
    down = rendered(macro_client, "delta_chip %}{{ delta_chip('−187,43', 'neg', 'down') }}")
    assert "dl-neg" in down and "dl-dn" in down
    sk = rendered(macro_client, "skeleton %}{{ skeleton(120, 12, 8) }}")
    assert "sk-box" in sk and "width: 120px" in sk and "height: 12px" in sk and "aria-hidden" in sk
    loading = rendered(
        macro_client, "state_loading %}{{ state_loading('Carregando cartões…', 2) }}"
    )
    assert 'aria-busy="true"' in loading and "Carregando cartões…" in loading
    assert loading.count("sk-line") == 2


def test_state_blocks(macro_client: TestClient) -> None:
    empty = rendered(
        macro_client,
        "state_empty %}{{ state_empty('Nenhum cartão cadastrado', 'Informe o apelido.', "
        "'Cadastrar cartão', '/cards', icon='card') }}",
    )
    assert (
        "Nenhum cartão cadastrado" in empty and 'href="/cards"' in empty and "state-title" in empty
    )
    assert 'role="alert"' not in empty  # empty is not an error
    bare = rendered(macro_client, "state_empty %}{{ state_empty('Nada aqui') }}")
    assert "<a " not in bare
    error = rendered(
        macro_client,
        "state_error %}{{ state_error('Algo deu errado', 'Nada foi perdido.', code='abc123', "
        "action_label='Voltar', action_href='/') }}",
    )
    assert 'role="alert"' in error and "abc123" in error and 'href="/"' in error
    sparse = rendered(
        macro_client,
        "state_sparse %}{{ state_sparse(3, 8, text='Faltam 5 saldos.', action_label='Registrar um "
        "saldo', action_href='/accounts') }}",
    )
    assert "3 de 8" in sparse and "Poucos dados ainda" in sparse and "dv-tip" in sparse
    assert 'href="/accounts"' in sparse and "--sg-w: 37.50%" in sparse


def test_error_pages_use_the_error_state(client: TestClient) -> None:
    response = client.get("/missing")
    assert response.status_code == 404
    assert 'role="alert"' in response.text and "Página não encontrada" in response.text
    assert 'href="/"' in response.text


def test_dashboard_header_has_no_second_primary_action(client: TestClient) -> None:
    """The sidebar owns «Novo lançamento»; the Painel header keeps only its own context."""
    page = client.get("/").text
    main = page[page.index("<main>") : page.index("</main>")]
    header = main[main.index('<header class="page-head"') : main.index("</header>")]
    assert "Novo lançamento" not in header and 'class="btn"' not in header
    assert 'class="pn-month"' in header and 'class="head-tools"' in header
    assert page.count("Novo lançamento") >= 2  # sidebar button + the tab bar's labelled «Novo» link


def test_dashboard_kpis_are_odometers_with_privacy_and_accessible_names(client: TestClient) -> None:
    page = client.get("/").text
    kpis = page[page.index('class="pn-kpis"') : page.index('class="pn-pair"')]
    figures = re.findall(r'<span class="od[^"]*" role="img" aria-label="([^"]+)"[^>]*>', kpis)
    assert len(figures) == 4 and all(label.startswith(("R$", "-R$")) for label in figures)
    assert kpis.count('data-private="transactions"') >= 4 and kpis.count("data-odometer=") == 4
    script = client.get("/static/fin-charts.js").text
    assert "FinUI.odometer" in script or "window.FinUI" in script  # chart figures roll too


def test_side_utilities_targets_cannot_outgrow_the_bar(client: TestClient) -> None:
    """app.css `button { min-height: 48px }` once pushed the 28 px targets out of the 32 px bar."""
    css = client.get("/static/screens.css").text
    block = css[css.index(".side-utilities") :]
    assert "min-height: 0" in block and "box-shadow: none" in block and "height: 32px" in block


def test_every_chart_figure_is_an_odometer_on_the_first_paint(client: TestClient) -> None:
    """Ritmo and Fluxo print their figure on the server: the odometer exists before the JSON."""
    for path in ("/", "/analises"):
        page = client.get(path).text
        figures = re.findall(r'<span class="fc-num[^"]*"[^>]*>(.*?)</span></div>', page, flags=re.S)
        cards = page.count('class="fc-num')
        assert cards == 2 and len(figures) == 2, path
        for figure in figures:
            assert (
                "data-odometer=" in figure and 'class="od' in figure and "sk-inline" not in figure
            )
            assert "fc-num-od" in figure and "data-private" not in figure
            assert re.search(r'aria-label="-?R\$[^"]*\d,\d\d"', figure), (
                figure
            )  # the full currency text
        assert page.count('data-private="transactions"') >= 2
    head = client.get("/").text
    assert head.index("/static/ui.js") < head.index(
        "/static/fin-charts.js"
    )  # FinUI exists when the chart runs


def test_static_files_are_revalidated(client: TestClient) -> None:
    assert client.get("/static/fin-charts.js").headers["cache-control"] == "no-cache"
