"""Job-scraping tools.

Sources are public JSON job APIs that don't require accounts or keys, so this works out of
the box and stays polite (no HTML scraping / bot evasion):

* Remotive        https://remotive.com/api/remote-jobs   (remote roles; small free feed)
* RemoteOK        https://remoteok.com/api                (remote roles)
* Arbeitnow       https://www.arbeitnow.com/api/job-board-api  (paginated)
* Jobicy          https://jobicy.com/api/v2/remote-jobs   (remote roles, server-side keyword search)
* Himalayas       https://himalayas.app/jobs/api          (remote roles)
* WeWorkRemotely  https://weworkremotely.com/remote-jobs.rss  (RSS)
* Hacker News     latest "Ask HN: Who is hiring?" thread via the Algolia API

Each source is isolated in a try/except so one failing (network or a changed schema) never
kills the whole search. Results are normalized to :class:`Job`, filtered by the user's
keywords / location / remote preference, and de-duplicated.

Note on terms of use: these APIs are provided for developers to *surface* jobs and link back
to the original posting — which is exactly what this tool does. Respect each board's terms and
rate limits; do not re-publish their listings elsewhere.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass, field
from html import unescape
from typing import List, Optional

import requests

from . import util
from .config import get_settings


def _clean(s) -> str:
    """Fix the text problems real boards ship: HTML entities and double-encoded UTF-8."""
    s = unescape(unescape(str(s or ""))).strip()
    if "Ã" in s or "â" in s:  # UTF-8 bytes mis-decoded as Latin-1 upstream (e.g. RemoteOK)
        try:
            repaired = s.encode("latin-1").decode("utf-8")
            if "Ã" not in repaired:
                s = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


@dataclass
class Job:
    id: str
    source: str
    title: str
    company: str
    location: str = ""
    remote: Optional[bool] = None
    url: str = ""
    apply_url: str = ""
    description: str = ""  # plain text (HTML already stripped)
    tags: List[str] = field(default_factory=list)
    salary: str = ""
    date: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        allowed = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})

    def search_text(self) -> str:
        return " ".join([self.title, self.company, self.location, " ".join(self.tags), self.description]).lower()


def _make_id(source: str, url: str, title: str, company: str) -> str:
    basis = url or f"{title}@{company}"
    return f"{source}-" + hashlib.sha1(basis.encode("utf-8", "ignore")).hexdigest()[:12]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": get_settings().user_agent, "Accept": "application/json"})
    return s


def _get_json(sess: requests.Session, url: str, params: dict | None = None):
    resp = sess.get(url, params=params or {}, timeout=get_settings().request_timeout)
    resp.raise_for_status()
    return resp.json()


# ----------------------------------------------------------------------------
# Individual sources (each returns a list[Job], or [] on failure)
# ----------------------------------------------------------------------------


def fetch_remotive(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    try:
        data = _get_json(sess, "https://remotive.com/api/remote-jobs",
                         {"search": keywords, "limit": max(limit, 20)})
        jobs = []
        for j in data.get("jobs", []):
            jobs.append(Job(
                id=_make_id("remotive", j.get("url", ""), j.get("title", ""), j.get("company_name", "")),
                source="remotive",
                title=j.get("title", "").strip(),
                company=j.get("company_name", "").strip(),
                location=(j.get("candidate_required_location") or "").strip(),
                remote=True,
                url=j.get("url", ""),
                apply_url=j.get("url", ""),
                description=util.html_to_text(j.get("description", "")),
                tags=[t for t in (j.get("tags") or []) if t],
                salary=(j.get("salary") or "").strip(),
                date=(j.get("publication_date") or "").strip(),
            ))
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] remotive source failed: {e}")
        return []


def fetch_remoteok(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    try:
        data = _get_json(sess, "https://remoteok.com/api")
        jobs = []
        for j in data:
            if not isinstance(j, dict) or not j.get("position"):
                continue  # first element is legal/metadata
            jobs.append(Job(
                id=_make_id("remoteok", j.get("url", ""), j.get("position", ""), j.get("company", "")),
                source="remoteok",
                title=(j.get("position") or "").strip(),
                company=(j.get("company") or "").strip(),
                location=(j.get("location") or "Remote").strip(),
                remote=True,
                url=j.get("url", ""),
                apply_url=j.get("apply_url") or j.get("url", ""),
                description=util.html_to_text(j.get("description", "")),
                tags=[t for t in (j.get("tags") or []) if t],
                salary=_salary_range(j.get("salary_min"), j.get("salary_max")),
                date=(j.get("date") or "").strip(),
            ))
        return jobs  # keyword filtering happens in the aggregator
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] remoteok source failed: {e}")
        return []


def fetch_arbeitnow(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    jobs: List[Job] = []
    try:
        for page in range(1, 4):  # the feed is paginated (100/page); no server-side search
            data = _get_json(sess, "https://www.arbeitnow.com/api/job-board-api", {"page": page})
            batch = data.get("data", [])
            for j in batch:
                jobs.append(Job(
                    id=_make_id("arbeitnow", j.get("url", ""), j.get("title", ""), j.get("company_name", "")),
                    source="arbeitnow",
                    title=(j.get("title") or "").strip(),
                    company=(j.get("company_name") or "").strip(),
                    location=(j.get("location") or "").strip(),
                    remote=bool(j.get("remote")),
                    url=j.get("url", ""),
                    apply_url=j.get("url", ""),
                    description=util.html_to_text(j.get("description", "")),
                    tags=[t for t in (j.get("tags") or []) if t] + [t for t in (j.get("job_types") or []) if t],
                    salary="",
                    date=str(j.get("created_at") or ""),
                ))
            if not batch:
                break
        return jobs
    except Exception as e:  # noqa: BLE001
        if jobs:
            return jobs  # keep whatever pages succeeded
        print(f"  [warn] arbeitnow source failed: {e}")
        return []


def fetch_jobicy(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    """Jobicy has real server-side keyword search, so query each term separately."""
    terms = [t for t in keywords.split() if t][:3] or [""]
    jobs: List[Job] = []
    ok = False
    for term in terms:
        try:
            params = {"count": min(max(limit, 20), 50)}
            if term:
                params["tag"] = term
            data = _get_json(sess, "https://jobicy.com/api/v2/remote-jobs", params)
            ok = True
            for j in data.get("jobs", []):
                jobs.append(Job(
                    id=_make_id("jobicy", j.get("url", ""), j.get("jobTitle", ""), j.get("companyName", "")),
                    source="jobicy",
                    title=(j.get("jobTitle") or "").strip(),
                    company=(j.get("companyName") or "").strip(),
                    location=(j.get("jobGeo") or "").strip(),
                    remote=True,
                    url=j.get("url", ""),
                    apply_url=j.get("url", ""),
                    description=util.html_to_text(j.get("jobDescription") or j.get("jobExcerpt") or ""),
                    tags=[t for t in (j.get("jobIndustry") or []) if t] + [t for t in (j.get("jobType") or []) if t],
                    salary=_salary_range(j.get("annualSalaryMin"), j.get("annualSalaryMax")),
                    date=(j.get("pubDate") or "").strip(),
                ))
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] jobicy query '{term}' failed: {e}")
    if not ok:
        print("  [warn] jobicy source failed entirely")
    return jobs


def fetch_himalayas(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    try:
        data = _get_json(sess, "https://himalayas.app/jobs/api", {"limit": min(max(limit, 20), 100)})
        jobs = []
        for j in data.get("jobs", []):
            locs = j.get("locationRestrictions") or []
            if isinstance(locs, str):  # the API ships a stringified list
                locs = [x.strip(" '\"") for x in locs.strip("[]").split(",") if x.strip(" '\"")]
            date = ""
            try:
                from datetime import datetime, timezone
                date = datetime.fromtimestamp(int(j.get("pubDate") or 0), tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                pass
            url = j.get("guid") or j.get("applicationLink") or ""
            jobs.append(Job(
                id=_make_id("himalayas", url, j.get("title", ""), j.get("companyName", "")),
                source="himalayas",
                title=(j.get("title") or "").strip(),
                company=(j.get("companyName") or "").strip(),
                location=", ".join(locs[:4]),
                remote=True,
                url=url,
                apply_url=j.get("applicationLink") or url,
                description=util.html_to_text(j.get("description") or j.get("excerpt") or ""),
                tags=[t for t in (j.get("categories") or []) if t] + [t for t in [j.get("seniority")] if t],
                salary=_salary_range(j.get("minSalary"), j.get("maxSalary")),
                date=date,
            ))
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] himalayas source failed: {e}")
        return []


def fetch_weworkremotely(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    try:
        import xml.etree.ElementTree as ET

        resp = sess.get("https://weworkremotely.com/remote-jobs.rss", timeout=get_settings().request_timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        jobs = []
        for item in root.iter("item"):
            raw_title = (item.findtext("title") or "").strip()
            company, _, role = raw_title.partition(":")
            if not role:
                company, role = "", raw_title
            link = (item.findtext("link") or "").strip()
            jobs.append(Job(
                id=_make_id("weworkremotely", link, role, company),
                source="weworkremotely",
                title=role.strip(),
                company=company.strip(),
                location=(item.findtext("region") or "").strip() or "Remote",
                remote=True,
                url=link,
                apply_url=link,
                description=util.html_to_text(item.findtext("description") or ""),
                tags=[c.text.strip() for c in item.findall("category") if c.text],
                salary="",
                date=(item.findtext("pubDate") or "").strip(),
            ))
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] weworkremotely source failed: {e}")
        return []


_LOCATION_FIRST = re.compile(r",\s*[A-Z]{2}\b|^(remote|usa|us\b|uk\b|eu\b|emea|worldwide|global)", re.I)


def fetch_hn_hiring(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    """The latest 'Ask HN: Who is hiring?' thread — each top-level comment is a posting.

    The conventional first line is 'Company | Role | Location | ...' but authors improvise,
    so the title keeps the whole first line and the company is a best-effort guess.
    """
    try:
        hits = _get_json(sess, "https://hn.algolia.com/api/v1/search_by_date",
                         {"tags": "story,author_whoishiring", "query": "who is hiring", "hitsPerPage": 5}).get("hits", [])
        story = next((h for h in hits if "who is hiring" in (h.get("title") or "").lower()), None)
        if not story:
            return []
        item = _get_json(sess, f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
        month = (item.get("title") or "").split("(")[-1].rstrip(")")
        jobs = []
        for c in (item.get("children") or [])[:400]:
            if not c.get("text"):
                continue
            text = util.html_to_text(c["text"])
            first = text.split("\n", 1)[0].strip()
            parts = [p.strip() for p in first.split("|") if p.strip()]
            if len(parts) < 2 or len(first) > 220:
                continue  # not in the job-post format
            if _LOCATION_FIRST.search(parts[0]) and len(parts) >= 3:
                company = parts[1]
            else:
                company = parts[0]
            jobs.append(Job(
                id=_make_id("hn", f"hn-{c.get('id')}", first, company),
                source="hn",
                title=first[:140],
                company=company[:80],
                location="",
                remote="remote" in first.lower(),
                url=f"https://news.ycombinator.com/item?id={c.get('id')}",
                apply_url=f"https://news.ycombinator.com/item?id={c.get('id')}",
                description=text,
                tags=["Hacker News", month],
                salary="",
                date=(c.get("created_at") or "")[:10],
            ))
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] hn source failed: {e}")
        return []


def fetch_themuse(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    """The Muse public API (keyless, mostly US/EU on-site + hybrid roles)."""
    jobs: List[Job] = []
    try:
        for page in range(1, 3):
            data = _get_json(sess, "https://www.themuse.com/api/public/jobs", {"page": page})
            for j in data.get("results", []):
                locs = ", ".join(x.get("name", "") for x in (j.get("locations") or [])[:3])
                jobs.append(Job(
                    id=_make_id("themuse", (j.get("refs") or {}).get("landing_page", ""),
                                j.get("name", ""), (j.get("company") or {}).get("name", "")),
                    source="themuse",
                    title=(j.get("name") or "").strip(),
                    company=((j.get("company") or {}).get("name") or "").strip(),
                    location=locs,
                    remote="flexible" in locs.lower() or "remote" in locs.lower(),
                    url=(j.get("refs") or {}).get("landing_page", ""),
                    apply_url=(j.get("refs") or {}).get("landing_page", ""),
                    description=util.html_to_text(j.get("contents") or ""),
                    tags=[c.get("name", "") for c in (j.get("categories") or []) if c.get("name")],
                    salary="",
                    date=(j.get("publication_date") or "")[:10],
                ))
            if page >= data.get("page_count", 1):
                break
        return jobs
    except Exception as e:  # noqa: BLE001
        if jobs:
            return jobs
        print(f"  [warn] themuse source failed: {e}")
        return []


def fetch_jooble(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    """Jooble aggregator (~69 countries incl. Israel). Activates when a free API key is set."""
    opts = opts or {}
    key = str((opts.get("config") or {}).get("jooble_key", "")).strip()
    if not key:
        return []  # not configured — quietly do nothing
    try:
        where = opts.get("location") or opts.get("country") or ""
        resp = sess.post(f"https://jooble.org/api/{key}",
                         json={"keywords": keywords, "location": where, "page": 1},
                         timeout=get_settings().request_timeout)
        resp.raise_for_status()
        jobs = []
        for j in resp.json().get("jobs", []):
            loc = (j.get("location") or "").strip()
            jobs.append(Job(
                id=_make_id("jooble", j.get("link", ""), j.get("title", ""), j.get("company", "")),
                source="jooble",
                title=(j.get("title") or "").strip(),
                company=(j.get("company") or "").strip(),
                location=loc,
                remote="remote" in f"{j.get('title', '')} {loc}".lower() or None,
                url=j.get("link", ""),
                apply_url=j.get("link", ""),
                description=util.html_to_text(j.get("snippet") or ""),
                tags=[t for t in [j.get("type")] if t],
                salary=(str(j.get("salary")) if j.get("salary") else "").strip(),
                date=(j.get("updated") or "")[:10],
            ))
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] jooble source failed: {e}")
        return []


_ADZUNA_COUNTRIES = {
    "united states": "us", "usa": "us", "united kingdom": "gb", "uk": "gb", "austria": "at",
    "australia": "au", "belgium": "be", "brazil": "br", "canada": "ca", "switzerland": "ch",
    "germany": "de", "spain": "es", "france": "fr", "india": "in", "italy": "it",
    "mexico": "mx", "netherlands": "nl", "new zealand": "nz", "poland": "pl",
    "singapore": "sg", "south africa": "za",
}


def fetch_adzuna(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    """Adzuna aggregator (20 countries, strong local on-site coverage). Needs free API keys."""
    opts = opts or {}
    cfg = opts.get("config") or {}
    app_id = str(cfg.get("adzuna_app_id", "")).strip()
    app_key = str(cfg.get("adzuna_app_key", "")).strip()
    cc = _ADZUNA_COUNTRIES.get((opts.get("country") or "").strip().lower())
    if not (app_id and app_key and cc):
        return []  # not configured or unsupported country — quietly do nothing
    try:
        data = _get_json(sess, f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1", {
            "app_id": app_id, "app_key": app_key, "what": keywords,
            "where": opts.get("location") or "", "results_per_page": min(max(limit, 20), 50),
        })
        jobs = []
        for j in data.get("results", []):
            jobs.append(Job(
                id=_make_id("adzuna", j.get("redirect_url", ""), j.get("title", ""),
                            (j.get("company") or {}).get("display_name", "")),
                source="adzuna",
                title=(j.get("title") or "").strip(),
                company=((j.get("company") or {}).get("display_name") or "").strip(),
                location=((j.get("location") or {}).get("display_name") or "").strip(),
                remote=None,
                url=j.get("redirect_url", ""),
                apply_url=j.get("redirect_url", ""),
                description=(j.get("description") or "").strip(),
                tags=[((j.get("category") or {}).get("label") or "")] if j.get("category") else [],
                salary=_salary_range(j.get("salary_min"), j.get("salary_max")),
                date=(j.get("created") or "")[:10],
            ))
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] adzuna source failed: {e}")
        return []


def _company_ats_jobs(sess, name: str) -> List[Job]:
    """Openings for one company via public ATS APIs (Greenhouse/Lever/Ashby/SmartRecruiters)."""
    slug = re.sub(r"[^a-z0-9-]", "", name.strip().lower().replace(" ", ""))
    if not slug:
        return []
    timeout = min(get_settings().request_timeout, 10)

    try:  # Greenhouse
        r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                     params={"content": "true"}, timeout=timeout)
        if r.status_code == 200 and r.json().get("jobs"):
            try:  # the board's real name makes a wrong-company match obvious in results
                board = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout=timeout)
                if board.status_code == 200 and board.json().get("name"):
                    name = board.json()["name"]
            except Exception:  # noqa: BLE001
                pass
            return [Job(
                id=_make_id("companies", j.get("absolute_url", ""), j.get("title", ""), name),
                source="companies", title=(j.get("title") or "").strip(), company=name,
                location=((j.get("location") or {}).get("name") or "").strip(),
                remote="remote" in ((j.get("location") or {}).get("name") or "").lower() or None,
                url=j.get("absolute_url", ""), apply_url=j.get("absolute_url", ""),
                description=util.html_to_text(j.get("content") or ""),
                tags=["watched company"], salary="", date=(j.get("updated_at") or "")[:10],
            ) for j in r.json()["jobs"]]
    except Exception:  # noqa: BLE001
        pass

    try:  # Lever
        r = sess.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=timeout)
        if r.status_code == 200 and isinstance(r.json(), list) and r.json():
            return [Job(
                id=_make_id("companies", j.get("hostedUrl", ""), j.get("text", ""), name),
                source="companies", title=(j.get("text") or "").strip(), company=name,
                location=((j.get("categories") or {}).get("location") or "").strip(),
                remote="remote" in ((j.get("categories") or {}).get("location") or "").lower() or None,
                url=j.get("hostedUrl", ""), apply_url=j.get("hostedUrl", ""),
                description=(j.get("descriptionPlain") or "")[:6000],
                tags=["watched company"], salary="", date="",
            ) for j in r.json()]
    except Exception:  # noqa: BLE001
        pass

    try:  # Ashby
        r = sess.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=timeout)
        if r.status_code == 200 and r.json().get("jobs"):
            return [Job(
                id=_make_id("companies", j.get("jobUrl", ""), j.get("title", ""), name),
                source="companies", title=(j.get("title") or "").strip(), company=name,
                location=(j.get("location") or "").strip(),
                remote=bool(j.get("isRemote")) or None,
                url=j.get("jobUrl", ""), apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
                description=util.html_to_text(j.get("descriptionHtml") or "") or (j.get("descriptionPlain") or ""),
                tags=["watched company"], salary="", date=(j.get("publishedAt") or "")[:10],
            ) for j in r.json()["jobs"]]
    except Exception:  # noqa: BLE001
        pass

    try:  # SmartRecruiters
        r = sess.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", timeout=timeout)
        if r.status_code == 200 and r.json().get("content"):
            return [Job(
                id=_make_id("companies", str(j.get("id", "")), j.get("name", ""), name),
                source="companies", title=(j.get("name") or "").strip(),
                company=((j.get("company") or {}).get("name") or name),
                location=", ".join(x for x in [((j.get("location") or {}).get("city") or ""),
                                               ((j.get("location") or {}).get("country") or "").upper()] if x),
                remote=bool((j.get("location") or {}).get("remote")) or None,
                url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                apply_url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                description="", tags=["watched company"], salary="",
                date=(j.get("releasedDate") or "")[:10],
            ) for j in r.json()["content"]]
    except Exception:  # noqa: BLE001
        pass
    return []


def fetch_companies(sess, keywords: str, limit: int, opts=None) -> List[Job]:
    """Careers pages of companies the user watches (set in Settings). Shows ALL their openings
    that match the keywords — the most targeted source there is."""
    opts = opts or {}
    raw = str((opts.get("config") or {}).get("watched_companies", "")).strip()
    if not raw:
        return []
    jobs: List[Job] = []
    for name in [c.strip() for c in raw.split(",") if c.strip()][:10]:
        found = _company_ats_jobs(sess, name)
        if not found:
            print(f"  [note] no public careers API found for '{name}' "
                  f"(works for companies on Greenhouse, Lever, Ashby, or SmartRecruiters)")
        jobs.extend(found)
    return jobs


def _salary_range(lo, hi) -> str:
    try:
        lo = int(lo) if lo else 0
        hi = int(hi) if hi else 0
    except (TypeError, ValueError):
        return ""
    if lo and hi:
        return f"${lo:,} - ${hi:,}"
    if lo:
        return f"${lo:,}+"
    return ""


SOURCES = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "arbeitnow": fetch_arbeitnow,
    "jobicy": fetch_jobicy,
    "himalayas": fetch_himalayas,
    "weworkremotely": fetch_weworkremotely,
    "hn": fetch_hn_hiring,
    "themuse": fetch_themuse,
    "companies": fetch_companies,   # careers pages of companies you watch (Settings)
    "jooble": fetch_jooble,         # ~69 countries incl. Israel; free key (Settings)
    "adzuna": fetch_adzuna,         # 20 countries, local on-site jobs; free keys (Settings)
}


# ----------------------------------------------------------------------------
# Aggregation, filtering, dedup
# ----------------------------------------------------------------------------


def _term_in(term: str, text: str) -> bool:
    """A term matches when all of its words appear, so the phrase 'Customer Support
    Specialist' (how people type job titles) also matches 'Customer Support Specialist
    with English' or 'Specialist, Customer Support'."""
    return all(w in text for w in term.lower().split())


