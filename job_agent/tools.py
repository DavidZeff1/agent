"""Tool definitions, registry, and dispatch.

How tools are defined and dispatched
------------------------------------
Every capability is a :class:`Tool` = name + description + JSON-Schema parameters + a Python
callable. :meth:`Tool.to_schema` emits the exact ``{"type": "function", ...}`` shape Groq's
(OpenAI-compatible) tool-calling expects, and :meth:`ToolRegistry.dispatch` runs a tool by name
with a dict of arguments and returns a JSON string.

The same registry backs BOTH dispatch styles:

* **Simple router** — the CLI pipeline (search -> match -> review -> apply) calls tools directly
  in a fixed order. Deterministic, free (no tokens spent on orchestration), and reliable.
* **Groq tool-calling** — :mod:`job_agent.agent` hands these schemas to the model so it can decide
  which tools to call for open-ended natural-language requests.

Tools deliberately operate on stored state (the profile, the jobs cache, ranked results) and
return compact *summaries* rather than large blobs, which keeps token usage — and rate-limit
pressure — low during tool-calling.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import matching, scraper
from . import generate as gen
from . import profile_store
from .config import get_settings
from .profile_store import Profile
from .scraper import Job


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments object
    func: Callable[..., Any]

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self) -> List[dict]:
        return [t.to_schema() for t in self._tools.values()]

    def names(self) -> List[str]:
        return list(self._tools)

    def dispatch(self, name: str, arguments: dict | str) -> str:
        """Run ``name`` with ``arguments`` (dict or JSON string). Always returns a JSON string."""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool '{name}'"})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return json.dumps({"error": "arguments were not valid JSON"})
        try:
            result = tool.func(**(arguments or {}))
        except TypeError as e:
            return json.dumps({"error": f"bad arguments for {name}: {e}"})
        except Exception as e:  # noqa: BLE001 - surface tool errors to the caller/model
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


@dataclass
class Context:
    """Shared state passed to every tool (profile, optional LLM, resolved paths)."""

    profile: Profile
    llm: Any = None
    settings: Any = field(default_factory=get_settings)

    # -- convenience persistence -----------------------------------------
    def load_jobs(self) -> List[Job]:
        from . import util

        data = util.read_json(self.settings.jobs_cache, default=[]) or []
        return [Job.from_dict(d) for d in data]

    def save_jobs(self, jobs: List[Job]) -> None:
        from . import util

        self.settings.ensure_home()
        util.write_json(self.settings.jobs_cache, [j.to_dict() for j in jobs])

    def load_ranked(self) -> List[dict]:
        from . import util

        return util.read_json(self.settings.ranked_path, default=[]) or []

    def save_ranked(self, ranked: List[dict]) -> None:
        from . import util

        self.settings.ensure_home()
        util.write_json(self.settings.ranked_path, ranked)


# ----------------------------------------------------------------------------
# Tool implementations (bound to a Context)
# ----------------------------------------------------------------------------


def build_registry(ctx: Context) -> ToolRegistry:
    reg = ToolRegistry()

    def search_jobs(keywords, location: str = "", remote: bool = True, limit: int = 30, sources=None) -> dict:
        from .config import load_app_settings

        jobs = scraper.search_jobs(keywords, location=location, remote=remote, limit=limit, sources=sources,
                                   country=ctx.profile.contact.country, config=load_app_settings())
        ctx.save_jobs(jobs)
        return {
            "found": len(jobs),
            "cached_to": str(ctx.settings.jobs_cache),
            "sample": [f"{j.title} @ {j.company} ({j.location or 'n/a'})" for j in jobs[:8]],
        }

    def rank_jobs(use_llm: bool = False, top: int = 10) -> dict:
        jobs = ctx.load_jobs()
        if not jobs:
            return {"error": "no jobs cached; run search_jobs first"}
        ranked = matching.rank_jobs(ctx.profile, jobs, use_llm=use_llm and ctx.llm is not None, llm=ctx.llm)
        ctx.save_ranked(ranked)
        return {
            "ranked": len(ranked),
            "top": [
                {
                    "rank": e["rank"],
                    "score": e["final_score"],
                    "title": e["job"]["title"],
                    "company": e["job"]["company"],
                    "verdict": e.get("verdict", ""),
                }
                for e in ranked[:top]
            ],
        }

    def list_ranked(top: int = 10) -> dict:
        ranked = ctx.load_ranked()
        return {
            "count": len(ranked),
            "jobs": [
                {"rank": e["rank"], "score": e["final_score"], "id": e["job"]["id"],
                 "title": e["job"]["title"], "company": e["job"]["company"]}
                for e in ranked[:top]
            ],
        }

    def generate_application(job_id: str) -> dict:
        ranked = ctx.load_ranked()
        jobs = {e["job"]["id"]: e["job"] for e in ranked} or {j.id: j.to_dict() for j in ctx.load_jobs()}
        if job_id not in jobs:
            return {"error": f"job id '{job_id}' not found in ranked/cache"}
        job = Job.from_dict(jobs[job_id])
        ctx.settings.ensure_home()
        paths = gen.generate_application(ctx.profile, job, ctx.settings.applications_dir, llm=ctx.llm)
        return {
            "job": f"{job.title} @ {job.company}",
            "tailored": paths["tailored"],
            "folder": paths["dir"],
            "files": [k for k in paths if k not in ("dir", "tailored")],
        }

    def answer_application_question(question: str) -> dict:
        ans = profile_store.answer_question(ctx.profile, question, llm=ctx.llm)
        return {"question": question, "answer": ans}

    def get_profile_summary() -> dict:
        return {"summary": ctx.profile.to_text_block(3000)}

    _keywords_schema = {
        "type": "array", "items": {"type": "string"},
        "description": "Search keywords, e.g. ['python', 'backend']",
    }

    reg.register(Tool(
        "search_jobs",
        "Search job boards for postings. Caches results. Returns a count and sample titles.",
        {
            "type": "object",
            "properties": {
                "keywords": _keywords_schema,
                "location": {"type": "string", "description": "Target location, or '' for any"},
                "remote": {"type": "boolean", "description": "Prefer remote roles"},
                "limit": {"type": "integer", "description": "Max jobs to return (default 30)"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": f"Subset of: {', '.join(scraper.SOURCES)}"},
            },
            "required": ["keywords"],
        },
        search_jobs,
    ))
    reg.register(Tool(
        "rank_jobs",
        "Score the cached jobs against the user's profile and store the ranking. "
        "Set use_llm=true to LLM-rerank the top jobs (uses the API; slower).",
        {
            "type": "object",
            "properties": {
                "use_llm": {"type": "boolean", "description": "LLM-rerank the top jobs"},
                "top": {"type": "integer", "description": "How many ranked jobs to summarize back"},
            },
        },
        rank_jobs,
    ))
    reg.register(Tool(
        "list_ranked",
        "List the currently ranked jobs (rank, score, id, title, company).",
        {"type": "object", "properties": {"top": {"type": "integer"}}},
        list_ranked,
    ))
    reg.register(Tool(
        "generate_application",
        "Generate a tailored resume, cover letter, and pre-filled application packet for one job id.",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
        generate_application,
    ))
    reg.register(Tool(
        "answer_application_question",
        "Answer an arbitrary application question using only the stored profile data.",
        {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
        answer_application_question,
    ))
    reg.register(Tool(
        "get_profile_summary",
        "Return a text digest of the user's profile.",
        {"type": "object", "properties": {}},
        get_profile_summary,
    ))
    return reg
