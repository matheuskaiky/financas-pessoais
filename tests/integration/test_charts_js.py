"""fin-charts.js, fp-money.js and the <fp-patrimonio> island in a real browser engine (math,
formatting through the shared currency layer, scrubbing, keyboard, ARIA).

Runs ``tests/js/charts_harness.html`` in headless Chromium/Edge. Skipped when no such browser is
installed (it is not a project dependency).
"""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_charts_js_money_island_maths_formatting_and_interaction() -> None:
    report = run_harness("tests/js/charts_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert report.count("PASS") >= 30 + 17  # + the golden currency vectors
    assert "PASS island upgrades" in report and "PASS golden 1485000 de-DE auto" in report


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_chart_figures_are_odometers_that_survive_hydration_and_scrubbing() -> None:
    """ui.js + fin-charts.js together: no TypeError, the same .od is updated in place."""
    report = run_harness("tests/js/figure_harness.html", "--virtual-time-budget=30000")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert report.count("PASS") == 15
