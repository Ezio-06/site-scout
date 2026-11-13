"""Runs the scout against the fake landmine sites and checks each verdict.
    python tests/run_tests.py           (from the repo root, venv active)
"""
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import fakesites  # noqa: E402

import scout.checks.reach as reach  # noqa: E402
from scout.config import ScoutConfig  # noqa: E402
from scout.report import write  # noqa: E402
from scout.runner import run_scout  # noqa: E402

reach.MIN_GAP = 0          # local server - no need to be gentle
PORT = 8799
B = f"http://127.0.0.1:{PORT}"

PACE = {"pace": True, "pace_interval": 0.1, "pace_requests": 5}


def _claimed(v, text, evidence="verified"):
    return any(text in c["claim"] and c["evidence"] == evidence for c in v.get("claims", []))


# name: (url, browser, check(ctx, verdict) -> error string or None)
CASES = {
    "zameen-like": (B + "/Homes/Town-1-1.html", "never",
                    lambda c, v: None if (c.facts.get("reach") or {}).get("last_ok_page") == 40
                    and c.facts.get("embedded_list") else "should find window.state list and stop at page 40"),
    "page-field-wrong": (B + "/state/1", "never",
                         lambda c, v: None if (c.facts.get("reach") or {}).get("last_ok_page") == 20
                         else "should trust the list's own page field and stop at page 20"),
    "page2-repeats": (B + "/catalog", "never",
                      lambda c, v: None if (c.facts.get("reach") or {}).get("page2_ok") is False
                      else "should say page 2 is broken"),
    "deep-fallback": (B + "/cars", "never",
                      lambda c, v: None if (c.facts.get("reach") or {}).get("last_ok_page") == 30
                      else "should stop at page 30 (repeats after)"),
    "healthy-blog": (B + "/blog/", "never",
                     lambda c, v: None if (c.facts.get("reach") or {}).get("last_ok") else "every page should work"),
    "offset-jobs": (B + "/jobs", "never",
                    lambda c, v: None if (c.facts.get("reach") or {}).get("reachable_items") == 600
                    else "should reach exactly 600 items"),
    "spa-feeds": (B + "/spa", "always",
                  lambda c, v: None if v["tier"] == "A" and "api/search" in c.facts["feeds"][0]["url"]
                  else "should pick /api/search (main) over /_msearch (side list)"),
    # ---- 22 Oct: shapes found by the 50-site holdout run
    "challenge-word-on-rich-page": (B + "/shop-menu", "never",
        lambda c, v: None if v["tier"] == "B" and not c.facts.get("blocked_at_start")
        else f"a menu/script mentioning Turnstile is not a block (got {v['tier']}, {c.facts.get('blocked_at_start')})"),
    "cf-script-on-every-page": (B + "/cf-everywhere", "never",
        lambda c, v: None if v["tier"] == "B" and not c.facts.get("blocked_at_start") else "Cloudflare's page script is not a block"),
    "real-challenge-200": (B + "/challenge200", "never",
        lambda c, v: None if c.facts.get("blocked_at_start") else "a thin 'Just a moment' page IS a block"),
    "gate-202": (B + "/gate202", "never",
        lambda c, v: None if c.facts.get("blocked_at_start") else "a thin 202 bot-check page IS a block"),
    "app-shell-is-not-a-gate": (B + "/app", "auto",
        lambda c, v: None if not c.facts.get("blocked_at_start") and v["tier"] in ("A", "C")
        else f"an empty app shell needs a browser, it is not blocked (got {v['tier']}, {c.facts.get('blocked_at_start')})"),
    "junk-feeds-are-not-routes": (B + "/junkfeed", "always",
        lambda c, v: None if v["tier"] == "B" and not c.facts.get("feeds")
        else f"config/ad JSON must not become the data route (got {v['tier']}, {[x['url'] for x in c.facts.get('feeds') or []]})"),
    "scripts-are-not-items": (B + "/scriptlist", "auto",
        lambda c, v: None if not c.facts.get("raw_listing") and v["tier"] == "A" and "api/rows" in c.facts["feeds"][0]["url"]
        else f"<script> tags are not items; the /api/rows feed is the route (got {v['tier']}, raw_listing={bool(c.facts.get('raw_listing'))})"),
    "packed-list-any-field-names": (B + "/packed", "auto",
        lambda c, v: None if v["tier"] == "B" and c.facts.get("embedded_has_items")
        else f"`var data = [...]` painted by JS is packed data -> B (got {v['tier']})"),
    "pushback-on-page2-is-not-broken": (B + "/hn", "never",
        lambda c, v: None if (c.facts.get("reach") or {}).get("page2_ok") is None
        and not any("page 2" in b for b in v["effort_bumps"]) else f"419 on page 2 = untested, not broken ({c.facts.get('reach')})"),
    "page-num-paging": (B + "/forms", "never",
        lambda c, v: None if (c.facts.get("reach") or {}).get("page2_ok") is True else f"?page_num= paging should be found and work ({c.facts.get('reach')})"),
    "bare-more-button": (B + "/more", "always",
        lambda c, v: None if "'load more' button" in (c.facts.get("paging") or []) else f"a 'More' button is load-more ({c.facts.get('paging')})"),
    "transient-drop-is-retried": (B + "/flaky", "never",
        lambda c, v: None if v["tier"] == "B" and (c.facts.get("start_retries") or {}).get("recovered")
        else f"one dropped connection must be retried (got {v['tier']}, {c.facts.get('start_retries')})",
        {"start_retry_wait": 0.2}),
    "per-item-classes-still-one-list": (B + "/woo/", "never",
        lambda c, v: None if (c.facts.get("raw_listing") or {}).get("count") == 16 and (c.facts.get("reach") or {}).get("page2_ok") is True
        else f"16 products with per-item classes are ONE list, page 2 works (got {(c.facts.get('raw_listing') or {}).get('count')}, {c.facts.get('reach')})"),
    "expanders-are-not-load-more": (B + "/facets", "always",
        lambda c, v: None if "'load more' button" not in (c.facts.get("paging") or []) else "filter '+ More' expanders are not load-more"),
    "tarpit-is-a-guard": (B + "/tarpit", "auto",
        lambda c, v: None if v["tier"] == "D" and any("tarpit" in g for g in c.facts.get("hard_guard") or [])
        else f"hangs for scripts + browser OK = guarded (got {v['tier']}, {c.facts.get('hard_guard')})",
        {"timeout": 2.0, "start_retry_wait": 0.2}),
    # ---- 28 Oct: Daraz product pages - a 1 KB deny page came back as HTTP 200 and the card
    # said "Plain requests get the content: verified" and "1 per 6 s is safe: verified"
    "redirect-gate-is-a-block": (B + "/redirect-gate", "never",
        lambda c, v: None if c.facts.get("blocked_at_start") and not _claimed(v, "Plain requests get the content")
        else f"an empty 200 page that only jumps elsewhere is a block (blocked_at_start={c.facts.get('blocked_at_start')})"),
    "page-goes-sour-mid-run": (B + "/goes-sour", "never",
        lambda c, v: None if (c.facts.get("pace") or {}).get("warning") and not _claimed(v, "is safe")
        else f"the page turned into a gate during the pace check - pace must not pass ({c.facts.get('pace')})",
        PACE),
    "page-goes-blank-mid-run": (B + "/goes-blank", "never",
        lambda c, v: None if (c.facts.get("pace") or {}).get("warning") and not _claimed(v, "is safe")
        else f"a rich page turning into an empty 200 is a soft block ({c.facts.get('pace')})",
        PACE),
    "browser-gets-its-own-room": (B + "/browser-trips", "auto",
        lambda c, v: None if (c.facts.get("pace") or {}).get("recommended_interval") and c.facts.get("browser_triggered_block")
        and _claimed(v, "A real browser can be used", "broken")
        else f"plain tests first, then the browser's block is caught and named (pace={c.facts.get('pace')}, "
             f"triggered={c.facts.get('browser_triggered_block')})",
        dict(PACE, expect=["Stand Mixer Pro 9000", "Rs 99,999"])),
    "price-typed-from-screen": (B + "/price-format", "never",
        lambda c, v: None if c.results["location"].findings["expect"].get("24,990")
        else f"24,990 should match \"24990\" in the page data ({c.results['location'].findings['expect']})",
        {"expect": ["24,990", "Stand Mixer Pro 9000"]}),
    # ---- 13 Nov (live example): a normal 304 to the caching check's conditional GET was judged
    # "page shrank to 0 bytes (soft block)", which stopped every later request and skipped pace
    "not-modified-is-not-a-block": (B + "/etag-list", "never",
        lambda c, v: None if c.facts.get("conditional_get") and not c.fetcher.blocked
        and (c.facts.get("pace") or {}).get("recommended_interval")
        else f"a 304 is the site saying 'not changed', not a block (blocked={c.fetcher.blocked!r}, "
             f"pace={c.facts.get('pace')})",
        PACE),
    # ---- phase 2: robots.txt forbids the start page - the verdict used to crash (NameError on `why`)
    "robots-forbidden-start-has-a-verdict": (B + "/private/list", "never",
        lambda c, v: None if "verdict failed" not in v["method"] and any("bot rules forbid" in w for w in v["why"])
        else f"a forbidden start page must still give a verdict that says why (got {v['method']!r}, {v['why']})"),
}


