"""Command-line interface — the minimal, reliable "router" over the same tools the agent uses.

Commands: setup, show, answer, search, match, review, apply, run, agent, web.
Run ``python -m job_agent --help`` or ``python -m job_agent <cmd> --help``.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import generate as gen
from . import matching
from . import profile_store as ps
from .config import get_settings
from .llm import get_llm
from .profile_store import Profile
from .scraper import PRESETS, SOURCES, Job, preset_terms
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
    keywords = _keywords_for(args, profile)
    if not keywords:
        print("Provide --keywords or --preset, or set desired titles/skills via setup.")
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

    for jid in ids:
        if jid not in by_id:
            print(f"  [skip] unknown job id {jid}")
    # Keep ranked order so --tailor-top spends the tailoring budget on the best matches.
    score_by_id = {e["job"]["id"]: e.get("final_score", 0) for e in ranked}
    wanted = set(ids)
    entries = [{"job": by_id[jid], "final_score": score_by_id.get(jid, 0)}
               for jid in (e["job"]["id"] for e in ranked) if jid in wanted]
    entries += [{"job": by_id[jid], "final_score": 0}
                for jid in ids if jid in by_id and jid not in score_by_id]

    print(f"Generating {len(entries)} application(s) ...")
    _prepare_and_report(profile, entries, ctx, tailor_top=args.tailor_top)


def cmd_run(args) -> None:
    """End-to-end: search -> match -> review -> apply."""
    profile = _require_profile()
    keywords = _keywords_for(args, profile)
    if not keywords:
        print("No keywords and no desired titles/skills in profile. Add some via setup, or pass --preset.")
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

    if args.all:
        entries = [e for e in ranked if e.get("final_score", 0) >= args.min_score]
        print(f"[3/4] Applying to all {len(entries)} match(es)"
              + (f" scoring {args.min_score}+" if args.min_score else "") + ".")
    else:
        print("[3/4] Select jobs to apply to.")
        ids = set(_prompt_selection(ranked, args.top))
        if not ids:
            print("No selection; stopping before apply.")
            return
        entries = [e for e in ranked if e["job"]["id"] in ids]
    if not entries:
        print(f"Nothing scored {args.min_score} or above. Lower --min-score to widen the batch.")
        return

    print("[4/4] Generating applications ...")
    _prepare_and_report(profile, entries, ctx, tailor_top=args.tailor_top)


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


def _keywords_for(args, profile: Optional[Profile]) -> List[str]:
    """Search terms for this run: an explicit preset, then --keywords, then the profile."""
    preset = getattr(args, "preset", None)
    if preset:
        try:
            return preset_terms(preset)
        except KeyError as e:
            print(e.args[0])
            sys.exit(1)
    if args.keywords:
        return _split(args.keywords)
    return ps.default_keywords(profile)


def _prepare_and_report(profile: Profile, entries: List[dict], ctx: Context,
                        tailor_top: int, min_score: float = 0.0) -> None:
    """Generate packets for ``entries`` and print a line per job plus a closing summary."""
    ctx.settings.ensure_home()
    if ctx.llm is None:
        print("[note] No GROQ_API_KEY — generating untailored resumes + fully pre-filled forms from stored data.")
    elif tailor_top < len(entries):
        print(f"      AI-tailoring the top {tailor_top}; the rest are prepared untailored "
              f"(tailoring is rate-limited — raise with --tailor-top).")

    batch = gen.prepare_batch(profile, entries, ctx.settings.applications_dir, llm=ctx.llm,
                              tailor_top=tailor_top, min_score=min_score,
                              progress=lambda note: print(f"  {note}"))
    for item in batch["prepared"]:
        print(f"  [{'tailored' if item['tailored'] else 'untailored'}] {item['job']} -> {item['dir']}")
    for bad in batch["failed"]:
        print(f"  [failed] {bad['job']}: {bad['error']}")
    print(f"\n{len(batch['prepared'])} application(s) ready in {ctx.settings.applications_dir} "
          f"({batch['tailored']} tailored, {batch['untailored']} untailored"
          + (f", {len(batch['failed'])} failed" if batch["failed"] else "") + ").")
    print("Open each folder, review, and submit through the posting URL in job.md.")


def _search(ctx: Context, keywords, location, remote, limit) -> List[Job]:
    from . import scraper
    from .config import load_app_settings

    jobs = scraper.search_jobs(keywords, location=location, remote=remote, limit=limit,
                               country=ctx.profile.contact.country, config=load_app_settings(),
                               llm=ctx.llm)
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
    sp.add_argument("--preset", help="Search a whole field instead of one title: " + ", ".join(sorted(PRESETS)))
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
    sp.add_argument("--tailor-top", type=int, default=gen.DEFAULT_TAILOR_TOP, metavar="N",
                    help=f"AI-tailor only the N best-scoring jobs (default {gen.DEFAULT_TAILOR_TOP}); "
                         "the rest are prepared untailored. Use a big number to tailor everything.")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("run", help="End-to-end: search -> match -> review -> apply.")
    sp.add_argument("--keywords", help="Comma-separated keywords (defaults to your desired titles).")
    sp.add_argument("--preset", help="Apply across a whole field instead of one title: " + ", ".join(sorted(PRESETS)))
    sp.add_argument("--location", default="")
    sp.add_argument("--limit", type=int, default=30, help="Max jobs to find (raise it for bulk runs).")
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--all", action="store_true",
                    help="Skip the selection prompt and prepare an application for every match.")
    sp.add_argument("--min-score", type=float, default=0.0, metavar="N",
                    help="With --all, only prepare jobs scoring N or above (0 = every match).")
    sp.add_argument("--tailor-top", type=int, default=gen.DEFAULT_TAILOR_TOP, metavar="N",
                    help=f"AI-tailor only the N best-scoring jobs (default {gen.DEFAULT_TAILOR_TOP}); "
                         "the rest are prepared untailored. Use a big number to tailor everything.")
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