def _keyword_tier(job: Job, terms: List[str]) -> int:
    """3 = keyword in title, 2 = in tags/company, 1 = only in description, 0 = no match.

    Boards no longer do reliable server-side search, so this client-side tier is the real
    relevance filter. Titles are the only trustworthy signal — boards attach generic tag
    clouds ('python' on an unrelated sales job) — so title matches fill the result list
    first and weaker matches only pad out what's left.
    """
    if not terms:
        return 3
    if any(_term_in(t, job.title.lower()) for t in terms):
        return 3
    if any(_term_in(t, f"{job.company} {' '.join(job.tags)}".lower()) for t in terms):
        return 2
    if any(_term_in(t, job.description.lower()) for t in terms):
        return 1
    return 0


def _matches_location(job: Job, location: str, remote: Optional[bool]) -> bool:
    if remote is True and not job.remote:
        # user wants remote-only; drop clearly non-remote postings
        if job.location and "remote" not in job.location.lower():
            return False
    loc = (location or "").strip().lower()
    if loc in ("", "remote", "any", "anywhere", "worldwide", "global"):
        return True  # not a real place constraint
    if job.remote:
        return True  # remote roles are reachable from any location; ranking scores the geo fit
    return loc in (job.location or "").lower()


def _dedupe(jobs: List[Job]) -> List[Job]:
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    out: List[Job] = []
    for j in jobs:
        url = (j.url or "").split("?")[0].rstrip("/").lower()
        key = f"{util.slugify(j.title)}::{util.slugify(j.company)}"
        if url and url in seen_urls:
            continue
        if key in seen_keys:
            continue
        if url:
            seen_urls.add(url)
        seen_keys.add(key)
        out.append(j)
    return out


