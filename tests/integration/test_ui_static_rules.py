"""Static rules of stylesheets and scripts (no browser): privacy blur and dialog motion."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src/financas/interfaces/web/static"
DIALOG_SHEETS = ("app.css", "palette.css", "components.css", "fp-privacy.css", "screens.css")


def css(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_privacy_blur_strengths_and_hover_reveal_is_opt_in() -> None:
    tokens = css("tokens.css")
    assert "--privacy-blur: blur(8px)" in tokens or "--privacy-blur:blur(8px)" in tokens
    assert re.search(r"--privacy-blur-lg:\s*blur\(max\(12px", tokens)
    selectors = [
        part.strip()
        for part in re.split(r",\s*\n|\{", tokens)
        if ":hover" in part and "data-privacy-mask" in part and "\n" not in part.strip()
    ]
    assert len(selectors) >= 8
    assert all('[data-privacy-reveal-hover="true"]' in sel for sel in selectors), selectors


def test_donut_ring_text_is_blurred_and_not_selectable() -> None:
    tokens = css("tokens.css")
    assert re.search(
        r'\[data-privacy-mask~="transactions"\] \.fc-ring text,\s*'
        r'\[data-privacy-mask~="transactions"\] \.fc-ring-box \[data-private\]:not\(\.money\)\s*'
        r"\{[^}]*filter:var\(--privacy-blur-lg\)[^}]*user-select:none",
        tokens,
    )
    charts = css("fin-charts.js")
    assert '"data-private": "transactions", "data-private-size": "lg"' in charts


def test_dialog_keyframes_animate_only_opacity_and_transform() -> None:
    tokens = css("tokens.css")
    for name in ("dialin", "dlg-scrim"):
        body = re.search(r"@keyframes " + name + r"\{(.*?)\}\}?\s*(?=[@.\n]|$)", tokens, re.S)
        assert body, name
        declared = set(re.findall(r"([a-z-]+)\s*:", body.group(1)))
        assert declared <= {"opacity", "transform"}, (name, declared)


def test_dialog_backdrops_are_static_8px_blur_and_surfaces_contain_paint() -> None:
    tokens = css("tokens.css")
    assert "--dlg-blur:blur(8px)" in tokens
    assert "--dlg-ms:180ms" in tokens and "--dlg-ease:cubic-bezier(.16,1,.3,1)" in tokens
    for sheet in DIALOG_SHEETS:
        for rule in re.findall(r"[^{}]*::backdrop\s*\{[^}]*\}", css(sheet)):
            assert "blur(" not in rule or "--dlg-blur" in rule, (sheet, rule)
            assert "filter:" not in re.sub(r"(-webkit-)?backdrop-filter:[^;}]*", "", rule), rule
    for sheet, selector in (
        ("app.css", ".dialog"),
        ("palette.css", "dialog.palette"),
        ("components.css", ".dlg"),
        ("screens.css", ".merge-dialog"),
    ):
        assert re.search(
            re.escape(selector) + r"(\[open\])?\s*\{[^}]*contain:\s*paint", css(sheet)
        ), sheet
    assert "dialog[data-opening]{will-change:transform,opacity}" in css("components.css")


def test_opening_marker_is_released_by_the_animation_events() -> None:
    script = css("fp-ui.js")
    for needle in ("data-opening", "animationend", "transitionend", "OPENING_FALLBACK_MS"):
        assert needle in script
