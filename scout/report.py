"""Turns the facts into a scout card: a verdict (method + difficulty), safe
pace, effort band for quoting, the evidence, and what was NOT tested."""
from __future__ import annotations

import json
import socket
import time
import traceback

from . import __version__, guards
from .config import EFFORT_BANDS

SCHEMA_VERSION = 1   # layout of report.json; see docs/report.schema.json

SIZES = ["S", "M", "L", "XL"]

METHODS = {
    "platform_feed": ("A", "Platform data feed", "easy"),
    "hidden_feed":   ("A", "Hidden data feed (direct JSON)", "easy"),
    "embedded":      ("B", "Plain fetch + read packed data", "easy"),
    "plain_html":    ("B", "Plain fetch + parse HTML", "easy"),
    "browser":       ("C", "Real browser needed", "medium"),
    "browser_guard": ("D", "Browser + bot protection", "hard"),
    "blocked":       ("D", "Blocked - no working route found", "hard"),
    "unknown":       ("?", "Unclear - see unknowns", "unknown"),
}


def protection(f: dict) -> list:
    """Bot protection judged by what the site DID, not only by vendor names.
    (22 Oct holdout: Zillow showed PerimeterX's challenge, Reddit 403'd plain Python,
    Best Buy hung every plain request - all passed by a better route, so the card
    never said 'guarded'.) Adds behaviour evidence to f['hard_guard']."""
    ev = list(f.get("hard_guard") or [])
    if f.get("tls_impersonation"):
        ev.append(f"filters scripted clients ({f.get('blocked_plain_python', 'blocked')} to plain Python, "
                  "passes with a Chrome connection fingerprint)")
    if f.get("unreachable") and f.get("browser_passes_block"):
        ev.append("plain requests hang or drop while a real browser loads the page (tarpit)")
    elif f.get("browser_passes_block"):
        ev.append("blocks plain requests, lets a real browser in")
    if f.get("challenge_solved_in_browser"):
        ev.append("the browser had to wait out a challenge page")
    if f.get("browser_blocked"):
        ev.append("blocks even a real browser")
    if f.get("browser_triggered_block"):
        ev.append("a real-browser visit got this connection blocked for plain requests too")
    seen, out = set(), []
    for e in ev:
        if e not in seen:
            seen.add(e)
            out.append(e)
    f["hard_guard"] = out
    return out


