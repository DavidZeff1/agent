"""Fill real application forms in your own browser — you review and click Submit.

Scope (and the honest limits):
* Supported: **Greenhouse** and **Lever** public application pages. They are plain forms
  with no login wall, and between them they host a large share of tech/startup postings.
  Aggregator links (Remotive/Jobicy/... pages) are resolved by scanning the posting page
  for the underlying Greenhouse/Lever link.
* Not supported: portals that require an account or run bot checks (Workday, LinkedIn,
  iCIMS, ...). Automating logins is fragile and usually against the site's terms — those
  stay copy-paste via the packet.
* This module **never clicks Submit**. It fills what it can confidently answer from the
  stored profile (using the LLM for open questions when available), uploads the resume
  PDF, highlights anything still empty, and leaves the tab open for the human to finish.

Implementation notes: Playwright drives the *system* Chrome/Edge (``channel="chrome"``,
no separate browser download) with a persistent profile in ``JOB_AGENT_HOME/browser``.
Playwright's sync API is thread-bound, while Flask serves each request on its own
thread — so all browser work funnels through one dedicated worker thread.
"""
from __future__ import annotations

import queue
import re
import threading
from typing import List, Optional

from . import profile_store as ps
from .config import get_settings
from .profile_store import Profile

# ----------------------------------------------------------------------------
# ATS detection & resolution
# ----------------------------------------------------------------------------

_GH_RE = re.compile(r"https?://(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/[^\s\"'<>\\]+", re.I)
_LEVER_RE = re.compile(r"https?://jobs\.(?:eu\.)?lever\.co/[^\s\"'<>\\]+", re.I)


def detect_ats(url: str) -> Optional[str]:
    if not url:
        return None
    if _GH_RE.match(url) or "greenhouse.io" in url:
        return "greenhouse"
    if _LEVER_RE.match(url) or "jobs.lever.co" in url:
        return "lever"
    return None


def resolve_ats_url(url: str) -> Optional[tuple]:
    """Return (kind, direct_url). Follows one aggregator page looking for an ATS link."""
    kind = detect_ats(url)
    if kind:
        return kind, url
    try:
        import requests

        resp = requests.get(url, timeout=get_settings().request_timeout,
                            headers={"User-Agent": "Mozilla/5.0 (job-agent)"})
        html = resp.text or ""
        m = _GH_RE.search(html)
        if m:
            return "greenhouse", m.group(0).rstrip(")\\.,")
        m = _LEVER_RE.search(html)
        if m:
            return "lever", m.group(0).rstrip(")\\.,")
    except Exception:  # noqa: BLE001
        pass
    return None


# ----------------------------------------------------------------------------
# Single browser worker thread (Playwright sync API is thread-bound)
# ----------------------------------------------------------------------------

_q: "queue.Queue" = queue.Queue()
_worker: Optional[threading.Thread] = None
_state: dict = {}


def _worker_loop() -> None:
    while True:
        fn, args, out = _q.get()
        try:
            out["result"] = fn(*args)
        except Exception as e:  # noqa: BLE001
            out["error"] = e
        out["done"].set()


def _run_in_browser(fn, *args, timeout: float = 420):
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_worker_loop, daemon=True, name="ja-browser")
        _worker.start()
    out: dict = {"done": threading.Event()}
    _q.put((fn, args, out))
    if not out["done"].wait(timeout):
        raise TimeoutError("The browser step took too long; check the browser window.")
    if "error" in out:
        raise out["error"]
    return out["result"]


def _context(headless: bool):
    """Get (or launch) the persistent browser context. Runs on the worker thread only."""
    ctx = _state.get("ctx")
    if ctx is not None:
        try:
            _ = ctx.pages  # probe: raises if the user closed the browser
            return ctx
        except Exception:  # noqa: BLE001
            _state.pop("ctx", None)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright is not installed. Run: pip install -r requirements.txt") from e
    pw = _state.get("pw") or sync_playwright().start()
    _state["pw"] = pw
    profile_dir = str(get_settings().ensure_home().home / "browser")
    last_err = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = dict(headless=headless, args=["--disable-blink-features=AutomationControlled"])
            if headless:
                kwargs["viewport"] = {"width": 1280, "height": 2000}
            else:
                kwargs["no_viewport"] = True
            if channel:
                kwargs["channel"] = channel
            ctx = pw.chromium.launch_persistent_context(profile_dir, **kwargs)
            _state["ctx"] = ctx
            return ctx
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(
        "No Chrome or Edge found to drive. Install Google Chrome, or run: "
        f".venv/bin/playwright install chromium  ({last_err})"
    )


