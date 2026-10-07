"""Selection Mode (static/selection-mode.js) in a real browser.

Runs ``tests/js/selection_harness.html``: server-rendered hooks in, behaviour out (counter and
plural, merge eligibility, locks and their message, the 5 s bubble, batch delete with a stubbed
server, Esc and Cancelar, selection kept across a swap). Skipped when no headless Chromium/Edge is
installed.
"""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_selection_mode_behaves_in_a_browser() -> None:
    report = run_harness("tests/js/selection_harness.html", "--window-size=1280,900")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    for name in (
        "1 selecionado (singular)",
        "merge enabled at 2",
        "mismatch message",
        "plan reason",
        "lock message in the bubble",
        "same lock again does not stack",
        "bubble leaves after 5 s",
        "confirm fetched with typed ids",
        "focus on Cancelar",
        "dialog removed after success",
        "result announced",
        "focus on Selecionar",
        "selection survives a row swap",
    ):
        assert f"PASS {name}" in report, name
    assert report.count("PASS") >= 80
