"""Can a scraper ask 'has this changed since last time?' and get a tiny
'no' answer? If yes, frequent re-checks are cheap for us and for the site."""
from __future__ import annotations

from .base import Check


class ChangeMarkers(Check):
    name = "caching"
    title = "Cheap re-checks (change markers)"
    order = 65

    def should_run(self, ctx):
        r = ctx.main_page()
        if not r.ok:
            return "start page not available to plain requests"
        h = {k.lower() for k in r.headers}
        if "etag" not in h and "last-modified" not in h:
            self.note("No change markers - every re-check downloads the full page.")
            ctx.facts["conditional_get"] = False
            return "site sends no ETag/Last-Modified"
        return None

    def run(self, ctx):
        r = ctx.main_page()
        h = {k.lower(): v for k, v in r.headers.items()}
        cond = {}
        if "etag" in h:
            cond["If-None-Match"] = h["etag"]
        if "last-modified" in h:
            cond["If-Modified-Since"] = h["last-modified"]
        r2 = ctx.get(ctx.url, headers=cond, use_cache=False, purpose="conditional GET")
        works = r2.status == 304
        ctx.facts["conditional_get"] = works
        if works:
            self.note(f"Site answers 'not changed' (304, {r2.size} bytes) instead of resending "
                      f"{r.size / 1024:.0f} KB - re-checks can be frequent and cheap.")
        else:
            self.note(f"Change markers present but ignored (got HTTP {r2.status} with full page).")
        return {"headers_sent": list(cond), "status": r2.status, "bytes": r2.size, "works": works}