# ----------------------------------------------------------------------------
# Field answering
# ----------------------------------------------------------------------------

def _name_parts(profile: Profile) -> tuple:
    full = (profile.full_name or profile.preferred_name or "").strip()
    if not full:
        return "", ""
    parts = full.split()
    return parts[0], " ".join(parts[1:]) or parts[0]


def _basic_answer(profile: Profile, label: str, cover_letter: str) -> Optional[str]:
    """Deterministic answers for the fields every form asks. Returns None when unsure."""
    l = " ".join(label.lower().split())
    c = profile.contact
    first, last = _name_parts(profile)

    def has(*words):
        return any(w in l for w in words)

    if has("first name", "given name"):
        return first
    if has("last name", "family name", "surname"):
        return last
    if has("full name") or l in ("name", "name *", "your name"):
        return profile.full_name or profile.preferred_name
    if has("preferred name"):
        return profile.preferred_name or first
    if has("e-mail", "email"):
        return c.email
    if has("phone"):
        return c.phone
    if has("cover letter"):
        return cover_letter or None
    if has("linkedin"):
        return profile.links.linkedin or None
    if has("github"):
        return profile.links.github or None
    if has("portfolio"):
        return profile.links.portfolio or profile.links.website or None
    if has("website", "personal site"):
        return profile.links.website or profile.links.portfolio or None
    if len(l) < 45 and has("current company", "current employer", "employer", "organization", "company you work"):
        return profile.experience[0].company if profile.experience else None
    if len(l) < 45 and has("current title", "current role", "job title") and not has("desired"):
        return profile.experience[0].title if profile.experience else profile.headline or None
    if has("city") and not has("authorized", "eligible"):
        return c.city or None
    if has("location") and len(l) < 40:
        loc = ", ".join(x for x in [c.city, c.state, c.country] if x)
        return loc or None
    if has("zip", "postal"):
        return c.postal_code or None
    if has("country") and len(l) < 30:
        return c.country or None
    if has("salary", "compensation") and profile.preferences.salary_min:
        p = profile.preferences
        amount = p.salary_max or p.salary_min
        return f"{p.salary_currency} {amount:,}"
    if has("notice period", "availability", "start date", "when can you start"):
        return profile.preferences.availability or profile.preferences.earliest_start_date or None
    if has("pronouns"):
        return profile.demographics.pronouns or None
    if has("how did you hear", "where did you hear", "how you heard"):
        val = profile.additional.get("How did you hear about us?")
        return str(val) if val else "Job board"
    return None


_YES_RE = re.compile(r"^\s*(yes|y)\b", re.I)
_NO_RE = re.compile(r"^\s*(no|n)\b", re.I)


def _pick_option(options: List[str], want_yes: Optional[bool] = None, want_text: str = "") -> Optional[str]:
    """Pick a select/radio option: by yes/no intent, by matching text, or a decline option."""
    if want_yes is not None:
        for o in options:
            if (want_yes and _YES_RE.match(o)) or (not want_yes and _NO_RE.match(o)):
                return o
        return None
    if want_text:
        wl = want_text.lower()
        for o in options:
            if o.lower() == wl:
                return o
        for o in options:
            if wl in o.lower() or o.lower() in wl:
                return o
    return None


def _choice_intent(profile: Profile, label: str) -> tuple:
    """What we'd answer for a choice question: (want_yes, want_text, allow_decline)."""
    l = " ".join(label.lower().split())
    d = profile.demographics
    auth = profile.work_authorizations[0] if profile.work_authorizations else None

    def has(*words):
        return any(w in l for w in words)

    if has("sponsor", "sponsorship") or ("visa" in l and "require" in l):
        return (auth.requires_sponsorship if auth else None), None, False
    if has("authorized to work", "legally authorized", "eligible to work", "right to work"):
        return ((not auth.requires_sponsorship) if auth else None), None, False
    if has("relocat"):
        return profile.preferences.open_to_relocation, None, False
    if has("remote") and has("willing", "open", "comfortable"):
        return profile.preferences.remote_ok, None, False
    if has("hispanic", "latino", "race", "ethnicity"):
        return None, d.race_ethnicity, True
    if has("gender") and not has("veteran"):
        return None, d.gender, True
    if has("veteran"):
        return None, d.veteran_status, True
    if has("disability"):
        return None, d.disability_status, True
    if has("pronoun"):
        return None, d.pronouns, True
    if has("country"):
        return None, profile.contact.country, False
    return None, None, False


