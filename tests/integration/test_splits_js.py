"""The items editor and the row breakdown toggle (app.js) in a real browser.

Runs ``tests/js/splits_harness.html``: "Restam R$ …" follows the items and the total, a wrong sum
blocks the submit natively and items are not submitted while the editor is off. Skipped when no
headless Chromium/Edge is installed.
"""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
@pytest.mark.parametrize("order", ["mask-first", "app-first"])
def test_split_editor_behaves_in_a_browser(order: str) -> None:
    """Both script orders: the sums must not depend on which listener runs first."""
    report = run_harness(f"tests/js/splits_harness.html?order={order}")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert "PASS exact sum" in report and "PASS arrow back" in report
    assert report.count("PASS") >= 45
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


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_quick_form_shows_the_method_and_the_items_editor_by_kind_and_account() -> None:
    """Bank expense: method and items; card: no method; income: a method, no items; transfer:
    neither. Switching the kind drops the items and unlocks the parent's category."""
    report = run_harness("tests/js/entry_form_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    for name in (
        "method hidden on a card",
        "items hidden for income",
        "method hidden for a transfer",
        "toggle turned off by the kind switch",
        "parent category unlocked again",
        "no orphan items are sent",
        "débito and boleto are detached on income",
        "boleto falls back to pix on income",
        "the server is asked for the income field",
        "no second request while the side is the same",
        "income has neither debito nor boleto in the DOM",
    ):
        assert f"PASS {name}" in report, name


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_smart_suggestions_fill_the_form_from_the_pages_habits() -> None:
    """Picking from the suggestion list fills category, account, method and the habitual amount
    (never over a typed amount; typing alone never fills); an income has its own list and method."""
    report = run_harness("tests/js/suggestions_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    for name in (
        "focus opens the list with each expense habit once",
        "each option shows where it goes",
        "typing filters the list",
        "enter picks the highlighted one",
        "escape closes",
        "category follows",
        "method follows",
        "the habitual amount fills an empty field",
        "a typed amount stays",
        "typing does not cascade",
        "a committed known habit cascades",
        "a card hides the method field",
        "the list switches to income habits",
        "income arrives by ted",
        "income amount",
    ):
        assert f"PASS {name}" in report, name


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_a_new_row_is_scrolled_to_and_pulsed_from_the_url_fragment() -> None:
    report = run_harness("tests/js/scroll_to_entry_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    for name in (
        "the row is pulsed on load",
        "the row was scrolled into view",
        "the pulse ends",
        "one reveal per address",
        "another row is pulsed",
    ):
        assert f"PASS {name}" in report, name
