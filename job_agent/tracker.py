"""Remember every job you've seen and what happened to it.

This is what makes applying at volume sustainable: a job you skipped, prepared, or
submitted never shows up as "new" again, and the app can tell you exactly what's
waiting for you. Stored as one JSON map (job id -> entry) in ``JOB_AGENT_HOME/seen.json``.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import util
from .config import get_settings

# Parallel preparation workers update statuses concurrently; serialize the
# read-modify-write of seen.json so no update is lost.
_LOCK = threading.Lock()

STATUSES = ("new", "skipped", "prepared", "submitted", "interview", "rejected")

# Statuses that mean "stop showing me this job as new".
HANDLED = {"skipped", "prepared", "submitted", "interview", "rejected"}


def _path() -> str:
    return str(get_settings().home / "seen.json")


def load() -> dict:
    return util.read_json(_path(), default={}) or {}


def save(data: dict) -> None:
    get_settings().ensure_home()
    util.write_json(_path(), data)


def record_seen(jobs) -> int:
    """Register jobs, adding unseen ones as 'new'. Returns how many were brand new."""
    with _LOCK:
        data = load()
        now = int(time.time())
        new = 0
        for j in jobs:
            job = j if isinstance(j, dict) else j.to_dict()
            jid = job.get("id")
            if not jid:
                continue
            if jid not in data:
                data[jid] = {
                    "status": "new", "first_seen": now, "updated": now,
                    "title": job.get("title", ""), "company": job.get("company", ""),
                    "url": job.get("url", ""),
                }
                new += 1
        if new:
            save(data)
        return new


def set_status(job_id: str, status: str, title: str = "", company: str = "", url: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; use one of {STATUSES}")
    with _LOCK:
        data = load()
        entry = data.setdefault(job_id, {"first_seen": int(time.time()), "title": title,
                                         "company": company, "url": url})
        entry["status"] = status
        entry["updated"] = int(time.time())
        save(data)
        return entry


def get(job_id: str) -> Optional[dict]:
    return load().get(job_id)


def counts() -> dict:
    out = {s: 0 for s in STATUSES}
    for e in load().values():
        s = e.get("status", "new")
        if s in out:
            out[s] += 1
    return out
