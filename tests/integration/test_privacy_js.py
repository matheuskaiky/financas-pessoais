"""Privacy mode (fp-privacy.js + tokens.css + fp-privacy.css) in a real browser engine.

Runs ``tests/js/privacy_harness.html``: masking changes no layout box (CLS 0), the symbol stays
crisp, the state persists in localStorage and is applied before the first paint of the next page,
groups mask independently, the P shortcut and the controls work, and a masked value is announced
as hidden.
"""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_privacy_mode_masks_without_moving_anything_and_persists() -> None:
    report = run_harness("tests/js/privacy_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    for name in ("no layout shift", "symbol stays crisp", "applied before first paint"):
        assert any(line.startswith("PASS") and name in line for line in report.splitlines()), name
    assert report.count("PASS") >= 14
