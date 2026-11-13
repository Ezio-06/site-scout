"""How does page N's address look? Worked out by comparing page 1's address
with page 2's (taken from the site's own 'next' link or page links).

Handles the common shapes:
  ?page=2   ?p=2   ?start=25 / ?offset=24 (offset style)   /page/2/   -2.html   _p2
and the case where page 1 has no number at all (/homes  ->  /homes?page=2).
Pure functions, no network."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_NUM = re.compile(r"\d+")


def _split(url: str):
    """URL -> (text pieces, numbers as ints, numbers as written).
    The written form is kept so '01' stays '01' when the address is rebuilt
    (e.g. 'region-01?page=2' was once rebuilt as 'region-1?page=2' -> false 'paging broken')."""
    raw = _NUM.findall(url)
    return _NUM.split(url), [int(x) for x in raw], raw


def template_from(url1: str, url2: str, per_page: int | None = None) -> dict | None:
    """Return {'kind': page|offset, 'first': n1, 'step': s, 'pattern': str, 'page1_url': url1}
    or None if the two addresses are not 'the same page with a different number'."""
    if not url1 or not url2 or url1 == url2:
        return None
    u1, u2 = _normalise(url1), _normalise(url2)

    # case A: same shape, exactly one number differs  (Lahore-1-1.html -> Lahore-1-2.html)
    t1, n1, r1 = _split(u1)
    t2, n2, r2 = _split(u2)
    if t1 == t2 and len(n1) == len(n2):
        diff = [i for i in range(len(n1)) if n1[i] != n2[i]]
        if len(diff) == 1:
            i = diff[0]
            return _checked(_make(t2, r2, i, n1[i], n2[i], url1, per_page), u2)

    # case B: page 2 adds a number page 1 doesn't have  (/homes -> /homes?page=2, /homes/page/2)
    if len(n2) == len(n1) + 1:
        for i in range(len(n2)):
            rest = n2[:i] + n2[i + 1:]
            if rest != n1:
                continue
            first = 1 if n2[i] in (1, 2) else 0
            if per_page and n2[i] == per_page:
                first = 0
            return _checked(_make(t2, r2, i, first, n2[i], url1, per_page), u2)
    return None


def _checked(tpl, u2):
    """Safety net: the template must rebuild the site's own page-2 address exactly.
    If it can't, say 'no template' (an honest unknown) rather than test the wrong page."""
    if tpl is None:
        return None
    return tpl if url_for(tpl, 2) == u2 else None


def _make(pieces, raw, i, v1, v2, url1, per_page):
    step = v2 - v1
    if step <= 0:
        return None
    kind = "page" if step == 1 else "offset"
    if kind == "offset" and per_page and abs(step - per_page) > max(2, per_page * 0.2):
        kind = "offset"   # step differs from items shown - still offset, the step is what the site uses
    out = []
    for j, piece in enumerate(pieces):
        out.append(piece)
        if j < len(raw):
            out.append("{n}" if j == i else raw[j])
    return {"kind": kind, "first": v1, "step": step, "pattern": "".join(out), "page1_url": url1}


def url_for(tpl: dict, page: int) -> str:
    """Address of page `page` (1-based)."""
    if page == 1:
        return tpl["page1_url"]
    value = tpl["first"] + (page - 1) * tpl["step"]
    return tpl["pattern"].replace("{n}", str(value))


def _normalise(url: str) -> str:
    """Sort query parameters so ?a=1&page=2 and ?page=2&a=1 line up."""
    p = urlsplit(url)
    q = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    return urlunsplit((p.scheme, p.netloc, p.path, q, ""))


_N = r"(\d{1,3}(?:[ ,.\u00a0\u202f]\d{3})+|\d+)"   # 28,367 / 28 367 / 28367
RANGE = re.compile(_N + r"\s*(?:-|\u2013|to|\u0434\u043e|bis|\u00e0|a)\s*" + _N +
                   r"\s+(?:of|\u0438\u0437|von|de|sur)\s+" + _N, re.I)


def range_text(text: str):
    """'10,001 to 10,025 of 28,367' -> (10001, 10025, 28367) or None."""
    m = RANGE.search(text or "")
    if not m:
        return None
    try:
        a, b, c = (int(re.sub(r"[^\d]", "", g)) for g in m.groups())
    except ValueError:
        return None
    if a > b or b > c * 1.01 + 1:
        return None
    return a, b, c
