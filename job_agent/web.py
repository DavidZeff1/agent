"""Local web app — the friendly, point-and-click face of job_agent.

Start it with ``python -m job_agent web --open`` (or double-click ``Job Agent.command``
on macOS). It serves a small single-page app on http://127.0.0.1:8765 backed by the exact
same modules the CLI uses — one thin Flask layer, no database, no accounts. Everything
stays local under ``JOB_AGENT_HOME``; the server binds to 127.0.0.1 only.

Why a local web app (and not a native GUI): the browser is the one UI toolkit every
non-technical user already knows, needs zero extra dependencies beyond Flask, and lets
the "copy this answer into the form" workflow live one tab away from the real job posting.
"""
from __future__ import annotations

import os
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import generate as gen
from . import matching
from . import profile_store as ps
from . import scraper, tracker, util
from .config import IS_HOSTED, get_settings, home
from .llm import GroqLLM, LLMError, get_llm, llm_available
from .profile_store import Profile
from .scraper import Job
from .tools import Context

STATIC_DIR = Path(__file__).resolve().parent / "static"

# ----------------------------------------------------------------------------
# App settings (automation knobs) + auto-search scheduler
# ----------------------------------------------------------------------------

from .config import APP_SETTINGS_DEFAULTS, load_app_settings  # noqa: E402


def save_app_settings(new: dict) -> dict:
    cfg = load_app_settings()
    for k in APP_SETTINGS_DEFAULTS:
        if k in new:
            cfg[k] = type(APP_SETTINGS_DEFAULTS[k])(new[k])
    get_settings().ensure_home()
    util.write_json(str(home() / "settings.json"), cfg)
    return cfg


_SOURCE_KEYS = ("jooble_key", "adzuna_app_id", "adzuna_app_key", "watched_companies")


def _active_source_count() -> int:
    """How many sources actually run: the keyless ones plus whatever is configured."""
    cfg = load_app_settings()
    n = len(scraper.SOURCES) - 3  # jooble/adzuna/companies only run when configured
    if cfg["jooble_key"]:
        n += 1
    if cfg["adzuna_app_id"] and cfg["adzuna_app_key"]:
        n += 1
    if cfg.get("auto_companies", True) or cfg["watched_companies"]:
        n += 1
    return n


def _source_config(data: dict) -> dict:
    """Job-source config for this request: saved settings, overridden by values the browser
    sends along (hosted mode keeps them in localStorage)."""
    saved = load_app_settings()
    cfg = {k: saved[k] for k in _SOURCE_KEYS}
    cfg["auto_companies"] = bool(saved.get("auto_companies", True))
    body_cfg = data.get("sources_config")
    if isinstance(body_cfg, dict):
        for k in _SOURCE_KEYS:
            if body_cfg.get(k):
                cfg[k] = str(body_cfg[k])
        if "auto_companies" in body_cfg:
            cfg["auto_companies"] = bool(body_cfg["auto_companies"])
    return cfg


def _autorun_path() -> str:
    return str(home() / "autorun.json")


# One-line "what is happening right now" note, shown in the UI while slow steps run.
_PROGRESS = {"text": "", "ts": 0.0}


def _set_progress(text: str) -> None:
    _PROGRESS["text"] = text
    _PROGRESS["ts"] = time.time()


