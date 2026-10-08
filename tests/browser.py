"""Run a harness page in headless Chromium/Edge and read the verdict the page writes into the DOM.

Not a project dependency: tests using it are skipped when no such browser is installed.
"""

import functools
import html
import http.server
import re
import shutil
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDGE = Path("/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
_WINDOWS_EDGE = (  # Edge ships with Windows: native runs find it without WSL
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def browser_command() -> list[str] | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return [found, "--no-sandbox"]
    for edge in (EDGE, *_WINDOWS_EDGE):
        if edge.exists():
            return [str(edge)]
    return None


def run_harness(page: str, *flags: str) -> str:
    """Serve the repository root, open ``page`` and return the text of its ``<pre id="out">``."""
    command = browser_command()
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
                *flags,
                "--dump-dom",
                f"http://127.0.0.1:{server.server_address[1]}/{page}",
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
    return html.unescape(match.group(1))
