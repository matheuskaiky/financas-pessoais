# Front v3 handoff: integration report

Date: 2026-10-04. Branch `dev`, base commit `9c147d1`. Source: `docs/financas-front-v3-handoff/` (22 files, all passed `sha256sum -c` from HANDOFF_NOTES section 6 before use).
Execution state: `docs/FRONT_V3_CHECKLIST.md`.

## 1. Result

| Check | Before (audit of 2026-10-04) | After |
|---|---|---|
| `pytest -q` | 1236 passed, 1 warning | **1263 passed, 0 failed, 0 skipped**, 1 warning (see 5.4) |
| `ruff check .` | clean | clean |
| `ruff format --check .` | 163 files formatted | 168 files formatted |
| `pyright` | 0 errors | 0 errors, 0 warnings, 0 informations |
| `lint-imports` | 5 kept | 5 kept, 0 broken |

+27 tests: 23 in `tests/unit/test_fp_money.py` (golden vectors, markup, escaping, install), 3 in `test_web_foundation.py` (head contract, privacy controls in the shell and on `/more`, MIME types and `nosniff`), 1 browser test `test_privacy_js.py`. Browser harness `charts_harness.html` now runs 68 checks (17 shared currency vectors and 3 more currency checks, `<fp-patrimonio>` upgrade, plus the earlier chart checks); the new `privacy_harness.html` runs 20.

## 2. Inventory

Modified or new: **61 paths** (`git status`, excluding `planner.py` and `simulate.py`, which were already modified before this task). 33 modified, 2 renamed+edited, 2 deleted, 24 new.

**Static assets** (`src/financas/interfaces/web/static/`)
- Copied verbatim from the handoff: `tokens.css` (replaces the 155-line v3 file), `charts.js`, `charts.css` (the `<fp-patrimonio>` island), `fp-money.js`, `fp-ui.js`, `fp-privacy.js`, `fp-privacy.css`, `fonts/fonts.css`, 4 woff2 (Public Sans and Source Serif 4, latin and latin-ext), 2 OFL texts.
- Removed: `fonts/LibreFranklin-latin.woff2`, `fonts/SourceSerif4-latin.woff2`.
- Renamed: the repo's own `charts.js`/`charts.css` (pace, flow, donut) are now `fin-charts.js`/`fin-charts.css` (section 4.1).
- Edited: `app.css` (fonts, palette aliases, theme-aware colors), `screens.css`, `carta.css`, `ese.css`, `palette.css`, `fin-charts.css`, `fin-charts.js` (formatting through `fp-money.js`).

**Server** (`interfaces/web/`): new `fp_money.py` (handoff file, ruff-formatted); `app.py` (`fp_money.install(...)`); `routes/charts.py` (`net_worth_island()` and `nw_island` in the Painel and Análises contexts); `pyproject.toml` (E501 ignored for the vendored `fp_money.py`).

**Templates**: `base.html` (handoff head, `.ink` sidebar, eye toggle, dialog), `more.html` (mobile controls), `patrimonio.html` (new macro), `snippets/privacy-eye-toggle.html` and `snippets/privacy-dialog.html` (new), `dashboard.html`, `analises.html`, `_charts.html`, `_ui.html` (`odometer` and `delta_chip` gained `private`), and the amount call sites of `networth`, `accounts`, `flow`, `entries`, `cards`, `investments`, `budget`, `recurring`, `categories`, `purchase`, `_purchase_preview`, `carta` (font in the seal SVG).

**Tests**: `tests/golden/money.json`, `tests/html_text.py` (`visible()` helper), `tests/browser.py` (shared headless runner), `tests/unit/test_fp_money.py`, `tests/integration/test_privacy_js.py`, `tests/js/privacy_harness.html`, `tests/js/privacy_frame.html`; edited `test_web.py`, `test_web_foundation.py`, `test_web_charts.py`, `test_web_v3_screens.py`, `test_charts_js.py`, `charts_harness.html`.

