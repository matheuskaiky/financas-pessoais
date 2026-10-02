"""charts.js in a real browser engine (math, formatting, scrubbing, keyboard, ARIA).

Runs ``tests/js/charts_harness.html`` in headless Chromium/Edge and reads the verdict the page
writes into the DOM. Skipped when no such browser is installed (it is not a project dependency).
"""

import functools
import html
import http.server
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "js" / "charts_harness.html"
EDGE = Path("/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def _browser() -> list[str] | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return [found, "--no-sandbox"]
    return [str(EDGE)] if EDGE.exists() else None


@pytest.mark.skipif(_browser() is None, reason="no headless Chromium/Edge installed")
def test_charts_js_maths_formatting_and_interaction() -> None:
    command = _browser()
    assert command is not None
    handler = functools.partial(_Quiet, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = subprocess.run(
            [
                *command,
                "--headless=new",
                "--disable-gpu",
                "--virtual-time-budget=8000",
                "--dump-dom",
                f"http://127.0.0.1:{server.server_address[1]}/tests/js/charts_harness.html",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    finally:
        server.shutdown()
    match = re.search(r'<pre id="out">(.*?)</pre>', result.stdout, re.S)
    assert match, result.stdout[-500:]
    report = html.unescape(match.group(1))
    failures = [line for line in report.splitlines() if line.startswith("FAIL")]
    assert not failures and "DONE 0" in report, report
    assert report.count("PASS") >= 30
