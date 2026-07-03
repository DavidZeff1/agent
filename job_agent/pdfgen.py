"""HTML -> PDF using the browser already installed on the machine.

Why this instead of a Python PDF library: application forms want a *file* upload, and
every Mac/Windows box with Chrome/Edge/Brave can print simple HTML to a clean,
ATS-parseable single-column PDF — no extra dependency, no bundled fonts. If no
compatible browser is found we skip the PDF silently; the .txt/.md versions are
always written, so nothing breaks.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

_MAC_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
_UNIX_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge", "brave-browser"]

_cached: Optional[str] = None
_checked = False


def find_browser() -> Optional[str]:
    global _cached, _checked
    if _checked:
        return _cached
    _checked = True
    for p in _MAC_BROWSERS:
        if os.path.isfile(p):
            _cached = p
            return _cached
    for name in _UNIX_NAMES:
        p = shutil.which(name)
        if p:
            _cached = p
            return _cached
    return None


def html_to_pdf(html: str, out_path: str, timeout: float = 30) -> bool:
    """Render ``html`` to ``out_path``. Returns True on success, False if unavailable/failed.

    Uses ``--headless=new`` (the old headless mode hangs when the user's regular Chrome is
    already open) and waits for the output file instead of process exit, because Chrome
    sometimes lingers after the PDF is fully written.
    """
    import time

    browser = find_browser()
    if not browser:
        return False
    try:
        if os.path.isfile(out_path):
            os.remove(out_path)
        with tempfile.TemporaryDirectory(prefix="jobagent-pdf-") as tmp:
            src = os.path.join(tmp, "doc.html")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(html)
            # A throwaway --user-data-dir keeps this instance fully separate from the
            # user's running browser.
            cmd = [
                browser, "--headless=new", "--disable-gpu", "--no-first-run",
                f"--user-data-dir={os.path.join(tmp, 'profile')}",
                "--no-pdf-header-footer",
                f"--print-to-pdf={out_path}",
                "file://" + src,
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + timeout
            try:
                while time.time() < deadline:
                    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                        time.sleep(0.5)  # let the write finish
                        break
                    if proc.poll() is not None:
                        break
                    time.sleep(0.25)
            finally:
                if proc.poll() is None:
                    proc.kill()
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception:  # noqa: BLE001 - PDF is best-effort, never break the packet
        return False


def pdf_available() -> bool:
    return find_browser() is not None
