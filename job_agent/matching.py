"""Score and rank scraped jobs against the user's profile.

Two tiers, so it works with or without an API key and stays inside free-tier limits:

1. ``deterministic_score`` — no LLM, no cost. Skill overlap + title match + location fit +
   a light seniority heuristic. Runs on *every* job and produces the base ranking.
2. ``llm_score`` (optional) — a single JSON call per job that returns a 0-100 fit score with a
   short rationale + strengths/gaps. To respect Groq's rate limits this is only applied to the
   top ``llm_top`` jobs after the deterministic pre-filter, and each call is throttled by the
   LLM wrapper.
"""
from __future__ import annotations

import re
from typing import List, Optional

from . import util
from .profile_store import Profile
from .scraper import Job

# Notable tech/role keywords used to detect a job's "asked-for" skills so we can report
# which ones the candidate is missing (approximate gap analysis).
COMMON_TECH_KEYWORDS = {
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#", "ruby",
    "php", "scala", "kotlin", "swift", "sql", "nosql", "postgres", "postgresql", "mysql",
    "mongodb", "redis", "elasticsearch", "kafka", "rabbitmq", "spark", "hadoop", "airflow",
    "react", "vue", "angular", "svelte", "node", "node.js", "django", "flask", "fastapi",
    "spring", "rails", ".net", "express", "graphql", "rest", "grpc", "aws", "gcp", "azure",
    "docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd", "git", "linux",
    "machine learning", "deep learning", "pytorch", "tensorflow", "pandas", "numpy", "nlp",
    "data engineering", "etl", "microservices", "distributed systems", "html", "css",
    "tailwind", "next.js", "salesforce", "sap", "excel", "tableau", "power bi", "figma",
}

_SENIORITY = [
    (["intern", "internship"], 0),
    (["junior", "entry", "graduate", "grad ", "associate"], 1),
    (["mid-level", "mid level"], 3),
    (["senior", "sr.", "sr ", "lead", "staff", "principal"], 6),
]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9+#.]+", (text or "").lower()))


def _required_seniority_years(job: Job) -> Optional[int]:
    title = job.title.lower()
    for words, years in _SENIORITY:
        if any(w in title for w in words):
            return years
    m = re.search(r"(\d+)\+?\s*years", job.description.lower())
    if m:
        return int(m.group(1))
    return None


def deterministic_score(profile: Profile, job: Job) -> dict:
    """Return a transparent, LLM-free fit assessment for one job (score is 0-100)."""
    skills = profile.skill_set()
    title_tokens = set()
    for t in profile.preferences.desired_titles + [profile.headline]:
        title_tokens |= _tokens(t)

    job_text = job.search_text()
    job_tokens = _tokens(job_text)

    # 1) Skill overlap (matched skills present in the posting)
    matched = sorted(s for s in skills if s in job_text)
    # Normalize against how many skills the posting seems to want.
    wanted = {k for k in COMMON_TECH_KEYWORDS if k in job_text}
    denom = max(4, min(len(wanted), 10)) if wanted else 6
    skill_score = min(1.0, len(matched) / denom)

    # 2) Title alignment
    title_overlap = title_tokens & _tokens(job.title)
    title_score = min(1.0, len(title_overlap) / 2) if title_tokens else 0.4

    # 3) Location / remote fit
    prefs = profile.preferences
    loc_l = (job.location or "").lower()
    matches_pref = bool(prefs.desired_locations) and any(l.lower() in loc_l for l in prefs.desired_locations)
    matches_country = bool(profile.contact.country) and profile.contact.country.lower() in loc_l
    if job.remote and prefs.remote_ok:
        # "remote" is only fully portable if it isn't geo-restricted to a region the user isn't in.
        portable = (not loc_l) or any(w in loc_l for w in ("anywhere", "worldwide", "global", "remote"))
        location_score = 1.0 if (portable or matches_pref or matches_country) else 0.55
    elif matches_pref:
        location_score = 1.0
    elif prefs.open_to_relocation:
        location_score = 0.7
    elif not job.location:
        location_score = 0.6
    else:
        location_score = 0.3

    # 4) Seniority fit (penalize large mismatches only)
    req = _required_seniority_years(job)
    have = profile.years_of_experience()
    if req is None:
        seniority_score = 0.7
    else:
        gap = abs(have - req)
        seniority_score = max(0.2, 1.0 - gap / 8.0)

    score = 100 * (0.40 * skill_score + 0.27 * title_score + 0.18 * location_score + 0.15 * seniority_score)
    missing = sorted(k for k in wanted if k not in skills)

    reasons = []
    reasons.append(f"{len(matched)} matching skill(s)" + (f": {', '.join(matched[:6])}" if matched else ""))
    if title_overlap:
        reasons.append("title matches your target roles")
    reasons.append(
        "remote-friendly" if (job.remote and prefs.remote_ok)
        else ("location matches preferences" if location_score >= 1.0 else "location may not match")
    )
    if missing:
        reasons.append("gaps: " + ", ".join(missing[:6]))

    return {
        "det_score": round(score, 1),
        "matched_skills": matched,
        "missing_keywords": missing,
        "reasons": "; ".join(reasons),
    }


