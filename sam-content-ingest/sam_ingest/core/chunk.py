"""HTML cleaning, section splitting, and deterministic summary generation (PRD §3, §2).

Adapters never emit boilerplate. This module converts HTML to clean markdown, strips
site chrome, splits content into sections by h2/h3 headings, and derives the short
plain-language `summary` without any LLM call.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag
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


def clean_soup(soup: BeautifulSoup) -> None:
    """Strip obvious site chrome in place."""
    for tag in soup.find_all(_CHROME_TAGS):
        tag.decompose()
    for el in soup.find_all(True):
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


def _content_children(container: Tag) -> list[Tag]:
    """Return block-level children, descending through single-child wrappers.

    Handles parser-inserted html/body wrappers and CMS wrapper divs: descend while the
    node has exactly one element child and this level contains no split headings yet.
    """
    node = container
    while True:
        elem_children = [c for c in node.children if isinstance(c, Tag)]
        if any(c.name in _HEADING_LEVELS for c in elem_children):
            break
        if len(elem_children) == 1:
            node = elem_children[0]
            continue
        break
    return [c for c in node.children if isinstance(c, Tag) or
            (isinstance(c, NavigableString) and c.strip())]


def split_by_headings(
    container: Tag, levels: tuple[str, ...] = _HEADING_LEVELS
) -> list[tuple[str, str]]:
    """Split a content container into (section_title, body_markdown) by h2/h3.

    Content before the first heading becomes an untitled ("") lead section. If no
    headings are present, returns a single ("", body) — the PRD's "Overview block" norm.
    """
    sections: list[tuple[str, list]] = []
    current_title = ""
    current_nodes: list = []

    def flush():
        if current_nodes:
            frag = "".join(str(n) for n in current_nodes)
            body = html_to_markdown(frag)
            if body.strip():
                sections.append((current_title, body))

    for child in _content_children(container):
        if isinstance(child, Tag) and child.name in levels:
            flush()
            current_title = child.get_text(" ", strip=True)
            current_nodes = []
        else:
            current_nodes.append(child)
    flush()

    # Collapse to a single untitled section if nothing split out.
    if not sections:
        body = html_to_markdown(container)
        return [("", body)] if body.strip() else []
    return [(t, b) for t, b in sections]


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
