"""The ATM-style money mask (money-mask.js) in a real browser engine.

Runs ``tests/js/money_mask_harness.html``: digits push in from the right, backspace drops the
least-significant digit, server-rendered values are formatted on load and the submitted text is
what ``parse_brl`` reads. Skipped when no headless Chromium/Edge is installed.
"""

import pytest

from browser import browser_command, run_harness
from financas.domain.money import parse_brl


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_money_mask_pushes_digits_from_the_right() -> None:
    report = run_harness("tests/js/money_mask_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert "PASS typing 100000" in report and report.count("PASS") >= 30


@pytest.mark.parametrize(
    ("masked", "cents"),
    [("0,01", 1), ("0,10", 10), ("10,00", 1_000), ("1.000,00", 100_000), ("-0,05", -5)],
)
def test_masked_text_is_what_the_server_parses(masked: str, cents: int) -> None:
    assert parse_brl(masked) == cents