_LLM_SCORE_SYSTEM = (
    "You are a technical recruiter. Given a candidate profile and a job posting, rate how good "
    "a fit the candidate is for THIS role. Be realistic and concise. Do not assume skills the "
    "candidate did not list. Return JSON with keys: score (integer 0-100), verdict (one short "
    "phrase like 'Strong fit'/'Possible fit'/'Weak fit'), rationale (<=2 sentences), "
    "strengths (array of <=4 short strings), gaps (array of <=4 short strings)."
)


def llm_score(profile: Profile, job: Job, llm) -> dict:
    user = (
        f"CANDIDATE PROFILE:\n{profile.to_text_block(4000)}\n\n"
        f"JOB POSTING:\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"Description:\n{util.truncate(job.description, 2500)}"
    )
    try:
        data = llm.complete_json(_LLM_SCORE_SYSTEM, user, temperature=0.2, max_tokens=500)
    except Exception as e:  # noqa: BLE001 - degrade gracefully to deterministic-only
        return {"llm_error": str(e)}
    try:
        score = int(round(float(data.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    return {
        "llm_score": max(0, min(100, score)),
        "verdict": str(data.get("verdict", "")).strip(),
        "rationale": str(data.get("rationale", "")).strip(),
        "strengths": [str(x) for x in (data.get("strengths") or [])][:4],
        "gaps": [str(x) for x in (data.get("gaps") or [])][:4],
    }


def rank_jobs(
    profile: Profile,
    jobs: List[Job],
    use_llm: bool = False,
    llm=None,
    llm_top: int = 10,
    top: Optional[int] = None,
) -> List[dict]:
    """Rank jobs best-first. Each entry contains the job dict plus scoring detail.

    ``final_score`` is the deterministic score, or (when the LLM re-ranked a job) a blend of the
    deterministic and LLM scores so both signals count.
    """
    ranked: List[dict] = []
    for job in jobs:
        det = deterministic_score(profile, job)
        ranked.append({
            "job": job.to_dict(),
            "det_score": det["det_score"],
            "final_score": det["det_score"],
            "matched_skills": det["matched_skills"],
            "missing_keywords": det["missing_keywords"],
            "reasons": det["reasons"],
        })

    ranked.sort(key=lambda e: e["final_score"], reverse=True)

    if use_llm and llm is not None:
        for entry in ranked[:llm_top]:
            job = Job.from_dict(entry["job"])
            result = llm_score(profile, job, llm)
            if "llm_score" in result:
                entry.update(result)
                entry["final_score"] = round(0.5 * entry["det_score"] + 0.5 * result["llm_score"], 1)
        ranked.sort(key=lambda e: e["final_score"], reverse=True)

    for i, entry in enumerate(ranked, 1):
        entry["rank"] = i

    return ranked[:top] if top else ranked
