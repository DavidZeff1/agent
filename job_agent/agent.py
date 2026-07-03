"""A minimal ReAct-style agent driven by Groq tool-calling.

The model is given the registry's tool schemas and decides which to call. We execute each call
via :meth:`ToolRegistry.dispatch`, feed the JSON result back as a ``role: "tool"`` message, and
loop until the model returns a plain text answer (or we hit ``max_steps``).

This is the "agent decides" counterpart to the deterministic CLI pipeline; both share the exact
same tools.
"""
from __future__ import annotations

import json
from typing import List

from .tools import Context, ToolRegistry, build_registry

SYSTEM_PROMPT = (
    "You are a job-application assistant. You help the user find suitable jobs and prepare "
    "tailored applications, using the provided tools. Typical flow: search_jobs -> rank_jobs -> "
    "generate_application for the best matches. Only generate applications the user asked for. "
    "Take one sensible step at a time, call tools with concrete arguments, and when done, reply "
    "with a short plain-text summary of what you did and where the files were written. Never "
    "invent job details or profile facts."
)


class Agent:
    def __init__(self, ctx: Context, registry: ToolRegistry | None = None, max_steps: int = 8) -> None:
        if ctx.llm is None:
            raise RuntimeError("The agent requires a Groq API key (set GROQ_API_KEY).")
        self.ctx = ctx
        self.registry = registry or build_registry(ctx)
        self.max_steps = max_steps

    def run(self, user_message: str, verbose: bool = True) -> str:
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        tools = self.registry.schemas()

        for _ in range(self.max_steps):
            msg = self.ctx.llm.chat(messages, tools=tools, tool_choice="auto", temperature=0.2)
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                return (msg.content or "").strip()

            # Record the assistant turn (with its tool calls) before appending results.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                name = tc.function.name
                args = tc.function.arguments
                if verbose:
                    print(f"  → tool: {name}({args})")
                result = self.registry.dispatch(name, args)
                if verbose:
                    print(f"    {_preview(result)}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": result,
                })

        return "Reached the step limit without a final answer. Partial progress is saved; try a more specific request."


def _preview(s: str, n: int = 200) -> str:
    try:
        obj = json.loads(s)
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass
    return s if len(s) <= n else s[:n] + " …"
