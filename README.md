# Job Agent

A minimal **agent-and-tools** system that stores your profile once, searches job boards,
ranks postings against your qualifications, and generates a **tailored resume + cover letter +
pre-filled application packet** for the jobs you pick.

All LLM calls go through the **Groq API** (free tier, default model `llama-3.3-70b-versatile`).
Everything except LLM tailoring/scoring works **without an API key**, so you can try the whole
pipeline for free.

---

## The easiest way to use it: the app

**On a Mac, double-click `Job Agent.command`.** First launch sets everything up (about a
minute), then your browser opens the app. No terminal knowledge needed:

1. **Profile** — fill in a friendly, dropdown-first form once (name, experience, skills,
   preferences…). One click imports your public GitHub repositories as resume-ready projects.
2. **Find jobs** — one button searches 7 job boards and shows the best matches first, each
   with a match score and *why* it matches. Jobs you've skipped, prepared, or submitted
   never come back as new. Optionally let the AI improve the ranking.
3. **Tick the jobs you like → “Prepare applications”** — for each job you get an ATS-safe
   **PDF resume**, a tailored cover letter, and every form answer with a **Copy** button.
   Documents match the posting's language (a Hebrew posting gets Hebrew documents with proper
   right-to-left layout; an English one gets English), whatever language your profile is in.
4. **“Auto-fill in my browser”** — for postings on Greenhouse or Lever (very common), a
   browser window opens with your details, resume, and answers already filled in. You
   review, complete anything highlighted, and click Submit yourself. **Nothing is ever
   submitted automatically.**
5. **Put it on autopilot** — in Settings, let the app re-check the boards every few hours
   while it's open and auto-prepare applications for anything above a match score you
   choose. You come back to a stack of ready-to-send applications and a tracker
   (prepared → submitted → interview / rejected).

