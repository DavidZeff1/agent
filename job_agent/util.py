"""Small dependency-free helpers: HTML->text, slugify, JSON IO, truncation."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser

_BLOCK_TAGS = {"br", "p", "li", "div", "tr", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "section"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")


def html_to_text(s: str | None) -> str:
    """Convert an HTML fragment (job descriptions arrive as HTML) to readable plain text."""
    if not s:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(s)
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", s)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def slugify(s: str | None, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return (s or "item")[:maxlen].strip("-") or "item"


def truncate(s: str | None, n: int = 1500) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip() + " …"


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj) -> str:
    path = str(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return path


def write_text(path, text: str) -> str:
    path = str(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
