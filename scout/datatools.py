"""Reading data that sites pack into pages and feeds - shared by several checks.

- state_blobs():  every JavaScript data blob in a page (window.state = {...},
                  __NEXT_DATA__, JSON.parse("..."), Apollo/Redux/Nuxt state...)
- totals():       "how many results exist" numbers inside a blob (nbHits,
                  hits.total, numFound, totalCount...)
- backend():      which search engine answers the site's searches (Algolia,
                  Elasticsearch, Solr...) - each has a known deep-paging limit
- item_ids():     identity of each item, used to compare page 1 vs page 2

Pure functions, no network. Extend the tables to teach the scout new patterns.
"""
from __future__ import annotations

import json
import re
from collections import deque

try:
    import chompjs  # parses JavaScript object literals (single quotes, undefined, trailing commas)
except ImportError:  # pragma: no cover - falls back to strict JSON
    chompjs = None

# ------------------------------------------------------------------ state blobs
# "<name> = {"  /  "<name> = ["  at the start of a statement. Catches window.state,
# window.__INITIAL_STATE__, window["x"], var/let/const x, self.__x, globalThis.x ...
ASSIGN = re.compile(
    r"""(?:^|[;\s(])((?:window|self|globalThis)(?:\.[\w$]+|\[["'][\w$.-]+["']\])+|(?:var|let|const)\s+[\w$]+)"""
    r"""\s*=\s*(?=[{\[]|JSON\.parse\()""")
JSON_PARSE = re.compile(r"""JSON\.parse\(\s*(["'])""")
SCRIPT_JSON_IDS = ("__NEXT_DATA__", "__NUXT_DATA__", "serverApp-state", "ng-state",
                   "__APOLLO_STATE__", "__remixContext")
MIN_BLOB = 400            # smaller blobs are config, not data
MAX_BLOB = 15_000_000

# frameworks whose data can't be read as one blob - name them so the card says so
UNREADABLE = [
    (re.compile(r"self\.__next_f\.push"), "Next.js app-router stream (data split into many small pushes)"),
    (re.compile(r"window\.__NUXT__\s*=\s*\(function"), "Nuxt 2 state built by a function (needs a browser or a JS engine)"),
]


def _parse_js(text: str):
    """Parse the first JS/JSON value at the start of `text`. Returns (obj, length) or (None, 0)."""
    text = text.lstrip()
    try:
        obj, end = json.JSONDecoder().raw_decode(text)
        return obj, end
    except Exception:
        pass
    if chompjs is not None:
        try:
            end = _balanced_end(text)
            if end:
                return chompjs.parse_js_object(text[:end]), end
        except Exception:
            pass
    return None, 0


def _balanced_end(text: str) -> int:
    """Index just past the bracket that closes text[0]; respects strings."""
    if not text or text[0] not in "{[":
        return 0
    depth, i, n, quote = 0, 0, min(len(text), MAX_BLOB), None
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return 0


def state_blobs(soup) -> tuple[list, list]:
    """Return ([(name, obj, size)], [unreadable framework labels]) for one parsed page."""
    found, unreadable = [], []
    for tag in soup.find_all("script"):
        if tag.get("src"):
            continue
        raw = tag.string or tag.get_text() or ""
        if len(raw) < MIN_BLOB:
            continue
        stype = (tag.get("type") or "").lower()
        sid = tag.get("id") or ""
        if stype == "application/ld+json":
            continue   # handled separately (search-engine data)
        if stype == "application/json" or sid in SCRIPT_JSON_IDS:
            obj, _ = _parse_js(raw)
            if obj is not None:
                found.append((sid or "JSON script block", obj, len(raw)))
            continue
        for rx, label in UNREADABLE:
            if rx.search(raw[:2000]) and label not in unreadable:
                unreadable.append(label)
        for m in ASSIGN.finditer(raw):
            name = re.sub(r"^(var|let|const)\s+", "", m.group(1))
            rest = raw[m.end():]
            if rest.startswith("JSON.parse("):
                obj = _json_parse_call(rest)
                size = len(rest)
            else:
                obj, size = _parse_js(rest)
            if obj is not None and size >= MIN_BLOB and isinstance(obj, (dict, list)):
                found.append((name, obj, size))
        if "JSON.parse(" in raw and not any(True for _ in ASSIGN.finditer(raw)):
            m = JSON_PARSE.search(raw)
            if m:
                obj = _json_parse_call(raw[m.start():])
                if isinstance(obj, (dict, list)):
                    found.append(("JSON.parse(...)", obj, len(raw)))
    return found, unreadable