The AI key is entered in the app (Settings, top-right chip) — no config files. If a form
asks something unusual, type the question into the app and it answers from your saved profile
(and tells you honestly when it doesn't know).

From a terminal the same app is `python -m job_agent web --open`. Everything below — the CLI —
does the same things scriptably and remains fully supported.

### Hosting it on Vercel

The repo deploys to Vercel as-is (`app.py` is the entrypoint). Hosted, the server is
**stateless**: each visitor's profile, API key, job history, and prepared applications live
in their own browser (localStorage), and the key is pasted in the app's Settings — nothing
is stored server-side. Machine-bound features are hidden with in-app explanations: form
autofill and background auto-search need the desktop version, and PDFs become a
"Print / save as PDF" button. Don't set `GROQ_API_KEY` as a Vercel env var unless you're
happy for every visitor to spend your quota — without it, each visitor uses their own key.

That statelessness is enforced on the server, not just observed by the UI. One warm serverless
instance serves unrelated visitors in turn, so anything written to its disk would be readable
by whoever lands on it next. Hosted, the endpoints backed by machine-wide state (`/api/profile`
POST, `/api/applications`, `/api/application/…`, `/api/results`, `/api/settings`, `/api/track`)
return 404, `/api/status` reports nothing about the stored profile, and a prepared application
is generated into a throwaway directory, returned inline, and deleted before the response is
sent. The `hosted isolation` CI job asserts all of this on every push.

---

## Why this design (read this first)

* **Enter your data once.** `setup` collects every field an application typically asks for and
  saves it to `~/.job_agent/profile.json`. It's never re-entered. An open `additional` map plus an
  `answer` command let you answer *unexpected* questions from what you stored.
* **Generate + pre-fill, you click submit.** True "hands-off" auto-submission is the fragile part:
  most sites require an account, run bot detection, and gate submission behind CAPTCHAs. So instead
  of blindly POSTing forms, this tool produces, per job, an ATS-friendly resume, a tailored cover
  letter, and an `application_form.md` that maps the common form fields to your answers — you paste
  and submit in about a minute. This is far more reliable than brittle auto-submit. (See
  [Auto-submitting: limitations & the fallback](#auto-submitting-limitations--the-fallback).)
* **Costs and rate limits respected.** Job search and the baseline ranking use **no LLM at all**.
  The LLM only runs where it adds real value (re-ranking the top matches, tailoring), and every call
  is throttled with automatic backoff for Groq's free-tier limits.

---

## Architecture

```
   ┌── web.py — local Flask app (the point-and-click UI; same modules, same state) ──┐
                 ┌─────────────────────────── CLI (router) ───────────────────────────┐
                 │  setup → search → match → review → apply   (also: run, show, answer) │
                 └───────────────┬──────────────────────────────────┬──────────────────┘
                                 │                                  │
                          ┌──────▼──────┐                    ┌──────▼───────┐
   Groq tool-calling ────▶│   tools.py  │  Tool + Registry   │   agent.py   │◀─ "find remote
   (agent mode)           │  (dispatch) │◀───────────────────│ (tool loop)  │   python jobs…"
                          └──┬───┬───┬──┘                    └──────────────┘
                             │   │   │
        ┌────────────────────┘   │   └──────────────────────┐
   ┌────▼──────┐          ┌───────▼────────┐         ┌───────▼────────┐
   │ scraper.py│          │  matching.py   │         │  generate.py   │
   │ Remotive  │          │ deterministic  │         │ resume + cover │
   │ RemoteOK  │          │   + optional   │         │  + form packet │
   │ Arbeitnow │          │   LLM re-rank  │         │  (ATS-friendly)│
   │ Jobicy    │          └────────────────┘         └────────────────┘
   └───────────┘
                       profile_store.py — the single source of truth (JSON)
                       llm.py — Groq wrapper (chat / tool-calling / JSON, throttled)
```

| File | Responsibility |
|------|----------------|
| `job_agent/profile_store.py` | Exhaustive profile schema, JSON persistence, interactive intake, arbitrary-question answering |
| `job_agent/scraper.py` | 9 keyless job sources (Remotive, RemoteOK, Arbeitnow, Jobicy, Himalayas, WeWorkRemotely, HN Who's Hiring, The Muse, Working Nomads) + **automatic company careers pages** for the user's country + optional **Jooble** (~69 countries incl. Israel) / **Adzuna** (20 countries) aggregators with free keys |
| `job_agent/company_directory.py` | Which companies to check per country: a shipped, live-verified directory (109 companies, 9 countries — Israel covered deeply) + AI discovery for any other country, verified against the real ATS APIs and cached |
| `job_agent/tracker.py` | Remembers every job seen + its status; powers the "only show me new jobs" inbox |
| `job_agent/pdfgen.py` | HTML→PDF via the locally installed Chrome/Edge (resume.pdf, cover_letter.pdf) |
| `job_agent/autofill.py` | Fills Greenhouse/Lever application forms in your own browser; never clicks Submit |
| `job_agent/matching.py` | Deterministic scoring (free) + optional LLM re-rank of the top matches |
| `job_agent/generate.py` | ATS-friendly resume, tailored cover letter, pre-filled form packet |
| `job_agent/tools.py` | `Tool` + `ToolRegistry`: how tools are **defined and dispatched** |
| `job_agent/agent.py` | Groq tool-calling agent loop |
| `job_agent/llm.py` | Groq client wrapper (chat, tool-calling, JSON) with throttle + retry |
| `job_agent/cli.py` | Command-line interface (the deterministic router) |
| `job_agent/web.py` + `static/` | Local web app: Flask JSON API + a no-build single-page UI |
| `job_agent/config.py` / `util.py` | Settings/paths/env, small helpers |

---

## Install

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install .                          # or: pip install -r requirements.txt
```

Requires Python 3.9+ (developed on 3.14); CI installs and starts the app on 3.9, 3.12 and 3.13.
Optional extras: `pip install '.[autofill]'` adds Playwright for desktop form-filling, and
`pip install '.[dev]'` adds ruff (`ruff check .` — CI runs the same command).

### Set your Groq API key (optional but recommended)

Get a free key at <https://console.groq.com>, then either export it or drop it in a `.env`:

```bash
cp .env.example .env      # then edit .env and paste your key
# or:
export GROQ_API_KEY="gsk_..."
```

Without a key you can still `search`, `match` (deterministic), and `apply` (untailored resume +
fully pre-filled form from your stored data). With a key you additionally get LLM re-ranking,
tailored resumes/letters, and the natural-language `agent`.

---

## Quickstart

```bash
# 1) One-time: enter all your details (saved to ~/.job_agent/profile.json)
python -m job_agent setup

# 2) Search job boards (defaults to your desired titles; override with --keywords)
python -m job_agent search --keywords "python,backend" --remote --limit 30

# 3) Rank the results against your profile (add --llm to re-rank the top matches)
python -m job_agent match --llm

# 4) Review the ranked list and pick which to apply to
python -m job_agent review          # then type e.g.  1,3,5   or  top 5

# 5) Generate tailored resume + cover letter + pre-filled form for each pick
python -m job_agent apply
#   → files land in ~/.job_agent/applications/<company>__<title>/
```

Or do it all at once, or drive it in natural language:

```bash
python -m job_agent run --keywords "python,backend" --llm     # search→match→review→apply
python -m job_agent agent "find remote python jobs and draft applications for the top 3"
```

(You can also use `python run.py <command>` instead of `python -m job_agent <command>`.)

### Answer any application question from your stored data

```bash
python -m job_agent answer "Describe a time you improved system performance."
python -m job_agent answer "What are your salary expectations?"
```

Confident answers are cached back into your profile so it grows richer over time.

---

## Command reference

| Command | What it does |
|---------|--------------|
| `setup [--import PATH] [--force]` | Interactive profile entry, or import an existing profile JSON |
| `show` | Print your stored profile |
| `answer "<question>"` | Answer an arbitrary application question from stored data |
| `search [--keywords ...] [--location ...] [--remote/--no-remote] [--limit N] [--sources ...]` | Search boards, cache results |
| `match [--llm] [--top N]` | Rank cached jobs; `--llm` re-ranks the top matches |
| `review [--top N]` | Show ranking, select jobs to apply to |
| `apply [--ids a,b] [--all] [--no-llm]` | Generate applications for selected jobs |
| `run [--keywords ...] [--no-llm] ...` | Full pipeline in one command |
| `agent "<request>"` | Natural-language agent using Groq tool-calling |
| `web [--port N] [--open]` | Start the local web app (what `Job Agent.command` runs) |

Everything is stored under `~/.job_agent/` (override with `JOB_AGENT_HOME`):
`profile.json`, `jobs_cache.json`, `ranked.json`, `selection.json`, and `applications/`.

---

## The profile schema (answer anything)

`setup` captures, at minimum: full/preferred name, headline, summary, full contact + address,
nationalities, **all work authorizations** (per country, with sponsorship flag), **multiple
degrees** (institution, field, dates, GPA, honors), **all projects** (name, description, tech,
links, highlights), **work experience** (with achievements + tech per role), skills, languages,
certifications, links (GitHub/LinkedIn/portfolio/site), salary expectations, availability,
relocation/remote preferences, optional voluntary EEO fields, and an open `additional` key-value
map for anything else.

Two things make *unexpected* questions answerable:

1. The open `additional` map stores arbitrary Q&A.
2. `answer "<question>"` feeds your **entire** profile to the LLM and returns a first-person answer
   suitable for a form field (and never fabricates — if the data isn't there it says so).

The JSON is plain and hand-editable; partial files load fine (every field has a default), so you can
also prepare it in an editor and `setup --import profile.json`.

---

## Design notes

### How tools are defined and dispatched

A **single tool layer backs two dispatch styles.** Each capability is a `Tool`
(name + description + JSON-Schema params + a Python callable). `ToolRegistry.dispatch(name, args)`
runs one by name; `Tool.to_schema()` emits the exact Groq/OpenAI function shape.

* **Simple router (default):** the CLI calls tools in a fixed order (`search → match → review →
  apply`). Deterministic, free (no tokens spent deciding what to do), and reliable — best for the
  normal flow.
* **Groq tool-calling (`agent`):** the same schemas are handed to the model, which chooses tools for
  open-ended requests. `agent.py` executes each call and feeds JSON results back until the model
  answers.

Tools operate on **stored state** and return compact **summaries** (counts, top titles, file
paths) rather than large blobs — this keeps token use and rate-limit pressure low during
tool-calling.

### How resume/letter generation stays ATS-friendly

* The resume is **assembled deterministically** from a fixed, single-column template with standard
  headings (Professional Summary, Skills, Experience, Projects, Education). The LLM supplies only
  *content* — a tailored summary, reordered skills, truthfully-rewritten bullets — never layout.
* No tables, columns, images, text boxes, headers/footers, or unusual glyphs. Reverse-chronological.
  A plain-text `resume.txt` (the most parser-safe form) is emitted next to the Markdown version.
* Real keywords from the posting are woven in **only where you genuinely have that experience**
  (`keywords_used` is recorded in the packet). The tailoring prompt hard-forbids inventing
  employers, titles, dates, degrees, metrics, or skills.

### Auto-submitting: limitations & the fallback

Fully automatic submission is intentionally **not** the default, because in practice it breaks:

* **Accounts & logins** — most portals (Greenhouse, Lever, Workday, LinkedIn…) require an
  authenticated session.
* **Bot detection & CAPTCHAs** — headless automation is frequently blocked or challenged.
* **Every form is different** — field names, multi-step wizards, and custom questions vary per site.
* **Terms of service** — automated submission may violate a site's terms; you're responsible for
  compliance.

**The reliable fallback (default):** for each selected job you get a folder with `resume.md` /
`resume.txt`, `cover_letter.md`, `job.md` (the posting + URL), `answers.json`, and
`application_form.md` — a copy-paste map of the standard fields to your stored answers. You open the
posting, paste, handle the login/CAPTCHA yourself, and submit. Fast and unbreakable.

**The shipped middle ground: `autofill.py`.** For postings on **Greenhouse** or **Lever** —
public application forms with no login wall — the app drives your own installed Chrome
(Playwright `channel="chrome"`, persistent profile under `JOB_AGENT_HOME/browser`) to fill
contact fields, upload the resume PDF, answer the standard yes/no and EEO questions
deterministically, and answer open questions with the LLM. It **never clicks Submit**: the
tab stays open with unanswered fields highlighted so a human reviews and sends. Aggregator
links are resolved by scanning the posting page for the underlying Greenhouse/Lever URL.
Portals that require accounts (Workday, LinkedIn, iCIMS…) stay copy-paste — automating
logins is fragile and usually against their terms.

### Rate limits (Groq free tier)

`llm.py` enforces a minimum interval between calls (`JOB_AGENT_MIN_INTERVAL`, default 2s) and retries
with exponential backoff, honoring `Retry-After`. Batch cost is bounded by design: LLM scoring runs
only on the **top ~10** jobs after the free deterministic pre-rank, and tailoring is one call per job
you actually choose. Turn the LLM off anytime with `--no-llm` / by omitting `--llm`.

---

## Extending

* **Add a job source:** write a `fetch_x(session, keywords, limit) -> list[Job]` in `scraper.py` and
  add it to `SOURCES`. It's automatically included and de-duplicated.
* **Add a tool:** construct a `Tool(...)` in `tools.build_registry`; it's instantly available to both
  the CLI and the agent.
* **Swap the model:** set `JOB_AGENT_MODEL` (any Groq chat model).

---

## Notes on data, privacy & terms

* Your profile and generated documents stay **local** under `~/.job_agent/`. Only the text needed for
  a given LLM task (profile digest + a job description) is sent to Groq.
* The job sources are public developer APIs meant for surfacing jobs and linking back to the original
  posting — which is all this tool does. Respect each board's terms and rate limits; don't republish
  their listings.
* This is a personal productivity tool. **Review every application before you submit it**, and never
  submit information you haven't verified.