def browser_available() -> bool:
    """True when Playwright AND a Chromium it can launch are present (checked once)."""
    if "ok" not in _BROWSER:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                pw.chromium.launch().close()
            _BROWSER["ok"] = True
        except Exception:
            _BROWSER["ok"] = False
    return _BROWSER["ok"]


_BROWSER: dict = {}


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), fakesites.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_case(name, out_root):
    """Run one landmine case. Returns an error string, or None when it passed."""
    url, browser, check, *extra = CASES[name]
    kw = {"pace": False, "min_delay": 0.0}
    kw.update(extra[0] if extra else {})
    cfg = ScoutConfig(url=url, browser=browser, out_dir=str(Path(out_root) / name), **kw)
    t0 = time.time()
    try:
        ctx = run_scout(cfg, echo=lambda *a: None)
        v = write(ctx, time.time() - t0)
        crashed = [k for k, r in ctx.results.items() if r.status == "error"]
        return check(ctx, v) or (f"checks crashed: {crashed}" if crashed else None)
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def main():
    srv = start_server()
    out_root = HERE / "out"
    failed = 0
    only = set(sys.argv[1:])
    for name in CASES:
        if only and name not in only:
            continue
        err = run_case(name, out_root)
        if err and "browser" in str(err).lower() and CASES[name][1] == "always":
            print(f"  SKIP  {name}: browser not available")
            continue
        failed += bool(err)
        print(f"  {'FAIL' if err else 'ok  '}  {name}" + (f": {err}" if err else ""))
    srv.shutdown()
    if not only:  # standalone landmine scripts
        import subprocess
        for script in ["test_robots_identity.py"]:
            rc = subprocess.run([sys.executable, str(HERE / script)]).returncode
            failed += bool(rc)
            print(f"  {'FAIL' if rc else 'ok  '}  {script}")
    print("all passed" if not failed else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