def _json_parse_call(text: str):
    """JSON.parse("...escaped json...") -> object."""
    m = JSON_PARSE.match(text.lstrip())
    if not m:
        return None
    q = m.group(1)
    body = text.lstrip()[m.end():]
    i, out = 0, []
    while i < len(body) and i < MAX_BLOB:
        ch = body[i]
        if ch == "\\":
            out.append(body[i:i + 2])
            i += 2
            continue
        if ch == q:
            break
        out.append(ch)
        i += 1
    lit = "".join(out)
    try:
        if q == "'":   # JS single-quoted string -> JSON double-quoted string
            lit = re.sub(r'(?<!\\)"', '\\"', lit.replace("\\'", "'"))
        return json.loads(json.loads('"' + lit + '"'))
    except Exception:
        return None


# ------------------------------------------------------------------ totals
# key -> how sure we are it means "total matching results"
TOTAL_KEYS = {
    "nbhits": "Algolia", "nbpages": None, "numfound": "Solr", "estimatedtotalhits": "Meilisearch",
    "totalhits": None, "totalcount": None, "total_count": None, "totalresults": None,
    "total_results": None, "totalitems": None, "total_items": None, "totalelements": None,
    "recordstotal": None, "found": "Typesense", "total": None, "count": None,
    "totalproducts": None, "total_products": None, "resultcount": None, "hitcount": None,
}
WEAK_KEYS = {"count", "total", "found"}   # common words - only trusted next to an item list
MAX_WALK = 60_000


def totals(obj, limit: int = 8) -> list:
    """Find 'how many results' numbers. Returns [{path, key, value, backend}] best first."""
    out = []
    q = deque([("$", obj, None)])
    seen = 0
    while q and seen < MAX_WALK:
        path, node, parent = q.popleft()
        seen += 1
        if isinstance(node, dict):
            has_list = any(isinstance(v, list) and len(v) >= 3 for v in node.values())
            for k, v in node.items():
                kl = str(k).lower()
                val = v
                # Elasticsearch: hits.total = {"value": n, "relation": "eq"}
                if kl == "total" and isinstance(v, dict) and isinstance(v.get("value"), int):
                    val = v["value"]
                    out.append({"path": f"{path}.{k}.value", "key": "hits.total", "value": val,
                                "backend": "Elasticsearch/OpenSearch", "strong": True,
                                "relation": v.get("relation")})
                elif kl in TOTAL_KEYS and isinstance(val, int) and not isinstance(val, bool) and val > 0:
                    if kl == "nbpages":
                        continue
                    strong = kl not in WEAK_KEYS or has_list or kl == "found" and "hits" in node
                    if strong:
                        out.append({"path": f"{path}.{k}", "key": k, "value": val,
                                    "backend": TOTAL_KEYS[kl], "strong": kl not in WEAK_KEYS})
                if isinstance(v, (dict, list)):
                    q.append((f"{path}.{k}", v, node))
        elif isinstance(node, list):
            for i, v in enumerate(node[:3]):
                if isinstance(v, (dict, list)):
                    q.append((f"{path}[{i}]", v, node))
    out.sort(key=lambda t: (not t["strong"], -t["value"]))
    return out[:limit]


def paging_info(obj) -> dict:
    """Algolia/ES-style paging fields if present: page, hitsPerPage, nbPages."""
    want = {"page": None, "hitsperpage": None, "nbpages": None, "per_page": None,
            "pagesize": None, "page_size": None, "currentpage": None, "totalpages": None}
    q = deque([obj])
    seen = 0
    while q and seen < MAX_WALK:
        node = q.popleft()
        seen += 1
        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).lower()
                if kl in want and want[kl] is None and isinstance(v, int) and not isinstance(v, bool):
                    want[kl] = v
                if isinstance(v, (dict, list)):
                    q.append(v)
        elif isinstance(node, list):
            q.extend(x for x in node[:3] if isinstance(x, (dict, list)))
    return {k: v for k, v in want.items() if v is not None}


