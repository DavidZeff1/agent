"""Command-line interface — the minimal, reliable "router" over the same tools the agent uses.

Commands: setup, show, answer, search, match, review, apply, run, agent, web.
Run ``python -m job_agent --help`` or ``python -m job_agent <cmd> --help``.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import matching
from . import generate as gen
from . import profile_store as ps
from .config import get_settings
from .llm import get_llm, llm_available
from .profile_store import Profile
from .scraper import SOURCES, Job
from .tools import Context


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _require_profile() -> Profile:
    profile = ps.load_profile()
    if profile is None or not (profile.full_name or profile.skills):
        print("No profile found. Set one up first:\n  python -m job_agent setup")
        sys.exit(1)
    return profile


def _context(profile: Optional[Profile] = None, want_llm: bool = True) -> Context:
    llm = get_llm(optional=True) if want_llm else None
    return Context(profile=profile or Profile(), llm=llm)


def _print_ranked(ranked: List[dict], top: int) -> None:
    print(f"\n{'#':>2}  {'score':>5}  {'title @ company':<52}  location")
    print("-" * 92)
    for e in ranked[:top]:
        j = e["job"]
        tc = f"{j['title']} @ {j['company']}"
        loc = j.get("location") or ("remote" if j.get("remote") else "")
        verdict = f"  [{e.get('verdict')}]" if e.get("verdict") else ""
        print(f"{e['rank']:>2}  {e['final_score']:>5.1f}  {tc[:52]:<52}  {loc[:20]}{verdict}")
    print()


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------


def cmd_setup(args) -> None:
    if args.import_path:
        profile = ps.import_profile(args.import_path)
        print(f"Imported profile for {profile.full_name!r} -> {get_settings().profile_path}")
        return
    existing = ps.load_profile()
    if existing and not args.force:
        print("Editing existing profile (blank answers keep current values).")
    profile = ps.run_intake(existing)
    path = ps.save_profile(profile)
    print(f"\nSaved profile -> {path}")


def cmd_show(args) -> None:
    profile = _require_profile()
    print(profile.to_text_block(8000))


def cmd_answer(args) -> None:
    profile = _require_profile()
    llm = get_llm(optional=True)
    if llm is None:
        print("[note] No GROQ_API_KEY set — answering from stored data only.")
    ans = ps.answer_question(profile, args.question, llm=llm)
    print("\n" + ans)


def cmd_search(args) -> None:
    profile = ps.load_profile()
    keywords = _split(args.keywords) if args.keywords else ps.default_keywords(profile)
    if not keywords:
        print("Provide --keywords, or set desired titles/skills via setup.")
        sys.exit(1)
    print(f"Searching for {keywords} (location={args.location or 'any'}, remote={args.remote}) ...")
    ctx = _context(profile, want_llm=False)
    from .tools import build_registry

    reg = build_registry(ctx)
    import json

    res = json.loads(reg.dispatch("search_jobs", {
        "keywords": keywords, "location": args.location, "remote": args.remote,
        "limit": args.limit, "sources": _split(args.sources) if args.sources else None,
    }))
    print(f"Found {res.get('found', 0)} jobs (cached).")
    for s in res.get("sample", []):
        print(f"  - {s}")


def cmd_match(args) -> None:
    profile = _require_profile()
    ctx = _context(profile, want_llm=args.llm)
    jobs = ctx.load_jobs()
    if not jobs:
        print("No cached jobs. Run `search` first.")
        sys.exit(1)
    use_llm = args.llm and ctx.llm is not None
    if args.llm and ctx.llm is None:
        print("[note] --llm requested but no GROQ_API_KEY set; using deterministic scoring only.")
    print(f"Ranking {len(jobs)} jobs against your profile" + (" (LLM re-rank on top matches)" if use_llm else "") + " ...")
    ranked = matching.rank_jobs(profile, jobs, use_llm=use_llm, llm=ctx.llm)
    ctx.save_ranked(ranked)
    _print_ranked(ranked, args.top)
    print(f"Saved ranking -> {ctx.settings.ranked_path}")


def cmd_review(args) -> None:
    profile = _require_profile()
    ctx = _context(profile, want_llm=False)
    ranked = ctx.load_ranked()
    if not ranked:
        print("Nothing ranked yet. Run `match` first.")
        sys.exit(1)
    _print_ranked(ranked, args.top)
    ids = _prompt_selection(ranked, args.top)
    if not ids:
        print("No jobs selected.")
        return
    from . import util

    util.write_json(ctx.settings.selection_path, {"ids": ids})
    print(f"Selected {len(ids)} job(s) -> {ctx.settings.selection_path}")
    print("Generate applications with:  python -m job_agent apply")


def cmd_apply(args) -> None:
    profile = _require_profile()
    want_llm = not args.no_llm
    ctx = _context(profile, want_llm=want_llm)
    ranked = ctx.load_ranked()
    by_id = {e["job"]["id"]: e["job"] for e in ranked}
    if not by_id:  # fall back to raw cache
        by_id = {j.id: j.to_dict() for j in ctx.load_jobs()}

    ids = _resolve_apply_ids(args, ctx, ranked)
    if not ids:
        print("No jobs to apply to. Run `review` to select, or pass --ids / --all.")
        sys.exit(1)

    if want_llm and ctx.llm is None:
        print("[note] No GROQ_API_KEY — generating untailored resumes + fully pre-filled forms from stored data.")

    ctx.settings.ensure_home()
    for jid in ids:
        if jid not in by_id:
            print(f"  [skip] unknown job id {jid}")
            continue
        job = Job.from_dict(by_id[jid])
        print(f"Generating application: {job.title} @ {job.company} ...")
        paths = gen.generate_application(profile, job, ctx.settings.applications_dir, llm=ctx.llm)
        tag = "tailored" if paths["tailored"] else "untailored"
        print(f"  [{tag}] -> {paths['dir']}")
    print("\nReview each folder, then submit via the posting URL in job.md / application_form.md.")


def cmd_run(args) -> None:
    """End-to-end: search -> match -> review -> apply."""
    profile = _require_profile()
    keywords = _split(args.keywords) if args.keywords else ps.default_keywords(profile)
    if not keywords:
        print("No keywords and no desired titles/skills in profile. Add some via setup.")
        sys.exit(1)
    location = args.location or (profile.preferences.desired_locations[0] if profile.preferences.desired_locations else "")
    ctx = _context(profile, want_llm=not args.no_llm)

    print(f"[1/4] Searching {keywords} (location={location or 'any'}) ...")
    jobs = _search(ctx, keywords, location, profile.preferences.remote_ok, args.limit)
    print(f"      {len(jobs)} jobs cached.")
    if not jobs:
        return

    print("[2/4] Ranking ...")
    use_llm = (not args.no_llm) and ctx.llm is not None
    ranked = matching.rank_jobs(profile, jobs, use_llm=use_llm, llm=ctx.llm)
    ctx.save_ranked(ranked)
    _print_ranked(ranked, args.top)

    print("[3/4] Select jobs to apply to.")
    ids = _prompt_selection(ranked, args.top)
    if not ids:
        print("No selection; stopping before apply.")
        return

    print("[4/4] Generating applications ...")
    for jid in ids:
        entry = next((e for e in ranked if e["job"]["id"] == jid), None)
        if not entry:
            continue
        job = Job.from_dict(entry["job"])
        paths = gen.generate_application(profile, job, ctx.settings.applications_dir, llm=ctx.llm)
        tag = "tailored" if paths["tailored"] else "untailored"
        print(f"  [{tag}] {job.title} @ {job.company} -> {paths['dir']}")
    print("\nDone. Open each folder, review, and submit through the posting URL.")


def cmd_web(args) -> None:
    try:
        from .web import serve
    except ImportError:
        print("The web app needs Flask. Install it with:\n  pip install -r requirements.txt")
        sys.exit(1)
    sys.exit(serve(port=args.port, open_browser=args.open))


def cmd_agent(args) -> None:
    profile = _require_profile()
    llm = get_llm(optional=True)
    if llm is None:
        print("The agent needs a Groq API key. Set GROQ_API_KEY (free at https://console.groq.com).")
        sys.exit(1)
    from .agent import Agent

    ctx = _context(profile, want_llm=True)
    print(f"Agent working on: {args.request}\n")
    reply = Agent(ctx).run(args.request)
    print("\n" + reply)


# ----------------------------------------------------------------------------
# shared command helpers
# ----------------------------------------------------------------------------


def _split(s: Optional[str]) -> List[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _search(ctx: Context, keywords, location, remote, limit) -> List[Job]:
    from . import scraper

    jobs = scraper.search_jobs(keywords, location=location, remote=remote, limit=limit)
    ctx.save_jobs(jobs)
    return jobs


def _resolve_apply_ids(args, ctx: Context, ranked: List[dict]) -> List[str]:
    if args.ids:
        return _split(args.ids)
    if args.all:
        return [e["job"]["id"] for e in ranked]
    sel = _read_selection(ctx)
    return sel


def _read_selection(ctx: Context) -> List[str]:
    from . import util

    data = util.read_json(ctx.settings.selection_path, default={}) or {}
    return list(data.get("ids", []))


def _prompt_selection(ranked: List[dict], top: int) -> List[str]:
    """Ask the user which ranks to apply to. Accepts '1,3,5', 'top 3', 'all', or blank."""
    prompt = "Select jobs to apply to [e.g. 1,3,5 | top 3 | all | (blank=skip)]: "
    try:
        raw = input(prompt).strip().lower()
    except EOFError:
        return []
    if not raw:
        return []
    id_by_rank = {e["rank"]: e["job"]["id"] for e in ranked}
    if raw == "all":
        return list(id_by_rank.values())
    if raw.startswith("top"):
        try:
            n = int(raw.split()[1])
        except (IndexError, ValueError):
            n = top
        return [id_by_rank[r] for r in sorted(id_by_rank) if r <= n]
    picks: List[str] = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit() and int(part) in id_by_rank:
            picks.append(id_by_rank[int(part)])
    return picks


# ----------------------------------------------------------------------------
# argparse wiring
# ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="job_agent",
        description="A minimal agent-and-tools system to find jobs and prepare tailored applications.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("setup", help="One-time interactive profile entry (or --import a JSON file).")
    sp.add_argument("--import", dest="import_path", metavar="PATH", help="Import a profile JSON instead of prompting.")
    sp.add_argument("--force", action="store_true", help="Start fresh instead of editing the existing profile.")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("show", help="Print your stored profile.")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("answer", help="Answer an arbitrary application question from stored data.")
    sp.add_argument("question")
    sp.set_defaults(func=cmd_answer)

    sp = sub.add_parser("search", help="Search job boards and cache results.")
    sp.add_argument("--keywords", help="Comma-separated keywords (defaults to your desired titles).")
    sp.add_argument("--location", default="", help="Target location (default: any).")
    sp.add_argument("--remote", action=argparse.BooleanOptionalAction, default=True, help="Prefer remote roles.")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--sources", help="Comma-separated subset: " + ",".join(SOURCES))
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("match", help="Rank cached jobs against your profile.")
    sp.add_argument("--llm", action="store_true", help="LLM re-rank the top matches (uses the API).")
    sp.add_argument("--top", type=int, default=15)
    sp.set_defaults(func=cmd_match)

    sp = sub.add_parser("review", help="Show ranked jobs and select which to apply to.")
    sp.add_argument("--top", type=int, default=15)
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("apply", help="Generate applications for selected jobs.")
    sp.add_argument("--ids", help="Comma-separated job ids (overrides saved selection).")
    sp.add_argument("--all", action="store_true", help="Apply to every ranked job.")
    sp.add_argument("--no-llm", action="store_true", help="Skip LLM tailoring (still fills forms from data).")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("run", help="End-to-end: search -> match -> review -> apply.")
    sp.add_argument("--keywords", help="Comma-separated keywords (defaults to your desired titles).")
    sp.add_argument("--location", default="")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--no-llm", action="store_true", help="Deterministic scoring + untailored resumes.")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("agent", help="Natural-language agent (Groq tool-calling).")
    sp.add_argument("request", help='e.g. "find remote python jobs and draft applications for the top 3"')
    sp.set_defaults(func=cmd_agent)

    sp = sub.add_parser("web", help="Start the point-and-click web app (easiest way to use Job Agent).")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true", help="Open your browser automatically.")
    sp.set_defaults(func=cmd_web)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