def _choice_answer(profile: Profile, label: str, options: List[str]) -> Optional[str]:
    """Deterministic choice for the standard yes/no + EEO questions."""
    want_yes, want_text, allow_decline = _choice_intent(profile, label)
    if want_yes is not None:
        return _pick_option(options, want_yes=want_yes)
    if want_text:
        pick = _pick_option(options, want_text=want_text)
        if pick:
            return pick
    if allow_decline:
        return _decline(options)
    return None


def _decline(options: List[str]) -> Optional[str]:
    for o in options:
        if re.search(r"decline|prefer not|rather not|(don'?t|do not) (wish|want)", o, re.I):
            return o
    return None


_PICK_SYSTEM = (
    "You fill job application forms using ONLY the candidate's profile. Given a form question "
    "and its allowed options, reply with the exact text of the single best option, or SKIP if "
    "the profile doesn't determine an answer. Reply with the option text only."
)


def _llm_choice(llm, profile: Profile, label: str, options: List[str]) -> Optional[str]:
    try:
        reply = llm.complete(
            _PICK_SYSTEM,
            f"PROFILE:\n{profile.to_text_block(2500)}\n\nQUESTION: {label}\nOPTIONS:\n- " + "\n- ".join(options),
            temperature=0.1, max_tokens=60,
        ).strip()
    except Exception:  # noqa: BLE001
        return None
    if not reply or reply.upper().startswith("SKIP"):
        return None
    return _pick_option(options, want_text=reply)


# ----------------------------------------------------------------------------
# The fill pass
# ----------------------------------------------------------------------------

_SCAN_JS = """
() => {
  const els = [...document.querySelectorAll('input, textarea, select')].filter(e => {
    const t = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(t)) return false;
    if (e.name === 'g-recaptcha-response' || (e.className||'').includes('recaptcha')) return false;
    if ((e.getAttribute('role') || '') === 'searchbox') return false;
    return true;
  });
  const grab = (el) => el ? el.textContent.trim().replace(/\\s+/g, ' ') : '';
  return els.map((e, i) => {
    e.setAttribute('data-ja-i', String(i));
    let label = '';
    if (e.id) label = grab(document.querySelector('label[for="' + CSS.escape(e.id) + '"]'));
    if (!label) { const l = e.closest('label'); if (l) label = grab(l); }
    if (!label) label = (e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim();
    let group = '';
    const wrap = e.closest('fieldset, .application-question, .field, [role="group"], li, .form-group');
    if (wrap) group = grab(wrap.querySelector('legend, .application-label, .text, .label, label'));
    const combobox = (e.getAttribute('role') || '') === 'combobox' ||
      (e.getAttribute('aria-haspopup') || '') === 'listbox' ||
      (e.getAttribute('aria-autocomplete') || '') === 'list' ||
      (e.className || '').includes('select__input');
    return {
      i, tag: e.tagName.toLowerCase(), type: (e.type || '').toLowerCase(),
      name: e.name || '', id: e.id || '', combobox,
      label: (label || '').slice(0, 300), group: (group || '').slice(0, 300),
      required: !!e.required || /[*✱]/.test(label + group),
      options: e.tagName === 'SELECT' ? [...e.options].map(o => o.textContent.trim()).filter(Boolean) : null,
      value: e.tagName === 'SELECT' ? '' : (e.value || ''),
    };
  });
}
"""