**Docs** (git-ignored folder): `FRONT_V3_CHECKLIST.md`, this report; `CLAUDE.md` section 11 updated (tokens, fonts, money in templates, privacy mode, scripts and charts).

## 3. What was done, by plan step

1. **Assets and fonts.** Public Sans is `--sans` (OpenType `tnum`: amount columns align), Source Serif 4 stays `--serif`. Families are declared only in `fonts/fonts.css`; `app.css` has no `@font-face`. `body` carries `font-family: var(--sans)`, `background: var(--bg)`, `color: var(--text-primary)` and `button, input, select, textarea { font: inherit }`, as the handoff requires.
2. **Currency.** `brl` is untouched (`format_brl`: ASCII minus, normal space), so attributes, `aria-label`, `<option>` and the 105+ text assertions keep working. `money`, `currency`, `amount` and `config` come from `fp_money.install`. Visible amounts in 12 templates use `{{ cents|money(private="<group>") }}` or `money(sign="always", ...)`; 22 hard-typed `R$` (form prefixes and chart headers) now print `{{ config.currency_symbol }}`; the odometer and delta chip accept `private`. Python and the browser are tested against the same 17 vectors (`tests/golden/money.json`).
3. **Layout.** `base.html` follows `snippets/base-head.html` order exactly (data block, fonts, tokens, charts, privacy CSS, **blocking** `fp-privacy.js`, three modules from one directory with no `?v=`), then the app's own CSS and scripts. Eye and gear sit in `.side-actions`; `/more` has a "Privacidade" panel for phones; the dialog is before `</body>`. CSP is unchanged (`script-src 'self'`); the only inline script tags are `type="application/json"` data blocks, and the three tests that forbade any inline `<script>` now allow exactly those.
4. **Net-worth chart.** Painel and Análises render `<fp-patrimonio>` from inline JSON (`[iso, cents]`), badge "parcial" and a pt-BR footnote built in `net_worth_island()`. The old `data-chart="networth"` card is no longer used by any page.
5. **Tests.** Font assertions rewritten; harness and test cover `fp-money.js`; 23 amount assertions in `test_web.py` read through `visible()`; two `assert not <script>` regexes allow data blocks.

## 4. Deviations from the written plan, and why

1. **Name collision on `charts.js`/`charts.css`.** The handoff files are only the `<fp-patrimonio>` island. The repo's files of the same names also draw pace, flow and donut. Overwriting would have deleted three live charts. The handoff files keep their names (`base-head.html` loads `/static/charts.js`); the repo's became `fin-charts.*`. `fin-charts.js` now imports `./fp-money.js` (one currency layer in the browser, no `R$` literals left in it).
2. **`ui.js` kept.** The handoff `fp-ui.js` only marks scroll edges; the odometer and gauge are in `ui.js`. Both load; nothing was retired.
3. **`brl` does not delegate to `fp_money`** (the notes allow it, the task forbids changing its output). Two formatters remain on the server by design: `format_brl` (text) and `fp_money` (markup).
4. **The old money-input wrapper `.money` collided** with the handoff's `span.money`; it is now `.money-field` (templates and CSS).
5. **Palette aliasing.** The new `tokens.css` switches theme on `prefers-color-scheme`; the old `app.css` pinned light colors, which would have printed pale text on a white page in dark mode. Its names are now aliases of token roles, plus targeted literal fixes (section 6).
6. **Sidebar has class `ink`** (token scope for dark panels) so the eye widget and tokens inside it adopt dark values in a light page; the tests that matched `class="sidebar"` follow.
7. **Flow, pace and donut cards** are blurred as a whole plot box (`data-private` on `.fc-plot`) because their SVG text cannot be marked per value.

## 5. Verification

### 5.1 Privacy mode: layout shift and persistence