# ------------------------------------------------------------------ search backends
# Known deep-paging limits. ASSUMED until the reach check tests them.
KNOWN_LIMITS = {
    "Elasticsearch/OpenSearch": "default limit: only the first 10,000 results can be paged to "
                                "(max_result_window) - sites often raise or lower it",
    "Algolia": "default limit: only the first 1,000 results can be paged to (paginationLimitedTo)",
    "Solr": "deep pages get slow and may time out; cursorMark is the sanctioned way",
    "Typesense": "default limit: 250 per page, deep paging allowed but slow",
    "Meilisearch": "default limit: only the first 1,000 results (maxTotalHits)",
    "GraphQL": "usually cursor paging (after=...) - page numbers may not exist",
}

BACKEND_URL = [
    (re.compile(r"/_msearch|/_search\b|/_count\b", re.I), "Elasticsearch/OpenSearch"),
    (re.compile(r"algolia(net)?\.(net|io|com)|/1/indexes/", re.I), "Algolia"),
    (re.compile(r"typesense|/multi_search\b|/collections/[^/]+/documents/search", re.I), "Typesense"),
    (re.compile(r"meilisearch|/indexes/[^/]+/search\b", re.I), "Meilisearch"),
    (re.compile(r"/solr/|/select\?", re.I), "Solr"),
    (re.compile(r"graphql", re.I), "GraphQL"),
]


def backend(url: str = "", body_sent: str | None = None, answer=None, headers: dict | None = None) -> str | None:
    """Best guess at the search engine behind a request. Order matters: an
    Elasticsearch body also contains "query", so URL evidence wins over GraphQL."""
    for rx, label in BACKEND_URL:
        if rx.search(url or ""):
            return label
    hdrs = {k.lower() for k in (headers or {})}
    if any(h.startswith("x-algolia-") for h in hdrs):
        return "Algolia"
    if isinstance(answer, (dict, list)):
        t = totals(answer, limit=3)
        for x in t:
            if x["backend"]:
                return x["backend"]
    if body_sent:
        s = body_sent.lstrip()[:2000]
        # GraphQL bodies carry a query *string* that starts with { / query / mutation
        if re.search(r'"query"\s*:\s*"\s*(query|mutation|\{)', s) or re.search(r'"operationName"', s):
            return "GraphQL"
        if re.search(r'"query"\s*:\s*\{', s) and re.search(r'"(bool|match|term|terms|range)"', s):
            return "Elasticsearch/OpenSearch"
    return None


# ------------------------------------------------------------------ item identity
ID_KEYS = ("externalID", "objectID", "id", "sku", "sku_id", "skuId", "itemId", "item_id",
           "productId", "product_id", "listingId", "listing_id", "uuid", "slug", "url", "href", "link")


def item_ids(items) -> list:
    """One identity string per item (first ID-like field present)."""
    ids = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        src = it.get("_source") if isinstance(it.get("_source"), dict) else it
        for k in ID_KEYS:
            v = src.get(k)
            if isinstance(v, (str, int)) and str(v).strip():
                ids.append(f"{k}={v}")
                break
    return ids


def get_path(obj, path: str):
    """Follow a '$.a.b[0].c' path produced by jsontools/totals."""
    node = obj
    for part in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", path):
        key, idx = part
        try:
            node = node[int(idx)] if idx else node[key]
        except Exception:
            return None
    return node


TELEMETRY = re.compile(
    r"/(ingest|collect|track(ing)?|events?|beacon|telemetry|metrics?|analytics|log(s|ging)?|"
    r"pixel|impressions?|stats?|rum|vitals|heartbeat|ping)(/|\b|\?|$)|"
    r"adMetric|storyMetric|pageview", re.I)
TELEMETRY_KEYS = {"client_ip", "session_id", "client_session_id", "client_device_id", "event",
                  "event_name", "eventname", "metric", "metric_action", "timestamp", "trace_id",
                  "user_agent", "pageview"}


def looks_like_telemetry(url: str, method: str, status: int, sample_keys: list) -> bool:
    """Requests where the page *reports* to the site (analytics), not fetches data."""
    if TELEMETRY.search(url or ""):
        return True
    if method == "POST" and status in (201, 202, 204):
        return True
    keys = {k.lower() for k in sample_keys or []}
    return len(keys & TELEMETRY_KEYS) >= 3


