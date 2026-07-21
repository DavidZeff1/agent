"""Which companies' careers pages to check for a given country — automatically.

Three layers, so the user never has to configure anything:

1. ``BUILTIN`` — a shipped directory of well-known companies per country whose public
   careers APIs (Greenhouse / Lever / Ashby / SmartRecruiters) were live-verified when
   this file was written. Zero setup, zero extra requests to find them.
2. AI discovery — for countries not in the directory (or to widen it), one LLM call
   suggests likely companies + board slugs; the scraper probes them and the verified
   ones are cached in ``JOB_AGENT_HOME/companies_cache.json`` for 30 days.
3. Manual extras — anything the user types in Settings is probed and cached too.

Each entry is ``(slug, ats, display_name)`` where ``ats`` is one of greenhouse / lever /
ashby / smartrecruiters, so fetching hits the right API directly with no re-probing.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import util
from .config import get_settings

Entry = Tuple[str, str, str]  # (slug, ats, display name)

# Live-verified 2026-07 (slug, ats, name). Ordered roughly by number of open roles.
BUILTIN: dict[str, List[Entry]] = {
    "israel": [
        ("nice", "greenhouse", "NICE"), ("via", "greenhouse", "Via"),
        ("catonetworks", "greenhouse", "Cato Networks"), ("gongio", "greenhouse", "Gong.io"),
        ("similarweb", "greenhouse", "Similarweb"), ("taboola", "greenhouse", "Taboola"),
        ("redis", "ashby", "Redis"), ("walkme", "lever", "WalkMe"),
        ("fireblocks", "greenhouse", "Fireblocks"), ("appsflyer", "greenhouse", "AppsFlyer"),
        ("jfrog", "greenhouse", "JFrog"), ("armissecurity", "greenhouse", "Armis Security"),
        ("lemonade", "ashby", "Lemonade"), ("axonius", "greenhouse", "Axonius"),
        ("riskified", "greenhouse", "Riskified"), ("transmitsecurity", "greenhouse", "Transmit Security"),
        ("yotpo", "greenhouse", "Yotpo"), ("melio", "greenhouse", "Melio"),
        ("bigid", "greenhouse", "BigID"), ("lightricks", "greenhouse", "Lightricks"),
        ("pagaya", "greenhouse", "Pagaya"), ("gloat", "smartrecruiters", "Gloat"),
        ("cybereason", "greenhouse", "Cybereason"), ("bringg", "greenhouse", "Bringg"),
        ("riseup", "smartrecruiters", "RiseUp"), ("orcasecurity", "greenhouse", "Orca Security"),
        ("fiverr", "smartrecruiters", "Fiverr"), ("hibob", "smartrecruiters", "HiBob"),
        ("duda", "greenhouse", "Duda"),
    ],
    "united states": [
        ("spacex", "greenhouse", "SpaceX"), ("databricks", "greenhouse", "Databricks"),
        ("openai", "ashby", "OpenAI"), ("stripe", "greenhouse", "Stripe"),
        ("snowflake", "ashby", "Snowflake"), ("datadog", "greenhouse", "Datadog"),
        ("mongodb", "greenhouse", "MongoDB"), ("anthropic", "greenhouse", "Anthropic"),
        ("okta", "greenhouse", "Okta"), ("palantir", "lever", "Palantir"),
        ("brex", "greenhouse", "Brex"), ("cloudflare", "greenhouse", "Cloudflare"),
        ("airbnb", "greenhouse", "Airbnb"), ("elastic", "greenhouse", "Elastic"),
        ("reddit", "greenhouse", "Reddit"), ("pinterest", "greenhouse", "Pinterest"),
        ("scaleai", "greenhouse", "Scale AI"), ("figma", "greenhouse", "Figma"),
        ("instacart", "greenhouse", "Instacart"), ("twilio", "greenhouse", "Twilio"),
        ("asana", "greenhouse", "Asana"), ("notion", "ashby", "Notion"),
        ("gitlab", "greenhouse", "GitLab"), ("robinhood", "greenhouse", "Robinhood"),
        ("coinbase", "greenhouse", "Coinbase"), ("epicgames", "greenhouse", "Epic Games"),
        ("ramp", "ashby", "Ramp"), ("plaid", "ashby", "Plaid"),
        ("vercel", "greenhouse", "Vercel"), ("duolingo", "greenhouse", "Duolingo"),
        ("discord", "greenhouse", "Discord"), ("mercury", "greenhouse", "Mercury"),
        ("dropbox", "greenhouse", "Dropbox"), ("airtable", "greenhouse", "Airtable"),
        ("linear", "ashby", "Linear"), ("webflow", "greenhouse", "Webflow"),
        ("squarespace", "greenhouse", "Squarespace"), ("zapier", "ashby", "Zapier"),
        ("calendly", "greenhouse", "Calendly"),
    ],
    "united kingdom": [
        ("deliveroo", "ashby", "Deliveroo"), ("elevenlabs", "ashby", "ElevenLabs"),
        ("wayve", "greenhouse", "Wayve"), ("wise", "smartrecruiters", "Wise"),
        ("synthesia", "ashby", "Synthesia"), ("monzo", "greenhouse", "Monzo"),
        ("improbable", "ashby", "Improbable"),
    ],
    "germany": [
        ("hellofresh", "greenhouse", "HelloFresh"), ("celonis", "greenhouse", "Celonis"),
        ("deliveryhero", "smartrecruiters", "Delivery Hero"), ("n26", "greenhouse", "N26"),
        ("getyourguide", "greenhouse", "GetYourGuide"), ("raisin", "greenhouse", "Raisin"),
        ("solarisbank", "greenhouse", "Solaris"),
    ],
    "netherlands": [
        ("adyen", "greenhouse", "Adyen"), ("mollie", "ashby", "Mollie"),
        ("picnic", "smartrecruiters", "Picnic"),
    ],
    "france": [
        ("mistral", "lever", "Mistral AI"), ("doctolib", "greenhouse", "Doctolib"),
        ("alan", "ashby", "Alan"), ("qonto", "lever", "Qonto"),
        ("contentsquare", "lever", "Contentsquare"), ("swile", "lever", "Swile"),
        ("mirakl", "greenhouse", "Mirakl"), ("backmarket", "ashby", "Back Market"),
    ],
    "canada": [
        ("cohere", "ashby", "Cohere"), ("faire", "greenhouse", "Faire"),
        ("1password", "ashby", "1Password"), ("wealthsimple", "ashby", "Wealthsimple"),
        ("hootsuite", "greenhouse", "Hootsuite"), ("benchsci", "lever", "BenchSci"),
    ],
    "australia": [
        ("airwallex", "ashby", "Airwallex"), ("canva", "smartrecruiters", "Canva"),
    ],
    "india": [
        ("postman", "greenhouse", "Postman"), ("meesho", "lever", "Meesho"),
        ("zeta", "lever", "Zeta"), ("groww", "greenhouse", "Groww"),
        ("cred", "lever", "CRED"), ("swiggy", "smartrecruiters", "Swiggy"),
    ],
}

_ALIASES = {"usa": "united states", "us": "united states", "america": "united states",
            "uk": "united kingdom", "great britain": "united kingdom",
            "the netherlands": "netherlands", "holland": "netherlands", "deutschland": "germany",
            "ישראל": "israel", "il": "israel"}

_CACHE_TTL = 30 * 86400


def _norm_country(country: str) -> str:
    c = (country or "").strip().lower()
    return _ALIASES.get(c, c)


def _cache_path() -> str:
    return str(get_settings().home / "companies_cache.json")


def _load_cache() -> dict:
    return util.read_json(_cache_path(), default={}) or {}


def _save_cache(data: dict) -> None:
    get_settings().ensure_home()
    util.write_json(_cache_path(), data)


def builtin_for(country: str) -> List[Entry]:
    return [tuple(e) for e in BUILTIN.get(_norm_country(country), [])]


def cached_discovered(country: str) -> Optional[List[Entry]]:
    """Verified entries from a previous discovery run, or None when absent/stale."""
    entry = (_load_cache().get("discovered") or {}).get(_norm_country(country))
    if not entry or time.time() - entry.get("ts", 0) > _CACHE_TTL:
        return None
    return [tuple(e) for e in entry.get("entries", [])]


def save_discovered(country: str, entries: List[Entry]) -> None:
    cache = _load_cache()
    cache.setdefault("discovered", {})[_norm_country(country)] = {
        "ts": int(time.time()), "entries": [list(e) for e in entries],
    }
    _save_cache(cache)


def cached_manual(raw: str) -> Optional[Optional[Entry]]:
    """Resolution for one manually typed company. None = unknown; [None] = known-bad."""
    entry = (_load_cache().get("manual") or {}).get(raw.strip().lower())
    if not entry or time.time() - entry.get("ts", 0) > _CACHE_TTL:
        return None
    resolved = entry.get("entry")
    return [tuple(resolved)] if resolved else [None]


def save_manual(raw: str, entry: Optional[Entry]) -> None:
    cache = _load_cache()
    cache.setdefault("manual", {})[raw.strip().lower()] = {
        "ts": int(time.time()), "entry": list(entry) if entry else None,
    }
    _save_cache(cache)


_DISCOVER_SYSTEM = (
    "You know which employers in each country are actively hiring. List up to 20 well-known "
    "companies headquartered or with a major office in the given country that likely host their "
    "careers page on Greenhouse, Lever, Ashby, or SmartRecruiters (venture-backed tech and "
    "scale-ups usually do). For each give the company name and 1-3 likely careers-board slugs "
    "(lowercase, no spaces; e.g. Gong.io -> gongio, gong-io). Return JSON: "
    '{"companies": [{"name": "...", "slugs": ["...", "..."]}]}'
)


def discover_candidates(country: str, llm) -> List[dict]:
    """Ask the LLM for likely companies in a country. Returns [{name, slugs}] (unverified)."""
    try:
        data = llm.complete_json(_DISCOVER_SYSTEM, f"Country: {country}", max_tokens=1200)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] company discovery failed for {country}: {e}")
        return []
    out = []
    for c in (data.get("companies") or [])[:20]:
        if isinstance(c, dict) and c.get("name"):
            slugs = [str(s).strip().lower() for s in (c.get("slugs") or []) if str(s).strip()]
            out.append({"name": str(c["name"]), "slugs": slugs[:3]})
    return out
