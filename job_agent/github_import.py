"""Turn a GitHub account's public repositories into resume-ready profile projects.

Uses GitHub's public REST API (no token needed for public repos; the unauthenticated
limit of 60 requests/hour is plenty — one request per import). Forks, archived repos,
and undescribed throwaways are skipped; the rest are ranked by stars and recency. When
the LLM is available it rewrites each description into one clear resume sentence and
adds factual highlights — it is given only the repo metadata, so it can't invent.
"""
from __future__ import annotations

import json
import re
from typing import List

import requests

from .config import get_settings


def parse_username(s: str) -> str:
    s = (s or "").strip().rstrip("/")
    m = re.search(r"github\.com/([A-Za-z0-9-]+)", s)
    if m:
        return m.group(1)
    return re.sub(r"[^A-Za-z0-9-]", "", s)


def fetch_repos(username: str, limit: int = 8) -> List[dict]:
    resp = requests.get(
        f"https://api.github.com/users/{username}/repos",
        params={"per_page": 100, "sort": "pushed"},
        headers={"Accept": "application/vnd.github+json", "User-Agent": "job-agent"},
        timeout=get_settings().request_timeout,
    )
    if resp.status_code == 404:
        raise ValueError(f"GitHub user '{username}' was not found.")
    if resp.status_code == 403:
        raise ValueError("GitHub is rate-limiting right now — try again in a few minutes.")
    resp.raise_for_status()
    repos = [r for r in resp.json() if isinstance(r, dict) and not r.get("fork") and not r.get("archived")]
    repos.sort(key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")), reverse=True)
    out = []
    for r in repos:
        if len(out) >= limit:
            break
        if not r.get("description") and r.get("stargazers_count", 0) < 2:
            continue  # skip undescribed throwaways
        out.append({
            "name": r.get("name", ""),
            "description": r.get("description") or "",
            "language": r.get("language") or "",
            "topics": (r.get("topics") or [])[:6],
            "stars": r.get("stargazers_count", 0),
            "url": r.get("html_url", ""),
            "homepage": r.get("homepage") or "",
        })
    return out


_WRITEUP_SYSTEM = (
    "You turn raw GitHub repository metadata into resume-ready project entries. Use ONLY the "
    "provided facts — never invent features, users, or metrics beyond what is given. Stay close "
    "to the description's own wording; if it is ambiguous, do not guess at its meaning. Return a "
    "JSON object: {\"projects\": [{\"name\": the repo name unchanged, \"description\": one clear "
    "plain-language sentence about what it is/does, \"highlights\": array of 0-1 short factual "
    "bullets (omit filler such as restating the programming language)}]} in the same order as "
    "the input."
)


def to_projects(repos: List[dict], llm=None) -> List[dict]:
    """Convert repo metadata to profile project dicts, with optional LLM-polished writeups."""
    writeups = {}
    if llm is not None and repos:
        try:
            data = llm.complete_json(_WRITEUP_SYSTEM, json.dumps(repos, ensure_ascii=False), max_tokens=1500)
            for w in data.get("projects", []):
                if isinstance(w, dict) and w.get("name"):
                    writeups[str(w["name"])] = w
        except Exception as e:  # noqa: BLE001 - fall back to the raw descriptions
            print(f"  [warn] GitHub writeup failed, using raw descriptions: {e}")

    projects = []
    for r in repos:
        w = writeups.get(r["name"], {})
        tech = ([r["language"]] if r["language"] else []) + list(r["topics"])
        highlights = [str(h) for h in (w.get("highlights") or []) if str(h).strip()][:1]
        if r["stars"] >= 25 and not any("star" in h.lower() for h in highlights):
            highlights.append(f"{r['stars']:,} GitHub stars")
        projects.append({
            "name": r["name"],
            "description": str(w.get("description") or r["description"]),
            "tech": tech,
            "link": r["homepage"],
            "repo": r["url"],
            "role": "",
            "highlights": highlights,
        })
    return projects