def run_auto_search() -> dict:
    """One scheduled pass: search -> rank -> record new -> optionally auto-prepare top new jobs."""
    cfg = load_app_settings()
    profile = ps.load_profile()
    summary = {"ran_at": int(time.time()), "found": 0, "new": 0, "prepared": 0, "error": ""}
    if not profile or not (profile.full_name or profile.skills):
        summary["error"] = "no profile yet"
        return summary
    keywords = ps.default_keywords(profile)
    if not keywords:
        summary["error"] = "no desired titles in profile"
        return summary
    try:
        ctx = Context(profile=profile, llm=get_llm(optional=True))
        jobs = scraper.search_jobs(keywords, remote=profile.preferences.remote_ok, limit=40,
                                   country=profile.contact.country, config=_source_config({}),
                                   llm=ctx.llm)
        ctx.save_jobs(jobs)
        ranked = matching.rank_jobs(profile, jobs)
        ctx.save_ranked(ranked)
        summary["found"] = len(ranked)
        summary["new"] = tracker.record_seen([e["job"] for e in ranked])

        min_score = int(cfg["auto_prepare_min_score"] or 0)
        if min_score > 0:
            seen = tracker.load()
            prepared = 0
            for e in ranked:
                if prepared >= int(cfg["auto_prepare_max"]):
                    break
                j = e["job"]
                if e["final_score"] < min_score:
                    break  # ranked is sorted; nothing below will qualify
                if seen.get(j["id"], {}).get("status", "new") != "new":
                    continue
                ctx.settings.ensure_home()
                gen.generate_application(profile, Job.from_dict(j), ctx.settings.applications_dir, llm=ctx.llm)
                tracker.set_status(j["id"], "prepared", title=j["title"], company=j["company"], url=j.get("url", ""))
                prepared += 1
            summary["prepared"] = prepared
    except Exception as e:  # noqa: BLE001 - a failed pass must not kill the scheduler
        summary["error"] = str(e)
    get_settings().ensure_home()
    util.write_json(_autorun_path(), summary)
    return summary


def _scheduler_loop() -> None:
    time.sleep(15)  # let the app come up first
    while True:
        cfg = load_app_settings()
        if cfg["autosearch"]:
            last = (util.read_json(_autorun_path(), default={}) or {}).get("ran_at", 0)
            due = time.time() - last >= float(cfg["autosearch_hours"]) * 3600 - 60
            if due:
                summary = run_auto_search()
                print(f"[auto-search] found={summary['found']} new={summary['new']} "
                      f"prepared={summary['prepared']}" + (f" error={summary['error']}" if summary["error"] else ""))
        time.sleep(300)


def _ctx(want_llm: bool = True) -> Context:
    profile = ps.load_profile() or Profile()
    return Context(profile=profile, llm=_req_llm() if want_llm else None)


def _req_llm(optional: bool = True):
    """LLM for this request: a key pasted in the app (X-Groq-Key header) wins over the env.
    In hosted mode each visitor brings their own key this way — nothing is stored server-side."""
    key = (request.headers.get("X-Groq-Key") or "").strip()
    if key:
        try:
            return GroqLLM(api_key=key)
        except LLMError:
            pass
    return get_llm(optional=optional)


def _profile_from(data: dict) -> Profile:
    """Profile for this request: inline from the browser (hosted mode) or from disk (local)."""
    if isinstance(data.get("profile"), dict):
        return Profile.from_dict(data["profile"])
    return ps.load_profile() or Profile()


def _ranked_payload(ranked: list[dict]) -> list[dict]:
    """Trim a ranking to what the UI needs (full descriptions stay in the cache)."""
    seen = tracker.load()
    out = []
    for e in ranked:
        j = e["job"]
        out.append({
            "rank": e["rank"],
            "status": seen.get(j["id"], {}).get("status", "new"),
            "score": round(e["final_score"]),
            "verdict": e.get("verdict", ""),
            "rationale": e.get("rationale", ""),
            "reasons": e.get("reasons", ""),
            "matched_skills": e.get("matched_skills", []),
            "missing_keywords": e.get("missing_keywords", []),
            "job": {
                "id": j["id"],
                "title": j["title"],
                "company": j["company"],
                "location": j.get("location", ""),
                "salary": j.get("salary", ""),
                "url": j.get("url", ""),
                "apply_url": j.get("apply_url", ""),
                "source": j.get("source", ""),
                "date": (j.get("date") or "")[:10],
                "description_preview": util.truncate(j.get("description", ""), 900),
                # Hosted mode has no server-side cache, so the browser keeps the full
                # description and sends it back when preparing an application.
                **({"description": j.get("description", "")} if IS_HOSTED else {}),
            },
        })
    return out