# ------------------------------------------------------------------ evidence: is this list what the page shows?
def item_values(items, limit_items: int = 20, per_item: int = 6) -> list:
    """Short human-readable strings from each item (names, titles, quotes...), per item."""
    out = []
    for it in (items or [])[:limit_items]:
        vals, stack = [], [it]
        while stack and len(vals) < per_item:
            node = stack.pop(0)
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node[:5])
            elif isinstance(node, str):
                s = re.sub(r"\s+", " ", node).strip()
                if 4 <= len(s) <= 120 and not s.startswith(("http", "/", "data:")) and re.search(r"[A-Za-z\u00C0-\u024F\u0400-\u04FF]{3}", s):
                    vals.append(s.lower())
        out.append(vals)
    return out


def value_overlap(items, page_text: str) -> float | None:
    """Share of items with at least one of their values visible on the page.
    The core test for 'is this list the page's data?' - works for any field
    names, any language. None = the items carry no readable values to test."""
    vals = [v for v in item_values(items) if v]
    if not vals or not page_text:
        return None
    text = re.sub(r"\s+", " ", page_text).lower()
    return sum(1 for vs in vals if any(v[:60] in text for v in vs)) / len(vals)


# ------------------------------------------------------------------ picking the listing
ITEMISH_KEYS = re.compile(r"^(price|title|name|url|link|href|slug|image|img|thumbnail|coverphoto|"
                          r"area|rating|brand|sku|location|address|createdat|postedat|seller|agency)$", re.I)


def pick_listing_array(obj, page_count: int | None = None, expect=(), total_paths=()) -> dict | None:
    """The array that is THE result list, not a menu or a link cloud.
    Evidence: it sits next to a 'total results' number, its length matches the
    item count seen on the page, it contains the user's expected text, and its
    items carry listing-like fields (price, title, url...)."""
    from . import jsontools
    cands = jsontools.item_arrays(obj, min_len=2, limit=40)
    if not cands:
        return None
    parents = {p.rsplit(".", 1)[0] for p in total_paths}
    best, best_score = None, -1.0
    for c in cands:
        score = 0.0
        parent = c["path"].rsplit(".", 1)[0]
        if parent in parents or any(parent.startswith(p) or p.startswith(parent) for p in parents if p != "$"):
            score += 50
        if page_count and abs(c["length"] - page_count) <= max(2, page_count * 0.15):
            score += 40
        itemish = sum(1 for k in c["keys"] if ITEMISH_KEYS.match(str(k)))
        score += 6 * itemish
        if expect:
            arr = get_path(obj, c["path"])
            blob = json.dumps(arr, ensure_ascii=False)[:2_000_000].lower() if arr is not None else ""
            score += 30 * sum(1 for e in expect if e.lower() in blob)
        score += min(c["length"], 100) * 0.05     # mild tie-break toward bigger lists
        c = dict(c, score=round(score, 1))
        if score > best_score:
            best, best_score = c, score
    return best


def backend_hint(obj) -> str | None:
    """Some sites name their engine inside the state: {"activeBackendName": "elastic"}."""
    q = deque([obj])
    seen = 0
    while q and seen < 5000:
        node = q.popleft()
        seen += 1
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and re.search(r"backend|engine|searchprovider", str(k), re.I):
                    lv = v.lower()
                    for word, label in (("elastic", "Elasticsearch/OpenSearch"), ("opensearch", "Elasticsearch/OpenSearch"),
                                        ("algolia", "Algolia"), ("solr", "Solr"), ("typesense", "Typesense"),
                                        ("meili", "Meilisearch")):
                        if word in lv:
                            return label
                elif isinstance(v, (dict, list)):
                    q.append(v)
        elif isinstance(node, list):
            q.extend(x for x in node[:3] if isinstance(x, (dict, list)))
    return None


def list_paging(obj, list_path: str) -> dict:
    """Paging fields that belong to THE result list: keys sitting next to it
    (same parent object). A 'page' elsewhere in the state (a map widget, a
    saved search) is ignored - Zameen has three, none of them the list's."""
    parent_path = list_path.rsplit(".", 1)[0] if "." in list_path else "$"
    parent = get_path(obj, parent_path) if parent_path != "$" else obj
    if not isinstance(parent, dict):
        return {}
    want = ("page", "currentpage", "hitsperpage", "nbpages", "per_page", "pagesize",
            "page_size", "totalpages")
    return {str(k).lower(): v for k, v in parent.items()
            if str(k).lower() in want and isinstance(v, int) and not isinstance(v, bool)}