_BANNER_JS = """
() => {
  if (document.getElementById('ja-banner')) return;
  const d = document.createElement('div');
  d.id = 'ja-banner';
  d.style.cssText = 'position:sticky;top:0;z-index:2147483647;background:#14532d;color:#fff;' +
    'padding:12px 16px;text-align:center;font:600 14px sans-serif;';
  d.textContent = 'Job Agent filled this form. Review every field, complete anything highlighted, then click Submit yourself.';
  document.body.prepend(d);
  for (const e of document.querySelectorAll('input[required], textarea[required], select[required]')) {
    const empty = e.tagName === 'SELECT' ? !e.value : !e.value;
    if (empty && (e.type || '') !== 'file') e.style.outline = '3px solid #f59e0b';
  }
}
"""


def _llm_pick(ctx: dict, profile: Profile, question: str, opts: List[str]) -> Optional[str]:
    if ctx["llm"] is None or ctx["budget"] <= 0 or not question:
        return None
    ctx["budget"] -= 1
    return _llm_choice(ctx["llm"], profile, question, opts)


def _fill_combobox(page, f: dict, question: str, profile: Profile, ctx: dict) -> Optional[str]:
    """React-select style dropdowns (new Greenhouse). The real <input> is visually tiny, so a
    normal click can fail actionability — focus it via JS and open the menu with ArrowDown."""
    loc = page.locator(f'[data-ja-i="{f["i"]}"]')

    def menu_options() -> List[str]:
        # Visible options only — hidden pickers (e.g. the phone country-code list)
        # keep hundreds of [role=option] nodes in the DOM permanently.
        return [o for o in page.evaluate(
            "() => [...document.querySelectorAll('[role=\"option\"]')]"
            ".filter(o => { const r = o.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"
            ".map(o => o.textContent.trim())"
        ) if o][:60]

    try:
        loc.click(timeout=1500)
    except Exception:  # noqa: BLE001
        try:
            loc.evaluate("el => el.focus()")
        except Exception:  # noqa: BLE001
            return None
    page.wait_for_timeout(250)
    opts = menu_options()
    if not opts:
        try:
            page.keyboard.press("ArrowDown")
        except Exception:  # noqa: BLE001
            return None
        page.wait_for_timeout(450)
        opts = menu_options()
    if not opts:
        page.keyboard.press("Escape")
        return None

    choice = _choice_answer(profile, question, opts)
    if not choice:
        # Long virtualized lists (countries…) don't render every option — type to filter.
        _, want_text, _ = _choice_intent(profile, question)
        if want_text:
            try:
                loc.evaluate("el => el.focus()")
                page.keyboard.type(want_text[:24], delay=15)
                page.wait_for_timeout(500)
                choice = _pick_option(menu_options(), want_text=want_text)
            except Exception:  # noqa: BLE001
                choice = None
    if not choice:
        choice = _llm_pick(ctx, profile, question, opts)
    if not choice:
        page.keyboard.press("Escape")
        return None
    try:
        visible = page.locator('[role="option"]:visible')
        opt = visible.filter(has_text=re.compile(rf"^{re.escape(choice)}$"))
        if opt.count() == 0:
            opt = visible.filter(has_text=re.compile(re.escape(choice)))
        opt.first.click(timeout=3000)
        page.wait_for_timeout(200)
        return choice
    except Exception:  # noqa: BLE001
        page.keyboard.press("Escape")
        return None