Two independent checks.
- Repository test (`tests/js/privacy_harness.html`, headless browser): toggling the mode leaves **every box at its exact rectangle** and the document height unchanged; `PerformanceObserver('layout-shift')` records **0**; the symbol keeps `filter: none` while the digits get `blur(8px)`; text content is untouched; masked values are announced as "Valor oculto"; the state is stored in `localStorage["fp_privacy_settings"]`; a second document sees `data-privacy-mask` **in a script placed right after the blocking head script** (so it is set before first paint); a group can be switched off alone; `P` toggles and is ignored while typing.
- Real app (session scratch server with synthetic data, Edge over the DevTools protocol, light and dark, 1500 px and 390 px): 847 elements measured on the Painel (36 marked values, all 36 blurred), **0 elements moved**, page size identical, **toggle CLS 0.0000** in all four configurations, state persisted across a reload.

Page-load CLS (not part of the privacy criterion): Painel 0.0029, Análises 0.011, Lançamentos, Cartões and Patrimônio 0. The shifts come from the legacy flow card of `fin-charts.js` (a few px when its legend and chips fill), existed before this work, and are far under the 0.1 "good" limit. Not fixed.

### 5.2 Light and dark contrast

Automated audit in the browser: for each visible text node, computed foreground (with opacity chain) over the effective background (ancestors composited), WCAG ratio. **14 pages x light/dark x 1500/390 px = 11,460 text elements, 0 below threshold** (4.5:1, or 3:1 for text of 24 px or more, or 18.66 px bold). All normal-size text is at 5.0:1 or better; the lowest element overall is a decorative "−" operator in the hero formula (large text, 4.46:1, needs 3:1). The privacy dialog was audited open in all four configurations: 0 failures.

The audit found and I fixed: the letter page in dark mode (paper backgrounds hard-coded light: text 1.08:1), "Apagar" links in the entries list (opacity .7: 3.4:1), card face captions (4.4:1), the letterhead "PESSOAIS" (3.4:1).

Method limits: the audit does not evaluate text over gradients or images beyond their `background-color`, SVG-only labels inside charts, or hover/focus states. Headless Edge does not advance CSS animations, so screenshots of odometers and chart draw-in are mid-animation; the logic is covered by the harness tests instead. The audit and screenshot scripts live in the session scratchpad and are not in the repository.

### 5.3 Other checks
- Handoff integrity: 22 of 22 hashes OK.
- MIME and `nosniff` on `.js`, `.css`, `.woff2` (tested).
- Head order and font preload `crossorigin` (tested). No `?v=` anywhere.

### 5.4 The one pytest warning
`StarletteDeprecationWarning` (`httpx` with `starlette.testclient`; suggests `httpx2`). It was already there in the baseline and is unrelated to the front end, so the "0 warnings" criterion is not met for this one line. It was not hidden.

## 6. Known limits (decide later)

- **Not yet marked for privacy:** text composed in Python (letter and its margin notes, simulator results `_ese_result`, `_palette_preview`, flash messages such as "O sistema esperava R$ ..."), values inside form inputs, SVG labels and tooltips of `fin-charts.js`, the donut centre value. Those still show amounts when the mode is on.
- `aria-label` of odometers still carries the amount (the handoff hides values from screen readers only on `[data-private]` elements it can reach).
- Dark mode follows the operating system only; there is no in-app theme switch (the handoff provides `data-theme` but no control).
- Remaining `brl`/`signed` uses in `_ese_result`, `_palette_preview` and `carta` are text-composition spots (`~`, `replace('R$ ', '')`) left as they were.
- `fin-charts.js` still contains the old `networth` kind (covered by its harness tests) although no page mounts it.
- Dozens of literal colors remain in the per-screen CSS (shadows, gradients, brand greens on dark panels); they pass the audit but are not tokenised.

## 7. Commit

This report is under `docs/`, which `.gitignore` excludes; it is committed with `git add -f` as requested. The integration is one commit on `dev` (not pushed): `feat(web): integrate front v3 handoff (Public Sans, fp-money, privacy mode, net-worth island, dark theme)`.
