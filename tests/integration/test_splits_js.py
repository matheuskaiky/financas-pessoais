"""The items editor, the row breakdown toggle and the merge toolbar (app.js) in a real browser.

Runs ``tests/js/splits_harness.html``: "Restam R$ …" follows the items and the total, a wrong sum
blocks the submit natively, items are not submitted while the editor is off, and the merge
toolbar shows with two picked rows. Skipped when no headless Chromium/Edge is installed.
"""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
@pytest.mark.parametrize("order", ["mask-first", "app-first"])
def test_split_editor_and_merge_toolbar_behave_in_a_browser(order: str) -> None:
    """Both script orders: the sums must not depend on which listener runs first."""
    report = run_harness(f"tests/js/splits_harness.html?order={order}")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert "PASS exact sum" in report and "PASS bar shown at 2" in report
    assert report.count("PASS") >= 55
    assert "PASS 25,52 = 15,52 + 10,00" in report and "PASS 1.250,00 = 1.000,00 + 250,00" in report


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_items_editor_on_the_purchase_form_and_on_an_installments_form() -> None:
    """Parent category lock, the whole-purchase total (installment value x N) and the plan-wide
    scope of an installment's items, in a real browser."""
    report = run_harness("tests/js/splits_forms_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    for name in (
        "parent select is disabled",
        "toggle stays enabled with 3 installments",
        "items = the whole purchase",
        "35000 + 10000 = 150,00 x 3",
        "plan-wide: items = the covered installments (2 x 150,00)",
        "single scope adds up",
    ):
        assert f"PASS {name}" in report, name
