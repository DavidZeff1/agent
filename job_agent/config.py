"""Configuration, paths, and environment loading for job_agent.

All persistent state lives under ``JOB_AGENT_HOME`` (default: ``~/.job_agent``).
The Groq API key is read from the environment. A local ``.env`` file (in the
current directory or in ``JOB_AGENT_HOME``) is loaded automatically so you do
not have to ``export`` the key on every shell.

Environment variables
---------------------
GROQ_API_KEY            Required for any LLM feature (scoring, tailoring, agent).
JOB_AGENT_HOME          Where profile + caches are stored. Default ~/.job_agent
JOB_AGENT_MODEL         Groq model id. Default llama-3.3-70b-versatile
JOB_AGENT_MIN_INTERVAL  Min seconds between LLM calls (free-tier throttle). Default 2.0
JOB_AGENT_MAX_RETRIES   Retries on rate-limit / transient errors. Default 5
JOB_AGENT_HTTP_TIMEOUT  Seconds for job-board HTTP requests. Default 20
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _load_dotenv(paths) -> None:
    """Minimal, dependency-free ``.env`` loader (does not overwrite real env vars)."""
    for p in paths:
        try:
            if not p or not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        except OSError:
            continue


# Hosted mode (e.g. Vercel): stateless serverless functions with a read-only filesystem.
# State lives in each visitor's browser; /tmp is only used as scratch space.
IS_HOSTED = bool(os.environ.get("VERCEL"))


def home() -> Path:
    default = "/tmp/job_agent" if IS_HOSTED else str(Path.home() / ".job_agent")
    return Path(os.environ.get("JOB_AGENT_HOME", default)).expanduser()


# Load .env once, before any settings are read.
_load_dotenv([os.path.join(os.getcwd(), ".env"), str(home() / ".env")])


class Settings:
    """Resolved runtime settings. Cheap to construct; call :meth:`ensure_home` before writing."""

    def __init__(self) -> None:
        self.home = home()
        self.profile_path = self.home / "profile.json"
        self.jobs_cache = self.home / "jobs_cache.json"
        self.ranked_path = self.home / "ranked.json"
        self.selection_path = self.home / "selection.json"
        self.applications_dir = self.home / "applications"

        self.model = os.environ.get("JOB_AGENT_MODEL", DEFAULT_MODEL)
        self.api_key = os.environ.get("GROQ_API_KEY", "").strip()

        self.min_request_interval = float(os.environ.get("JOB_AGENT_MIN_INTERVAL", "2.0"))
        self.max_retries = int(os.environ.get("JOB_AGENT_MAX_RETRIES", "5"))
        self.request_timeout = int(os.environ.get("JOB_AGENT_HTTP_TIMEOUT", "20"))
        self.user_agent = os.environ.get(
            "JOB_AGENT_UA", "job-agent/1.0 (personal job-search assistant)"
        )

    def ensure_home(self) -> "Settings":
        self.home.mkdir(parents=True, exist_ok=True)
        self.applications_dir.mkdir(parents=True, exist_ok=True)
        return self


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` (re-reads env each call; no filesystem side effects)."""
    return Settings()
