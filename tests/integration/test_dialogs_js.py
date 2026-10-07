"""Dialog opening (tokens.css, components.css, fp-ui.js) in a real browser engine.

Runs ``tests/js/dialogs_harness.html``: every modal animates only compositor properties
(opacity, transform) on itself and its backdrop, the backdrop blur is static, the dialog gets
``will-change`` only while it opens, and contains its paint.
"""

import pytest

from browser import browser_command, run_harness


@pytest.mark.skipif(browser_command() is None, reason="no headless Chromium/Edge installed")
def test_dialogs_animate_only_compositor_properties_and_release_their_layer() -> None:
    report = run_harness("tests/js/dialogs_harness.html")
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert report.count("PASS") >= 40