def verdict(ctx) -> dict:
    f, cfg = ctx.facts, ctx.cfg
    why, bumps, unknowns = [], [], []
    protection(f)

    # ---- choose the method (best available route wins)
    if f.get("rules_forbid_start"):
        method = "unknown"
        why.append("the site's bot rules forbid this page, so nothing was fetched from it")
    elif f.get("unreachable") and f.get("browser_passes_block"):
        method = "browser_guard"
        why.append("plain requests time out / drop, but a real browser loads the page")
    elif f.get("unreachable"):
        method = "unknown"
        why.append(f"start page unreachable: {f['unreachable']}")
    elif f.get("platform_feeds"):
        method = "platform_feed"
        why.append(f"{f['platform_feeds'][0]['label']} returns clean JSON")
    elif f.get("feeds"):
        method = "hidden_feed"
        how = f["feeds"][0]["replay"]["how"]
        why.append("a behind-the-scenes JSON feed works " +
                   ("with a plain request" if how == "plain" else "when copying the browser's headers"))
    elif f.get("blocked_at_start"):
        if f.get("browser_passes_block"):
            method = "browser_guard"
            why.append("plain requests blocked, real browser gets through"
                       + ("; plain requests work with the browser's cookies"
                          if f.get("cookie_reuse_works") else ""))
        elif f.get("browser_blocked"):
            method = "blocked"
            why.append("both plain requests and the browser were blocked")
        else:
            method = "unknown"
            why.append("plain requests blocked; browser not tried")
            unknowns.append("Whether a real browser gets past the block (browser check did not run).")
    elif f.get("needs_browser") is True and not f.get("embedded_has_items"):
        method = "browser"
        why.append("content only appears after the page runs its own code")
    elif f.get("needs_browser") is None:
        method = "unknown"
        why.append("could not confirm where the data lives")
    elif f.get("embedded_has_items"):
        method = "embedded"
        why.append("the raw page already contains a clean data block with the items")
    else:
        method = "plain_html"
        why.append("the content is in the raw page")
        if f.get("needs_browser") == "partly":
            unknowns.append("Part of the page is added by code - check that the data you need is not in that part.")

    if method in ("browser", "plain_html", "embedded", "hidden_feed", "platform_feed") and f.get("hard_guard"):
        bumps.append(f"bot protection present ({', '.join(f['hard_guard'])})")
        if method == "browser":
            method = "browser_guard"

    if f.get("tls_impersonation") and method in ("plain_html", "embedded", "hidden_feed", "platform_feed"):
        why.append("plain Python is blocked, so requests must copy a browser fingerprint (curl_cffi)")
    tier, label, difficulty = METHODS[method]

    # ---- effort band
    start = {"A": 0, "B": 0, "C": 1, "D": 2, "?": 1}[tier]
    if f.get("cookie_reuse_works"):
        start -= 1   # browser only for cookies, the rest is plain requests
    if f.get("fragility") == "high" and tier in ("B", "C", "D"):
        bumps.append("fragile layout (random-looking class names)")
    if f.get("login_wall"):
        bumps.append("login wall")
    pace = f.get("pace") or {}
    if pace.get("warning") or (f.get("crawl_delay") or 0) > 10:
        bumps.append("strict pace limits")
    if any(p in (f.get("paging") or []) for p in ("endless scroll", "'load more' button")) \
            and tier in ("C", "D"):
        bumps.append("button/scroll paging without a direct feed")
    reach = f.get("reach") or {}
    if reach.get("page2_ok") is False:
        bumps.append("page 2 doesn't work as advertised - another paging route needed")
    elif reach.get("share") is not None and reach["share"] < 0.98:
        bumps.append(f"paging only reaches ~{reach['share']:.0%} of items - job must be split into slices")
    if (f.get("pages_needed") or 0) > 1000:
        bumps.append("many list pages (scheduling, resume-after-failure, storage)")
    elif (f.get("sitemap_urls") or 0) > 50_000:
        bumps.append("huge site - only applies if the job covers all of it")
    if f.get("ua_filtering"):
        pass  # trivial to handle; noted in evidence only
    size = SIZES[max(0, min(start + len(bumps), len(SIZES) - 1))]

    # ---- pace recommendation
    if pace.get("recommended_interval"):
        safe = f"1 request per {pace['recommended_interval']:g} s (tested)"
    elif pace.get("stopped_for_allowance"):
        safe = f"stay under the site's stated limit ({pace.get('stated_limit')} per window)"
    elif f.get("crawl_delay"):
        safe = f"1 request per {f['crawl_delay']:g} s (site's own rule, not tested)"
    elif pace.get("warning"):
        safe = "slower than the gentle test pace - site pushed back"
    else:
        safe = "not tested"
        if f.get("blocked_at_start"):
            unknowns.append("Safe pace: plain requests are blocked, so the pace check could not run "
                            "(browser-based pace testing is a v2 item).")
        elif not cfg.pace:
            unknowns.append("Safe pace (pace check turned off with --no-pace).")
        else:
            unknowns.append("Safe pace (pace check did not run - see its section).")

    # ---- other unknowns
    if f.get("browser_unavailable"):
        unknowns.append("Browser checks (Playwright not available): hidden feeds and JS-only content untested.")
    elif ctx.rendered() is None and cfg.browser != "never" and not f.get("unreachable"):
        unknowns.append("Hidden data feeds: browser not used because the raw page looked complete "
                        "(run with --browser always to hunt for feeds).")
    if cfg.browser == "never":
        unknowns.append("Browser checks skipped (--browser never).")
    if not cfg.pace_fast and pace.get("clean_interval"):
        unknowns.append("Speeds faster than the gentle pace (use --pace-fast).")
    if not reach:
        rr = ctx.results.get("reach")
        skip_reason = (rr.reason if rr else "") or "reach check did not run"
        unknowns.append(f"Whether paging really reaches every item ({skip_reason}).")
    elif reach.get("page2_ok") and reach.get("limit") is None and not reach.get("last_ok") \
            and reach.get("share") is None:
        unknowns.append("Deepest reachable page (site total unknown).")
    if f.get("browser_triggered_block"):
        unknowns.insert(0, "Opening a real browser got this connection blocked (plain requests worked before it). "
                           "Anything tested after that point ran blocked - never let the scraper use a browser here, "
                           "and wait before re-running the scout from this network.")
    unknowns.append("Long-window limits (hourly/daily quotas) - a short test cannot see them.")
    unknowns.append("How often the data changes (needs watch mode, planned for v2).")
    if f.get("robots_allowed") is False:
        unknowns.insert(0, "robots.txt disallows this page - check terms/permission before quoting.")

    hours = EFFORT_BANDS[size]
    if f.get("unreachable"):
        size, hours = "?", "n/a"
    return {"tier": tier, "method": label, "difficulty": difficulty, "why": why,
            "effort": size, "effort_hours": hours, "effort_bumps": bumps,
            "safe_pace": safe, "unknowns": unknowns}