def _fill_page(page, profile: Profile, cover_letter: str, resume_pdf: Optional[str],
               cover_pdf: Optional[str], llm) -> dict:
    filled, skipped = [], []
    ctx = {"llm": llm, "budget": 8}

    fields = page.evaluate(_SCAN_JS)
    handled_radio_groups: set = set()
    file_inputs = [f for f in fields if f["type"] == "file"]

    for f in fields:
        loc = page.locator(f'[data-ja-i="{f["i"]}"]')
        label = (f["label"] or f["group"] or f["name"] or "").strip()
        question = (f["group"] or f["label"] or "").strip()
        try:
            if f["type"] == "file":
                ident = f["name"] + f["id"] + label
                if cover_pdf and re.search(r"cover", ident, re.I):
                    loc.set_input_files(cover_pdf)
                    page.wait_for_timeout(1200)
                    filled.append("Cover letter upload")
                elif resume_pdf and (re.search(r"resume|cv", ident, re.I) or len(file_inputs) == 1):
                    loc.set_input_files(resume_pdf)
                    page.wait_for_timeout(1500)  # many forms upload asynchronously on attach
                    filled.append("Resume upload")
                continue

            if f["tag"] == "select":
                opts = [o for o in (f["options"] or []) if not re.match(r"^(select|choose|please|--|—)", o, re.I)]
                if not opts:
                    continue
                choice = _choice_answer(profile, question or label, opts) \
                    or _llm_pick(ctx, profile, question or label, opts)
                if choice:
                    loc.select_option(label=choice)
                    filled.append(f"{question or label} → {choice}")
                else:
                    skipped.append(question or label or f["name"])
                continue

            if f["type"] in ("radio", "checkbox"):
                key = f["name"] or question
                if key in handled_radio_groups:
                    continue
                group = [g for g in fields if (g["name"] or g["group"]) == key and g["type"] == f["type"]]
                opts = [g["label"] for g in group if g["label"]]
                if not opts or not question:
                    continue
                handled_radio_groups.add(key)
                choice = _choice_answer(profile, question, opts)
                if choice is None and len(opts) > 1:
                    choice = _llm_pick(ctx, profile, question, opts)
                if choice:
                    target = next((g for g in group if g["label"] == choice), None)
                    if target:
                        page.locator(f'[data-ja-i="{target["i"]}"]').check()
                        filled.append(f"{question} → {choice}")
                else:
                    skipped.append(question)
                continue

            if f.get("combobox"):
                choice = _fill_combobox(page, f, question or label, profile, ctx)
                if choice:
                    filled.append(f"{question or label} → {choice}")
                elif question or f["required"]:
                    skipped.append(question or label)
                continue

            # plain text inputs & textareas
            if f["value"]:
                continue  # already has something (e.g. browser autofill)
            answer = _basic_answer(profile, label, cover_letter)
            if answer is None and llm is not None and ctx["budget"] > 0 and len(label) > 12 \
                    and (f["tag"] == "textarea" or (f["required"] and f["type"] in ("text", ""))):
                ctx["budget"] -= 1
                ans = ps.answer_question(profile, label, llm=llm, save=False)
                answer = None if not ans or ans.upper() == "NOT IN PROFILE" else ans
            if answer:
                loc.fill(str(answer))
                filled.append(label or f["name"])
            elif label or f["required"]:
                skipped.append(label or f["name"])
        except Exception:  # noqa: BLE001 - one bad field must not stop the rest
            skipped.append(label or f["name"] or f["type"])

    page.evaluate(_BANNER_JS)
    return {"filled": filled, "skipped": [s for s in skipped if s]}


def _open_and_fill(kind: str, url: str, profile: Profile, cover_letter: str,
                   resume_pdf: Optional[str], cover_pdf: Optional[str], llm, headless: bool) -> dict:
    ctx = _context(headless)
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)

    if kind == "lever" and "/apply" not in page.url:
        try:
            page.goto(url.rstrip("/") + "/apply", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass

    # Greenhouse job pages sometimes hide the form behind an Apply button/tab.
    try:
        page.wait_for_selector("input[type='file'], #first_name, input[name='first_name'], textarea",
                               state="attached", timeout=3000)
    except Exception:  # noqa: BLE001
        try:
            btn = page.get_by_role("link", name=re.compile(r"^apply", re.I)).or_(
                page.get_by_role("button", name=re.compile(r"^apply", re.I)))
            btn.first.click(timeout=3000)
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass

    result = _fill_page(page, profile, cover_letter, resume_pdf, cover_pdf, llm)
    result["url"] = page.url
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    return result


def autofill(job_url: str, apply_url: str, profile: Profile, cover_letter: str = "",
             resume_pdf: Optional[str] = None, cover_pdf: Optional[str] = None,
             llm=None, headless: bool = False) -> dict:
    """Resolve the posting to a supported ATS form, fill it, and leave it open. Never submits."""
    resolved = None
    for candidate in [apply_url, job_url]:
        if candidate:
            resolved = resolve_ats_url(candidate)
            if resolved:
                break
    if not resolved:
        return {
            "ok": False,
            "error": "This posting doesn't use Greenhouse or Lever (or hides it behind a login), "
                     "so it can't be auto-filled. Use the copy buttons instead.",
        }
    kind, url = resolved
    result = _run_in_browser(_open_and_fill, kind, url, profile, cover_letter, resume_pdf, cover_pdf, llm, headless)
    result.update({"ok": True, "ats": kind})
    return result
