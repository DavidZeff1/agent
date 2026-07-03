"""Thin Groq wrapper: chat, tool-calling, and JSON completion.

Design notes
------------
* Groq exposes an OpenAI-compatible Chat Completions API, so tool schemas use the
  standard ``{"type": "function", "function": {...}}`` shape and tool calls come back
  on ``message.tool_calls``.
* The Groq free tier is rate-limited (requests/min and tokens/min), so every call goes
  through a minimum-interval throttle plus an exponential-backoff retry loop that honors
  a ``Retry-After`` header when the server sends one.
* ``groq`` is imported lazily so the rest of the package (profiles, scraping, deterministic
  matching) works with no API key and without the dependency installed.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Optional

from .config import get_settings


class LLMError(RuntimeError):
    pass


# One rate-limit shared by every GroqLLM instance in the process, so parallel workers
# (e.g. preparing several applications at once) still space their calls politely.
_THROTTLE_LOCK = threading.Lock()
_NEXT_SLOT = [0.0]


class GroqLLM:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        min_interval: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        s = get_settings()
        self.api_key = (api_key or s.api_key or "").strip()
        self.model = model or s.model
        self.min_interval = s.min_request_interval if min_interval is None else min_interval
        self.max_retries = s.max_retries if max_retries is None else max_retries
        self._client = None
        self._last_call = 0.0
        if not self.api_key:
            raise LLMError(
                "GROQ_API_KEY is not set. Add it to your environment or a .env file "
                "(get a free key at https://console.groq.com)."
            )

    @property
    def client(self):
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as e:  # pragma: no cover - depends on install
                raise LLMError("The 'groq' package is not installed. Run: pip install groq") from e
            self._client = Groq(api_key=self.api_key)
        return self._client

    # -- internals ---------------------------------------------------------
    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        with _THROTTLE_LOCK:  # reserve the next slot, then sleep outside the lock
            now = time.monotonic()
            slot = max(now, _NEXT_SLOT[0])
            _NEXT_SLOT[0] = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)

    def _retry_delay(self, err: Exception, attempt: int) -> float:
        try:
            resp = getattr(err, "response", None)
            if resp is not None:
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    return min(float(retry_after) + 0.5, 30.0)
        except Exception:
            pass
        return min(2.0 * (2 ** attempt), 30.0)

    def _create(self, **kwargs):
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001 - normalize provider errors
                last_err = e
                status = getattr(e, "status_code", None)
                msg = str(e).lower()
                retryable = (
                    status in (429, 500, 502, 503, 529)
                    or "rate limit" in msg
                    or "overloaded" in msg
                    or "timeout" in msg
                    or "temporarily" in msg
                )
                if attempt >= self.max_retries or not retryable:
                    break
                time.sleep(self._retry_delay(e, attempt))
        raise LLMError(f"Groq request failed after {self.max_retries + 1} attempt(s): {last_err}")

    # -- public API --------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ):
        """Return the raw assistant ``message`` (has ``.content`` and ``.tool_calls``)."""
        kwargs: dict[str, Any] = dict(model=self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format
        return self._create(**kwargs).choices[0].message

    def complete(self, system: str, user: str, **kwargs) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return (self.chat(messages, **kwargs).content or "").strip()

    def complete_json(
        self, system: str, user: str, temperature: float = 0.2, max_tokens: int = 2048
    ) -> dict:
        """Return a parsed JSON object. Uses Groq's JSON mode with a regex fallback."""
        messages = [
            {"role": "system", "content": system + "\n\nRespond with a single valid JSON object and nothing else."},
            {"role": "user", "content": user},
        ]
        try:
            text = (
                self.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                ).content
                or ""
            ).strip()
        except LLMError as e:
            # Groq's JSON mode rejects output containing unescaped quotes — which Hebrew
            # produces naturally (ש"ח, בע"מ). Retry once without JSON mode and repair.
            if "json_validate_failed" not in str(e):
                raise
            text = (self.chat(messages, temperature=temperature, max_tokens=max_tokens).content or "").strip()
        return parse_json(text)


def parse_json(text: str) -> dict:
    candidates = [text]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    # Escape ASCII quotes sandwiched between Hebrew letters (ש"ח, בע"מ) — the one way
    # models routinely produce invalid JSON in Hebrew.
    candidates += [re.sub(r'(?<=[֐-׿])"(?=[֐-׿])', '\\\\"', c) for c in list(candidates)]
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:  # noqa: BLE001
            continue
    raise LLMError("Model did not return valid JSON.")


def get_llm(optional: bool = False) -> Optional[GroqLLM]:
    """Build a :class:`GroqLLM`. With ``optional=True`` return ``None`` instead of raising."""
    try:
        return GroqLLM()
    except LLMError:
        if optional:
            return None
        raise


def llm_available() -> bool:
    return bool(get_settings().api_key)