EVIDENCE = {
    "verified": "tested by fetching it",
    "broken": "tested - does NOT work",
    "seen": "the site says so / seen once, not tested",
    "assumed": "general knowledge, not tested on this site",
    "unknown": "not checked",
}


def claims(ctx, v) -> list:
    """Every claim the card makes, with how strong its evidence is. Only
    'verified' claims should feed a quote."""
    f = ctx.facts
    out = []
    add = lambda claim, ev, detail="": out.append({"claim": claim, "evidence": ev, "detail": detail})

    # route
    if f.get("feeds"):
        add("A direct feed carries the main result list", "verified",
            f["feeds"][0].get("role_why") or "replayed outside the browser")
    elif f.get("embedded_list"):
        e = f["embedded_list"]
        add("The raw page carries the result list as data", "verified",
            f"{e['length']} items at {e['block']} {e['path']}")
    elif f.get("needs_browser") is False:
        add("Plain requests get the content", "verified", "found in the raw page")
    for c in (f.get("feeds_seen") or []):
        if c.get("role") == "side list":
            add(f"Feed {c['url'].split('?')[0][:60]} is NOT the main list", "verified", c.get("role_why", ""))
    if f.get("browser_triggered_block"):
        add("A real browser can be used on this site", "broken",
            f"plain requests were blocked right after the browser visit ({f['browser_triggered_block']})")
    # guards (present = a header/cookie/status rule matched, or a thin page matched a body rule)
    for g in f.get("guards") or []:
        add(f"Bot guard: {g}", "seen", "matched in the start page's headers, cookies or text; not a block by itself")
    # identity
    ident = f.get("identity") or {}
    if ident.get("honest_ok") is True:
        add("Site accepts an honest bot identity", "verified", "SiteScout/%s user agent was served normally" % __version__)
    elif ident.get("honest_ok") is False:
        add("Site accepts an honest bot identity", "broken", ident.get("note", "site blocks honest bots"))
    elif ident:
        add("Site accepts an honest bot identity", "unknown", ident.get("note", ""))
    # robots
    if f.get("robots_allowed") is not None:
        add("robots.txt allows the start page", "verified" if f["robots_allowed"] else "broken")
    for host, state in (f.get("robots_other_hosts") or {}).items():
        add(f"robots.txt of {host}", "verified", state)
    # size
    if f.get("total_items"):
        add(f"About {f['total_items']:,} items exist", "seen", "the site's own count")
    # paging
    reach = f.get("reach") or {}
    if reach.get("page2_ok") is True:
        add("Page 2 gives different items", "verified")
    elif reach.get("page2_ok") is False:
        add("Page 2 gives different items", "broken", reach.get("verdict", ""))
    else:
        add("Page 2 gives different items", "unknown")
    if reach.get("last_ok"):
        add("Every item is reachable by paging", "verified", "the last page served real results")
    elif reach.get("share") is not None:
        add("Every item is reachable by paging", "broken",
            f"only ~{reach['share']:.0%}; last working page {reach.get('last_ok_page'):,}")
    else:
        add("Every item is reachable by paging", "unknown")
    eng = (f.get("search_backend") or {}).get("name")
    if eng:
        add(f"Search engine: {eng}", "seen", "named in the page data")
    # pace
    pace = f.get("pace") or {}
    if pace.get("recommended_interval"):
        add(f"1 request per {pace['recommended_interval']:g} s is safe", "verified",
            "short test only - hourly/daily limits unknown")
    elif pace.get("warning"):
        add("Gentle pace is safe", "broken", str(pace.get("warning")))
    else:
        add("Safe pace", "unknown")
    return out


def missing_extras() -> list:
    """Checks that could not run because an optional extra is not installed - said plainly on the card."""
    from importlib.util import find_spec
    out = []
    if find_spec("playwright") is None:
        out.append("Real-browser checks not tested: Playwright is not installed "
                   "(pip install site-scout[browser], then python -m playwright install chromium).")
    if find_spec("curl_cffi") is None:
        out.append("Browser-fingerprint (TLS) check not tested: curl_cffi is not installed "
                   "(pip install site-scout[tls]).")
    return out


