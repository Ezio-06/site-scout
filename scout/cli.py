"""Site Scout command line - run:  site-scout https://example.com/listing   (or python run_scout.py ...)

Examples
  python run_scout.py https://example.com/shop
  python run_scout.py https://example.com/shop --expect "Blue Widget" --expect "$19.99"
  python run_scout.py https://example.com/shop --browser always --pace-fast
  python run_scout.py https://example.com/shop --no-pace --browser never     (quickest, 3-6 requests)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from scout import __version__
from scout.checks import discover
from scout.config import ScoutConfig
from scout.logsetup import setup_console
from scout.report import write
from scout.runner import run_scout


def main(argv=None):
    p = argparse.ArgumentParser(prog="site-scout", description="Scout a website before building a scraper.")
    p.add_argument("url", nargs="?", help="page to scout (ideally a listing/search-results page)")
    p.add_argument("--expect", action="append", default=[], metavar="TEXT",
                   help="a piece of text you can see on the page (product name, price...). "
                        "The scout reports where it lives. Repeatable.")
    p.add_argument("--browser", choices=["auto", "always", "never"], default="auto",
                   help="auto: only when the raw page looks incomplete (default)")
    p.add_argument("--no-pace", action="store_true", help="skip the pace check")
    p.add_argument("--pace-fast", action="store_true",
                   help="after the gentle pace, step up carefully until a warning sign")
    p.add_argument("--pace-interval", type=float, default=6.0, help="seconds between gentle pace requests")
    p.add_argument("--pace-requests", type=int, default=10, help="requests in the gentle pace check")
    p.add_argument("--min-delay", type=float, default=2.0, help="minimum seconds between any two requests")
    p.add_argument("--max-requests", type=int, default=60, help="hard cap on requests for the whole run")
    p.add_argument("--identity", choices=["honest", "browser"], default="honest",
                   help="honest: say 'SiteScout' (default; falls back to Chrome with ONE labelled "
                        "request if the site refuses). browser: use Chrome's identity from the start")
    p.add_argument("--proxy", help="e.g. http://user:pass@host:port")
    p.add_argument("--out", help="output folder (default: scouts/<site>_<time>)")
    p.add_argument("--only", help="comma-separated check names to run")
    p.add_argument("--skip", help="comma-separated check names to skip")
    p.add_argument("--list-checks", action="store_true", help="show available checks and exit")
    p.add_argument("-v", "--verbose", action="store_true", help="show DEBUG messages too (every request)")
    p.add_argument("--quiet", action="store_true", help="console shows warnings only (scout.log still gets everything)")
    p.add_argument("--version", action="version", version=f"site-scout {__version__}")
    a = p.parse_args(argv)

    try:  # Windows consoles choke on some characters - never crash on printing
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    if a.list_checks:
        for c in discover():
            sys.stdout.write(f"  {c.name:<12} {c.title}\n")
        return 0
    if not a.url:
        p.error("url is required")
    url = a.url if "://" in a.url else "https://" + a.url

    cfg = ScoutConfig(
        url=url, expect=a.expect, browser=a.browser, pace=not a.no_pace, pace_fast=a.pace_fast,
        pace_interval=a.pace_interval, pace_requests=a.pace_requests, min_delay=a.min_delay,
        max_requests=a.max_requests, identity=a.identity, proxy=a.proxy, out_dir=a.out, verbose=a.verbose,
        only=[s.strip() for s in (a.only or "").split(",") if s.strip()],
        skip=[s.strip() for s in (a.skip or "").split(",") if s.strip()],
    )
    handler = setup_console(logging.DEBUG if a.verbose else logging.WARNING if a.quiet else logging.INFO)
    try:
        t0 = time.time()
        ctx = run_scout(cfg)
        v = write(ctx, time.time() - t0)
    finally:
        logging.getLogger("scout").removeHandler(handler)

    print()
    print(f"  METHOD : {v['tier']} - {v['method']} ({v['difficulty']})")
    print(f"  WHY    : {'; '.join(v['why'])}")
    print(f"  PACE   : {v['safe_pace']}")
    print(f"  EFFORT : {v['effort']} ({v['effort_hours']})" +
          (f"  raised by: {', '.join(v['effort_bumps'])}" if v["effort_bumps"] else ""))
    weak = [c for c in v.get("claims", []) if c["evidence"] in ("broken", "unknown")]
    for c in weak:
        print(f"  {c['evidence'].upper():<7}: {c['claim']}" + (f" - {c['detail']}" if c["detail"] else ""))
    print(f"\n  Scout card: {ctx.out_dir / 'scout_card.md'}")
    return 0