def _write_env_key(key: str) -> None:
    """Persist GROQ_API_KEY to JOB_AGENT_HOME/.env (kept out of the project folder)."""
    get_settings().ensure_home()
    path = home() / ".env"
    lines: list[str] = []
    if path.is_file():
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if not l.strip().startswith("GROQ_API_KEY")]
    if key:
        lines.append(f"GROQ_API_KEY={key}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    app.json.sort_keys = False  # keep form fields in their natural order (name first, not alphabetical)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    # -- status & settings ---------------------------------------------------

    @app.get("/api/status")
    def status():
        profile = ps.load_profile()
        s = get_settings()
        return jsonify({
            "hosted": IS_HOSTED,
            "has_profile": bool(profile and (profile.full_name or profile.skills)),
            "name": (profile.preferred_name or profile.full_name) if profile else "",
            "ai": bool((request.headers.get("X-Groq-Key") or "").strip()) or llm_available(),
            "model": s.model,
            "home": str(s.home),
            "sources": _active_source_count(),
            "tracker": tracker.counts(),
            "autorun": util.read_json(_autorun_path(), default={}) or {},
            "settings": load_app_settings(),
        })

    @app.get("/api/results")
    def results():
        c = _ctx(want_llm=False)
        ranked = c.load_ranked()
        return jsonify({"count": len(ranked), "results": _ranked_payload(ranked)})

    @app.post("/api/track")
    def track():
        data = request.get_json(force=True, silent=True) or {}
        job_id = str(data.get("job_id", ""))
        status_ = str(data.get("status", ""))
        if not job_id or status_ not in tracker.STATUSES:
            return jsonify({"error": "Need a job_id and a valid status."}), 400
        entry = tracker.set_status(job_id, status_, title=str(data.get("title", "")),
                                   company=str(data.get("company", "")), url=str(data.get("url", "")))
        return jsonify({"ok": True, "status": entry["status"]})

    @app.get("/api/settings")
    def get_app_settings():
        return jsonify(load_app_settings())

    @app.post("/api/settings")
    def post_app_settings():
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(save_app_settings(data))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid settings values."}), 400

    @app.post("/api/autosearch/run")
    def autosearch_now():
        return jsonify(run_auto_search())

    @app.get("/api/progress")
    def progress():
        # Stale notes (no update for 2 minutes) are treated as finished.
        fresh = time.time() - _PROGRESS["ts"] < 120
        return jsonify({"text": _PROGRESS["text"] if fresh else ""})

    @app.post("/api/settings/key")
    def save_key():
        data = request.get_json(force=True, silent=True) or {}
        key = str(data.get("key", "")).strip()
        if not IS_HOSTED:  # hosted mode never stores keys server-side; the browser keeps them
            _write_env_key(key)
        if not key:
            if not IS_HOSTED:
                os.environ.pop("GROQ_API_KEY", None)
            return jsonify({"ok": True, "ai": False, "message": "AI features turned off."})
        if not IS_HOSTED:
            os.environ["GROQ_API_KEY"] = key
        try:  # one tiny live call so the user knows right away whether the key works
            GroqLLM(api_key=key, max_retries=1).complete("Reply with the word OK.", "ping", max_tokens=8)
        except LLMError as e:
            msg = ("That key doesn't look right — make sure you copied the whole key from console.groq.com."
                   if "invalid" in str(e).lower() or "401" in str(e)
                   else f"The key was saved but the AI service couldn't be reached: {e}")
            return jsonify({"ok": False, "ai": False, "error": msg}), 400
        return jsonify({"ok": True, "ai": True, "message": "AI features are on."})

    # -- profile ---------------------------------------------------------------

    @app.get("/api/profile")
    def get_profile():
        return jsonify((ps.load_profile() or Profile()).to_dict())

    @app.post("/api/profile")
    def save_profile():
        data = request.get_json(force=True, silent=True) or {}
        profile = Profile.from_dict(data)
        if not (profile.full_name or profile.preferred_name):
            return jsonify({"error": "Please enter your name before saving."}), 400
        ps.save_profile(profile)
        return jsonify({"ok": True, "name": profile.preferred_name or profile.full_name})

    @app.post("/api/github/import")
    def github_import_():
        from . import github_import as gh

        data = request.get_json(force=True, silent=True) or {}
        given = str(data.get("github", "")).strip()
        profile = ps.load_profile() or Profile()
        username = gh.parse_username(given or profile.links.github)
        if not username:
            return jsonify({"error": "Enter your GitHub username or profile link first."}), 400
        try:
            repos = gh.fetch_repos(username)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"Could not reach GitHub: {e}"}), 502
        if not repos:
            return jsonify({"error": f"No public projects found on github.com/{username}."}), 404
        _set_progress("Writing project descriptions from your GitHub…")
        try:
            projects = gh.to_projects(repos, llm=_req_llm())
        finally:
            _set_progress("")
        return jsonify({"ok": True, "username": username, "projects": projects})

    # -- search & ranking --------------------------------------------------------

    @app.post("/api/search")
    def search():
        data = request.get_json(force=True, silent=True) or {}
        c = Context(profile=_profile_from(data), llm=None)
        keywords = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()]
        if not keywords:
            keywords = ps.default_keywords(c.profile)
        if not keywords:
            return jsonify({"error": "Type what you're looking for, or add desired job titles to your profile."}), 400
        limit = max(1, min(int(data.get("limit", 30) or 30), 100))
        jobs = scraper.search_jobs(
            keywords,
            location=str(data.get("location", "")).strip(),
            remote=bool(data.get("remote", True)),
            limit=limit,
            country=c.profile.contact.country,
            config=_source_config(data),
            llm=_req_llm(),
        )
        c.save_jobs(jobs)
        ranked = matching.rank_jobs(c.profile, jobs)
        c.save_ranked(ranked)
        new_count = tracker.record_seen([e["job"] for e in ranked])
        return jsonify({"keywords": keywords, "count": len(ranked), "new": new_count,
                        "results": _ranked_payload(ranked)})

    @app.post("/api/rerank")
    def rerank():
        data = request.get_json(force=True, silent=True) or {}
        llm = _req_llm()
        if llm is None:
            return jsonify({"error": "Add your free AI key in Settings to use smart ranking."}), 400
        c = Context(profile=_profile_from(data), llm=llm)
        if isinstance(data.get("jobs"), list):  # hosted mode: the browser holds the jobs
            jobs = [Job.from_dict(j) for j in data["jobs"] if isinstance(j, dict)]
        else:
            jobs = c.load_jobs()
        if not jobs:
            return jsonify({"error": "Search for jobs first."}), 400
        ranked = matching.rank_jobs(c.profile, jobs, use_llm=True, llm=llm)
        c.save_ranked(ranked)
        return jsonify({"count": len(ranked), "results": _ranked_payload(ranked)})

    # -- applications -----------------------------------------------------------

    @app.post("/api/apply")
    def apply_():
        data = request.get_json(force=True, silent=True) or {}
        job_id = str(data.get("job_id", ""))
        want_tailor = bool(data.get("tailor", True))
        profile = _profile_from(data)
        if isinstance(data.get("job"), dict):  # hosted mode: the browser holds the job
            job = Job.from_dict(data["job"])
        else:
            c = _ctx(want_llm=False)
            by_id = {e["job"]["id"]: e["job"] for e in c.load_ranked()}
            if not by_id:
                by_id = {j.id: j.to_dict() for j in c.load_jobs()}
            if job_id not in by_id:
                return jsonify({"error": "That job is no longer in the current search. Search again."}), 404
            job = Job.from_dict(by_id[job_id])
        s = get_settings().ensure_home()
        _set_progress(f"Reading the {job.company} posting…")
        try:
            paths = gen.generate_application(profile, job, s.applications_dir,
                                             llm=_req_llm() if want_tailor else None,
                                             progress=_set_progress)
        finally:
            _set_progress("")
        tracker.set_status(job.id, "prepared", title=job.title, company=job.company, url=job.url)
        resp = {
            "ok": True,
            "tailored": paths["tailored"],
            "folder": os.path.basename(paths["dir"]),
            "job": f"{job.title} @ {job.company}",
        }
        if data.get("inline"):  # hosted mode: return the whole packet for browser storage
            meta = util.read_json(paths["answers_json"], default={}) or {}
            resp["packet"] = {**meta, **paths.get("content", {})}
        return jsonify(resp)

    @app.get("/api/applications")
    def applications():
        d = get_settings().applications_dir
        out = []
        if d.is_dir():
            for sub in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not sub.is_dir():
                    continue
                meta = util.read_json(str(sub / "answers.json"), default={}) or {}
                job = meta.get("job", {})
                seen = tracker.get(job.get("id", "")) or {}
                out.append({
                    "folder": sub.name,
                    "title": job.get("title") or sub.name,
                    "company": job.get("company", ""),
                    "url": job.get("url", ""),
                    "tailored": bool(meta.get("tailored")),
                    "status": seen.get("status", "prepared"),
                    "created": int(sub.stat().st_mtime),
                })
        return jsonify({"applications": out})

    @app.get("/api/application/<name>")
    def application_detail(name: str):
        d = get_settings().applications_dir.resolve()
        folder = (d / name).resolve()
        if folder.parent != d or not folder.is_dir():
            return jsonify({"error": "Application not found."}), 404

        def read(fn: str) -> str:
            p = folder / fn
            return p.read_text(encoding="utf-8") if p.is_file() else ""

        meta = util.read_json(str(folder / "answers.json"), default={}) or {}
        seen = tracker.get(meta.get("job", {}).get("id", "")) or {}
        return jsonify({
            "folder": name,
            "dir": str(folder),
            "job": meta.get("job", {}),
            "status": seen.get("status", "prepared"),
            "tailored": bool(meta.get("tailored")),
            "review": meta.get("review", {}),
            "fields": meta.get("fields", {}),
            "common_answers": meta.get("common_answers", {}),
            "keywords_used": meta.get("keywords_used", []),
            "resume_md": read("resume.md"),
            "resume_txt": read("resume.txt"),
            "cover_letter": read("cover_letter.md"),
            "files": {fn: (folder / fn).is_file() for fn in ("resume.pdf", "cover_letter.pdf")},
        })

    _SERVABLE = {"resume.pdf", "cover_letter.pdf", "resume.txt", "resume.md",
                 "cover_letter.md", "application_form.md", "job.md"}

    @app.get("/api/application/<name>/file/<fn>")
    def application_file(name: str, fn: str):
        d = get_settings().applications_dir.resolve()
        folder = (d / name).resolve()
        if folder.parent != d or fn not in _SERVABLE or not (folder / fn).is_file():
            return jsonify({"error": "File not found."}), 404
        return send_from_directory(folder, fn, as_attachment=True)

    @app.post("/api/autofill")
    def autofill_():
        if IS_HOSTED:
            return jsonify({"error": "Auto-fill needs the desktop version — it opens and fills "
                                     "the form in your own browser, which a website can't do."}), 400
        data = request.get_json(force=True, silent=True) or {}
        name = str(data.get("folder", ""))
        d = get_settings().applications_dir.resolve()
        folder = (d / name).resolve()
        if folder.parent != d or not folder.is_dir():
            return jsonify({"error": "Application not found."}), 404
        meta = util.read_json(str(folder / "answers.json"), default={}) or {}
        jobm = meta.get("job", {})
        profile = ps.load_profile() or Profile()
        cover = folder / "cover_letter.md"
        resume_pdf = folder / "resume.pdf"
        cover_pdf = folder / "cover_letter.pdf"
        from . import autofill as af

        try:
            result = af.autofill(
                jobm.get("url", ""), jobm.get("apply_url", ""), profile,
                cover_letter=cover.read_text(encoding="utf-8") if cover.is_file() else "",
                resume_pdf=str(resume_pdf) if resume_pdf.is_file() else None,
                cover_pdf=str(cover_pdf) if cover_pdf.is_file() else None,
                llm=get_llm(optional=True),
            )
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify(result), (200 if result.get("ok") else 400)

    # -- answer anything ---------------------------------------------------------

    @app.post("/api/answer")
    def answer():
        data = request.get_json(force=True, silent=True) or {}
        question = str(data.get("question", "")).strip()
        if not question:
            return jsonify({"error": "Type a question first."}), 400
        profile = _profile_from(data)
        llm = _req_llm()
        # Only cache answers back into the profile when it lives on this machine.
        ans = ps.answer_question(profile, question, llm=llm, save="profile" not in data)
        return jsonify({"answer": ans, "ai": llm is not None})

    return app


def serve(port: int = 8765, open_browser: bool = False, debug: bool = False) -> int:
    app = create_app()
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_scheduler_loop, daemon=True, name="ja-autosearch").start()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Job Agent is running at {url}")
    print("Keep this window open while you use it. Press Ctrl+C (or close the window) to stop.")
    try:
        app.run(host="127.0.0.1", port=port, threaded=True, debug=debug, use_reloader=False)
    except OSError:
        print(f"Port {port} is already in use — Job Agent may already be running at {url}")
        if open_browser:
            webbrowser.open(url)
        return 1
    return 0