def _clean(obj):
    """Make findings JSON-safe (drop private keys / soup objects)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple, set)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _safe_verdict(ctx) -> dict:
    try:
        return verdict(ctx)
    except Exception as e:   # a strange fact must never cost us the card
        return {"tier": "?", "method": "verdict failed - see report.json", "difficulty": "unknown",
                "why": [f"{type(e).__name__}: {e}"], "effort": "?", "effort_hours": "n/a",
                "effort_bumps": [], "safe_pace": "unknown",
                "unknowns": ["Verdict could not be computed: " + traceback.format_exc(limit=2)[-300:]]}


def write(ctx, elapsed: float) -> dict:
    v = _safe_verdict(ctx)
    f = ctx.facts
    try:
        cl = claims(ctx, v)
    except Exception as e:
        cl = [{"claim": "claims table failed", "evidence": "unknown", "detail": str(e)}]
    v["claims"] = cl
    v["unknowns"] = list(v.get("unknowns") or []) + missing_extras()
    lines = []
    add = lines.append
    add(f"# Scout card - {ctx.host}")
    add("")
    add(f"`{ctx.url}`  ")
    add(f"{time.strftime('%Y-%m-%d %H:%M')} - {ctx.fetcher.count} requests - {elapsed:.0f} s - "
        f"ran from `{socket.gethostname()}`{' via proxy' if ctx.cfg.proxy else ''}  ")
    ident = f.get("identity") or {}
    if ident.get("honest_ok") is False and ident.get("used") == "browser":
        add("identity: browser - site blocks honest bots  ")
    elif ctx.cfg.identity == "browser":
        add("identity: browser (chosen with --identity browser)  ")
    else:
        add("identity: honest (SiteScout user agent)  ")
    if f.get("interrupted"):
        add(f"**Run was cut short: {f['interrupted']}** - findings below are partial.  ")
    add("")
    add("## Verdict")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| **Method** | **{v['tier']} - {v['method']}** ({v['difficulty']}) |")
    add(f"| Why | {'; '.join(v['why'])} |")
    add(f"| Safe pace | {v['safe_pace']} |")
    bumps = f" - raised by: {', '.join(v['effort_bumps'])}" if v["effort_bumps"] else ""
    add(f"| Effort | **{v['effort']}** ({v['effort_hours']}, starting estimate){bumps} |")
    if f.get("total_items"):
        extra = f" over ~{f['pages_needed']:,} list pages" if f.get("pages_needed") else ""
        reach = f.get("reach") or {}
        if reach.get("share") is not None and not reach.get("last_ok"):
            extra += f"; paging reaches only ~{reach['share']:.0%}"
        add(f"| Size | ~{f['total_items']:,} items{extra} |")
    elif f.get("sitemap_urls"):
        add(f"| Size | ~{f['sitemap_urls']:,} pages in sitemap |")
    add("")

    warnings = [(r.title, w) for r in ctx.results.values() for w in r.warnings]
    if f.get("rules_forbid_start"):   # said from the request log, not from intent
        n = sum(1 for e in ctx.fetcher.log if e.get("url") == ctx.url)
        warnings.append(("Start page", "no request was made to it" if n == 0 else
                         f"the scout DID request it {n} time(s) before the rules were known - see requests.log.json"))
    if warnings:
        add("## Warnings")
        add("")
        for title, w in warnings:
            add(f"- **{title}:** {w}")
        add("")

    add("## Claims and evidence")
    add("")
    add("Only *verified* claims should go into a quote. Results are for this network only - "
        "re-run from a server before moving a collector there.")
    add("")
    add("| Claim | Evidence | Detail |")
    add("|---|---|---|")
    for c in cl:
        add(f"| {c['claim']} | **{c['evidence']}** | {c['detail']} |")
    add("")

    add("## Findings")
    add("")
    for r in ctx.results.values():
        badge = {"ok": "", "skipped": " *(skipped)*", "error": " *(error)*"}.get(r.status, "")
        add(f"### {r.title}{badge}")
        if r.reason:
            add(f"_{r.reason}_")
        for n in r.notes:
            add(f"- {n}")
        if not r.notes and not r.reason:
            add("- nothing notable")
        add("")

    add("## Not tested / unknowns")
    add("")
    for u in v["unknowns"]:
        add(f"- {u}")
    add("")
    add("## Files")
    add("")
    add("- `report.json` - every finding, machine-readable")
    add("- `requests.log.json` - every request the scout made")
    add("- `snapshots/` - saved pages and feeds (reuse them as test material for the scraper)")
    card = "\n".join(lines) + "\n"

    (ctx.out_dir / "scout_card.md").write_text(card, encoding="utf-8")
    report = {
        "schema_version": SCHEMA_VERSION,
        "url": ctx.url, "host": ctx.host, "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(elapsed, 1), "requests": ctx.fetcher.count,
        "signature_rules_skipped": guards.skipped_rules(),
        "verdict": v, "facts": _clean(f),
        "checks": {k: _clean(r.__dict__) for k, r in ctx.results.items()},
        "config": _clean({k: getattr(ctx.cfg, k) for k in ctx.cfg.__dataclass_fields__}),
    }
    (ctx.out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                             encoding="utf-8")
    (ctx.out_dir / "requests.log.json").write_text(json.dumps(ctx.fetcher.log, indent=1),
                                                   encoding="utf-8")
    return v
