"""End-to-end test of Job Agent: a fresh user's full journey in a real browser.

Usage: e2e_test.py local|hosted
Local  = desktop mode (server-side state, PDFs, autofill button, AI from .env)
Hosted = Vercel simulation (VERCEL=1, no env key; browser localStorage; key via UI)
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

AGENT = str(pathlib.Path(__file__).resolve().parents[1])
SCRATCH = "/tmp"
MODE = sys.argv[1] if len(sys.argv) > 1 else "local"
PORT = 8801 if MODE == "local" else 8802

failures = []
console_errors = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(f"{name}: {detail}")


def groq_key():
    with open(os.path.join(AGENT, ".env")) as fh:
        for line in fh:
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


# --- server ------------------------------------------------------------------
if MODE == "local":
    home = os.path.join(SCRATCH, "e2e-home")
    shutil.rmtree(home, ignore_errors=True)
    env = {**os.environ, "JOB_AGENT_HOME": home}
else:
    home = "/tmp/job_agent"
    shutil.rmtree(home, ignore_errors=True)
    env = {**os.environ, "VERCEL": "1", "GROQ_API_KEY": ""}
os.makedirs(home, exist_ok=True)
with open(os.path.join(home, "settings.json"), "w") as fh:
    json.dump({"autosearch": False}, fh)  # keep the scheduler out of the test's way

server = subprocess.Popen(
    [os.path.join(AGENT, ".venv/bin/python"), "-m", "job_agent", "web", "--port", str(PORT)],
    cwd=AGENT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.5)

from playwright.sync_api import sync_playwright  # noqa: E402

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        B = f"http://127.0.0.1:{PORT}"

        # -- 1. first visit: welcome screen, nothing blank ---------------------
        page.goto(B, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        check("first visit shows welcome screen", page.is_visible("#view-welcome"))
        check("page is not blank", page.locator("#main section:not([hidden])").count() == 1)

        # -- 2. build a profile through the real form --------------------------
        page.click("#welcome-start")
        check("profile view opens", page.is_visible("#view-profile"))
        page.fill('[data-bind="full_name"]', "Dana E2E")
        page.fill('[data-bind="contact.email"]', "dana@example.com")
        page.select_option('select[data-bind="contact.country"]', label="Israel")
        for skill in ("Customer service", "Microsoft Excel"):
            page.fill("#skills .chiprow input", skill)
            page.click("#skills .chiprow .btn")
        page.fill("#desired_titles .chiprow input", "Customer Support Specialist")
        page.click("#desired_titles .chiprow .btn")
        page.click("#save-profile")
        page.wait_for_selector(".toast", timeout=8000)
        check("profile saves with a confirmation", True)

        # -- 3. reload: profile persists, find view with hint ------------------
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        check("after reload lands on Find jobs", page.is_visible("#view-find"))
        check("search box prefilled from profile", page.input_value("#kw") != "")
        check("friendly hint instead of blank space",
              MODE == "hosted" or page.locator("#results .empty").count() > 0
              or page.locator(".jobcard").count() > 0)

        # -- 4. hosted only: paste the AI key through the UI -------------------
        if MODE == "hosted":
            page.click("#ai-chip")
            page.fill("#key-input", groq_key())
            page.click("#btn-save-key")
            page.wait_for_selector(".toast", timeout=30000)
            page.wait_for_timeout(500)
            has_key = page.evaluate("() => !!localStorage.getItem('ja_key')")
            check("AI key saved to this browser only", has_key)
            if page.locator("#settings[open]").count():
                page.click("#btn-close-settings")

        # -- 5. search (real boards + Israeli company pages) -------------------
        page.click("#btn-search")
        page.wait_for_selector(".jobcard", timeout=180000)
        n_before = page.locator(".jobcard").count()
        check("search returns results", n_before >= 5, f"got {n_before}")

        # -- 6. skip a job: gone immediately, remembered ------------------------
        page.locator(".skipbtn").first.click()
        page.wait_for_timeout(800)
        check("skip hides the job", page.locator(".jobcard").count() == n_before - 1)

        # -- 7. prepare one application -----------------------------------------
        if MODE == "hosted":
            if page.is_visible("#tailor"):
                page.uncheck("#tailor")  # tailored+inline already covered by API tests
        page.locator(".jobcard input[type=checkbox]").first.check()
        check("selection bar appears", page.is_visible("#selectbar"))
        page.click("#btn-prepare")
        page.wait_for_selector("#view-apps:not([hidden])", timeout=240000)
        page.wait_for_timeout(500)
        check("lands on Applications with one ready", page.locator(".appitem").count() >= 1)

        # -- 8. application detail ----------------------------------------------
        page.locator(".appitem").first.click()
        page.wait_for_timeout(800)
        check("detail view opens", page.is_visible("#view-appdetail"))
        check("form fields with copy buttons", page.locator(".fieldrow").count() >= 3)
        check("resume text present", len(page.text_content("#app-resume") or "") > 50)
        if MODE == "local":
            check("PDF download offered (local)", page.is_visible("#dl-resume-pdf"))
            check("autofill button offered (local)", page.is_visible("#btn-autofill"))
        else:
            check("print-to-PDF offered (hosted)", page.is_visible("#print-resume"))
            check("autofill hidden with explanation (hosted)",
                  page.is_hidden("#btn-autofill") and "desktop" in (page.text_content("#autofill-result") or ""))

        # -- 9. status tracking --------------------------------------------------
        page.select_option("#app-status", "submitted")
        page.wait_for_selector(".toast", timeout=8000)
        check("status change confirmed", True)

        # -- 10. answer-anything box (AI) ----------------------------------------
        page.fill("#qa-question", "How many years of experience do you have with Excel?")
        page.click("#btn-answer")
        page.wait_for_selector("#qa-result:not([hidden])", timeout=60000)
        ans = page.text_content("#qa-answer") or ""
        check("answer box replies", len(ans) > 2, ans[:60])

        # -- 11. settings modal opens and closes ---------------------------------
        page.click("#ai-chip")
        check("settings modal opens", page.locator("#settings[open]").count() == 1)
        check("automation shown only locally",
              page.is_visible("#automation-block") == (MODE == "local"))
        page.click("#btn-close-settings")

        benign = ("favicon", "net::ERR_FAILED", "Failed to load resource")
        real_errors = [e for e in console_errors if not any(b in e for b in benign)]
        check("no JavaScript errors anywhere", not real_errors, "; ".join(real_errors[:3]))

        browser.close()
finally:
    server.terminate()

print()
if failures:
    print(f"E2E-{MODE.upper()}-FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"E2E-{MODE.upper()}-PASSED (all checks)")
