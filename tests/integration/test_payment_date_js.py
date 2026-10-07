"""Live validation of the statement payment date (app.js) in a real browser engine."""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_payment_date_alerts_follow_the_typed_date_and_gate_the_submit() -> None:
    report = run_harness("tests/js/payment_date_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert report.count("PASS") >= 7