def search_jobs(
    keywords,
    location: str = "",
    remote: Optional[bool] = None,
    limit: int = 30,
    sources: Optional[List[str]] = None,
    country: str = "",
    config: Optional[dict] = None,
) -> List[Job]:
    """Search all sources, filter to the user's criteria, dedupe, and return up to ``limit`` jobs.

    ``country`` (from the profile) and ``config`` (free aggregator keys + watched companies)
    activate the country-aware sources; without them those sources quietly do nothing.
    """
    if isinstance(keywords, str):
        terms = [k.strip() for k in keywords.split(",") if k.strip()]
    else:
        terms = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    query = " ".join(terms)
    opts = {"location": location, "country": country, "config": config or {}}

    use = sources or list(SOURCES.keys())
    sess = _session()
    collected: List[tuple[int, Job]] = []
    for name in use:
        fn = SOURCES.get(name)
        if not fn:
            print(f"  [warn] unknown source '{name}' skipped")
            continue
        for j in fn(sess, query, limit, opts):
            j.title = _clean(j.title)
            j.company = _clean(j.company)
            j.location = _clean(j.location)
            j.tags = [_clean(t) for t in j.tags if _clean(t)]
            j.description = _clean(j.description)
            j.salary = _clean(j.salary)
            if not j.title or not j.company:
                continue
            if not _matches_location(j, location, remote):
                continue
            tier = _keyword_tier(j, terms)
            if tier and j.source == "companies":
                tier += 1  # openings at companies the user watches outrank generic matches
            if tier:
                collected.append((tier, j))

    # Strong (title/tag) matches first, so weak matches never crowd them out of `limit`.
    collected.sort(key=lambda x: x[0], reverse=True)
    deduped = _dedupe([j for _, j in collected])
    return deduped[:limit]
