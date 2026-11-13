"""Passive guard recognition from a signature file (derived from wafw00f's public plugins).

Only looks at a response the scout ALREADY has - it never sends a request or a trigger payload.
Each guard = OR of rule groups; each group = AND of conditions:
    {"t": "header", "name": ..., "re": ...}   a response header (cookies = the Set-Cookie header)
    {"t": "body", "re": ...}                  text of the page
    {"t": "status", "code": 405}              the HTTP status
A group with a header/status condition that matches means the guard is PRESENT. A body-only group
counts as present only when the page is thin (same rule as challenge words: a rich page that merely
mentions a vendor is not guarded). On a rich page it is only "possible".
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from importlib import resources

from . import signals

log = logging.getLogger("scout")

BODY_LIMIT = 200_000

# signature-file vendor name -> the name the hand-written tables already use (so a vendor is reported once)
ALIASES = {
    "cloudfront": "Amazon CloudFront",
    "incapsula": "Imperva/Incapsula",
    "perimeterx": "PerimeterX/HUMAN",
    "sucuri cloudproxy": "Sucuri",
    "kona sitedefender": "Akamai",
}
_COOKIE_SPLIT = re.compile(r",\s*(?=[^\s;,=]+=)")


def display_name(raw: str) -> str:
    """'Cloudflare (Cloudflare Inc.)' -> 'Cloudflare'; known aliases map to the hand-table label."""
    base = raw.split(" (")[0].strip() or raw
    return ALIASES.get(base.lower(), base)


@lru_cache(maxsize=1)
def load_signatures() -> tuple:
    """-> (rules, skipped). rules = list of (guard name, kind, conditions); kind is 'strong' or 'body'.
    A group with a bad regex or a malformed condition is skipped and counted, never fatal."""
    rules, skipped = [], 0
    try:
        text = (resources.files("scout") / "data" / "guard_signatures.json").read_text(encoding="utf-8")
        guards = (json.loads(text) or {}).get("guards") or {}
    except Exception as e:   # missing/corrupt file = no matching, not a crash
        log.warning("guard signatures could not be loaded (%s: %s)", type(e).__name__, e)
        return [], 0
    for key, g in guards.items():
        try:
            name = display_name(g.get("name") or key)
            groups = g.get("rules") or []
        except Exception:
            skipped += 1
            continue
        for group in groups:
            try:
                conds = [_compile(c) for c in group]
                if not conds:
                    raise ValueError("empty group")
            except Exception as e:
                skipped += 1
                log.debug("skipped signature rule of %s: %s: %s", name, type(e).__name__, e)
                continue
            kind = "body" if all(c[0] == "body" for c in conds) else "strong"
            rules.append((name, kind, conds))
    log.debug("guard signatures: %d rules loaded, %d skipped", len(rules), skipped)
    return rules, skipped


def _compile(c: dict) -> tuple:
    t = c["t"]
    if t == "header":
        return ("header", c["name"].lower(), re.compile(c["re"], re.I))
    if t == "body":
        return ("body", None, re.compile(c["re"], re.I))
    if t == "status":
        if "code" in c:
            return ("status", None, int(c["code"]))
        return ("status", None, re.compile(c["re"]))
    raise ValueError(f"unknown condition type {t!r}")


def skipped_rules() -> int:
    return load_signatures()[1]


def _cond_matches(cond: tuple, status: int, headers: dict, body: str) -> bool:
    kind, name, pat = cond
    if kind == "body":
        return bool(pat.search(body))
    if kind == "status":
        return status == pat if isinstance(pat, int) else bool(pat.fullmatch(str(status)))
    value = headers.get(name)
    if value is None:
        return False
    if pat.search(value):
        return True
    if name == "set-cookie":   # several cookies are joined into one string; try them one by one
        return any(pat.search(part) for part in _COOKIE_SPLIT.split(value))
    return False


def match_signatures(status: int, headers: dict, text: str, thin: bool | None = None) -> tuple:
    """-> (present, possible): sorted guard names. Possible = only a body-only rule matched on a rich page."""
    rules, _ = load_signatures()
    lower = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    body = (text or "")[:BODY_LIMIT]
    if thin is None:
        thin = signals.is_thin(text or "")
    present, possible = set(), set()
    for name, kind, conds in rules:
        if name in present:
            continue
        if all(_cond_matches(c, status, lower, body) for c in conds):
            (present if kind == "strong" or thin else possible).add(name)
    return sorted(present), sorted(possible - present)


def detect(status: int, headers: dict, cookie_names, text: str) -> tuple:
    """Hand tables + signature file merged. -> (present, possible), each name once."""
    thin = signals.is_thin(text or "")
    sig_present, sig_possible = match_signatures(status, headers, text, thin)
    found = signals.guards_from_headers(headers) | signals.guards_from_cookies(cookie_names)
    merged: dict = {}
    for n in list(found) + sig_present:
        merged.setdefault(n.lower(), n)
    possible = {}
    for n in sig_possible:
        if n.lower() not in merged:
            possible.setdefault(n.lower(), n)
    return sorted(merged.values()), sorted(possible.values())
