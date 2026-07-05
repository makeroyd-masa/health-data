"""HTML cleaning, section splitting, and deterministic summary generation (PRD §3, §2).

Adapters never emit boilerplate. This module converts HTML to clean markdown, strips
site chrome, splits content into sections by h2/h3 headings, and derives the short
plain-language `summary` without any LLM call.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as _md

# Tags that are never content.
_CHROME_TAGS = [
    "script", "style", "noscript", "nav", "header", "footer", "aside",
    "form", "button", "svg", "iframe", "template", "img", "figure", "picture",
]
# Substrings in class/id/role that mark chrome to drop.
_CHROME_HINTS = (
    "nav", "menu", "breadcrumb", "cookie", "banner", "skip-link", "skip-to",
    "related", "sidebar", "social", "share", "footer", "header", "search",
    "subscribe", "newsletter", "advert",
)
_HEADING_LEVELS = ("h2", "h3")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIST_LINE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def clean_soup(soup: BeautifulSoup) -> None:
    """Strip obvious site chrome in place."""
    for tag in soup.find_all(_CHROME_TAGS):
        tag.decompose()
    for el in soup.find_all(True):
        # Decomposing a parent detaches its descendants, which remain in this list with
        # attrs=None — skip anything already removed.
        if el.decomposed or el.attrs is None:
            continue
        role = (el.get("role") or "").lower()
        if role in {"navigation", "banner", "contentinfo", "complementary", "search"}:
            el.decompose()
            continue
        hint_text = " ".join(
            [role, " ".join(el.get("class") or []), el.get("id") or ""]
        ).lower()
        if any(h in hint_text for h in _CHROME_HINTS):
            el.decompose()


def html_to_markdown(html: str | Tag) -> str:
    """Convert an HTML fragment to clean markdown."""
    md = _md(str(html), heading_style="ATX", bullets="-")
    return _tidy(md)


def _tidy(md: str) -> str:
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = "\n".join(line.rstrip() for line in md.splitlines())
    return md.strip()


def split_by_headings(
    container: Tag, levels: tuple[str, ...] = _HEADING_LEVELS
) -> list[tuple[str, str]]:
    """Split a content container into (section_title, body_markdown) by h2/h3.

    Content before the first heading becomes an untitled ("") lead section. If no
    headings are present, returns a single ("", body) — the PRD's "Overview block" norm.
    """
    depths = {int(lvl[1:]) for lvl in levels}  # ("h2","h3") -> {2, 3}
    md = html_to_markdown(container)

    sections: list[tuple[str, str]] = []
    current_title = ""
    current: list[str] = []

    def flush():
        body = "\n".join(current).strip()
        if body:
            sections.append((current_title, body))

    for line in md.split("\n"):
        m = _MD_HEADING.match(line)
        if m and len(m.group(1)) in depths:
            flush()
            current_title = m.group(2).strip()
            current = []
        else:
            current.append(line)
    flush()

    if not sections:
        body = md.strip()
        return [("", body)] if body else []
    return sections


def make_summary(body_markdown: str, section_title: str = "", limit: int = 300) -> str:
    """Deterministic gist: first sentence(s) up to `limit` chars (PRD §2).

    For list-only sections, use the title + item count instead of a truncated fragment.
    """
    lines = [ln for ln in body_markdown.splitlines() if ln.strip()]
    list_lines = [ln for ln in lines if _LIST_LINE.match(ln)]
    prose_lines = [ln for ln in lines if not _LIST_LINE.match(ln) and not ln.startswith("#")]

    # List-mode summary only for a bare list (no prose to summarize from).
    if list_lines and not prose_lines:
        label = section_title.strip() or "Checklist"
        return f"{label}: {len(list_lines)} items."[:limit]

    text = " ".join(prose_lines) if prose_lines else " ".join(lines)
    text = re.sub(r"[#*_>`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return (section_title.strip() or "Content")[:limit]

    out = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        candidate = (out + " " + sentence).strip() if out else sentence
        if len(candidate) > limit:
            break
        out = candidate
    if not out:  # first sentence already exceeds the limit
        out = text[:limit].rsplit(" ", 1)[0]
    return out.strip()
