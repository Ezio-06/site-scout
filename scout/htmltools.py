"""Small shared HTML helpers. Parsing is the expensive part, so callers should
parse once via ScoutContext.soup() and reuse the result."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:  # works without lxml, just slower
    PARSER = "html.parser"

_WS = re.compile(r"\s+")
_NON_VISIBLE = ("script", "style", "noscript", "template", "svg", "iframe", "head")


def parse(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, PARSER)


def visible_text(soup: BeautifulSoup) -> str:
    """Text a human would see. Works on a copy-free pass: skips non-visible tags."""
    parts = []
    for s in soup.find_all(string=True):
        parent = s.parent
        if parent is None or parent.name in _NON_VISIBLE:
            continue
        if any(p.name in _NON_VISIBLE for p in parent.parents if p.name):
            continue
        t = s.strip()
        if t:
            parts.append(t)
    return _WS.sub(" ", " ".join(parts)).strip()


def norm(text: str) -> str:
    return _WS.sub(" ", text or "").strip().lower()


def contains(haystack: str, needle: str) -> bool:
    return norm(needle) in norm(haystack)


# a thousands separator between digit groups: 24,990 / 24 990 / 24.990 / 1'045 / 2,49,990 (lakh style)
_DIGIT_SEP = re.compile(r"(?<=\d)(?:[,'\u2019\u00a0\u202f ](?=\d{2,3}(?!\d))|\.(?=\d{3}(?!\d)))")   # '.' only as 24.990, never 24.99


def norm_num(text: str) -> str:
    """norm() with thousands separators dropped, so '24,990' typed from the screen
    matches "price":"24990" in the page data (28 Oct, Daraz product pages)."""
    return _DIGIT_SEP.sub("", norm(text))


def has_text(norm_hay: str, num_hay: str, needle: str) -> bool:
    """Is `needle` in a haystack given as norm() and norm_num() versions?
    Numbers are compared without separators; words exactly as before."""
    n = norm(needle)
    if n in norm_hay:
        return True
    return any(c.isdigit() for c in n) and norm_num(needle) in num_hay
