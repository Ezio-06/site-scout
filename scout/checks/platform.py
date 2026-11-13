"""Known shop/CMS platforms ship public data feeds. If the site runs on one,
try its feed - it's usually the cleanest, sturdiest way to get the data."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .. import jsontools
from .base import Check, SkipCheck

NEXT_BUILD = re.compile(r'"buildId"\s*:\s*"([^"]+)"')


class PlatformFeeds(Check):
    name = "platform"
    title = "Platform shortcuts (built-in data feeds)"
    order = 25

    def should_run(self, ctx):
        tech = set(ctx.facts.get("tech") or [])
        if not tech & {"Shopify", "WordPress", "WooCommerce", "Next.js"}:
            return "no platform with a known public feed detected"
        if ctx.facts.get("blocked_at_start"):
            return "site blocked plain requests"
        return None

    def _try(self, ctx, label, url, accept_json=True):
        if not ctx.allowed(url):
            self.note(f"{label}: robots.txt disallows {url} - not tried.")
            return None
        hdr = {"Accept": "application/json"} if accept_json else None
        r = ctx.get(url, headers=hdr, purpose="platform feed")
        data = r.json() if r.ok else None
        arrays = jsontools.item_arrays(data, min_len=1) if data is not None else []
        works = data is not None
        res = {"label": label, "url": url, "status": r.status, "works": works,
               "bytes": r.size, "item_arrays": arrays}
        if works:
            self.note(f"{label} WORKS: {url} returns clean JSON"
                      + (f" (list at {arrays[0]['path']}, fields: {', '.join(arrays[0]['keys'][:6])})"
                         if arrays else "") + ".")
            ctx.save(f"feed_{label.split()[0].lower()}.json", r.text[:500_000])
        else:
            self.note(f"{label}: not available (HTTP {r.status or r.error}).")
        return res

    def run(self, ctx):
        tech = set(ctx.facts.get("tech") or [])
        tried = []
        if "Shopify" in tech:
            tried.append(self._try(ctx, "Shopify products feed", ctx.origin + "/products.json?limit=1"))
            self.note("Shopify feed pages with ?limit=250&page=N; per-collection feeds live at "
                      "/collections/<name>/products.json.")
        if "WooCommerce" in tech:
            tried.append(self._try(ctx, "WooCommerce Store API",
                                   ctx.origin + "/wp-json/wc/store/v1/products?per_page=1"))
        elif "WordPress" in tech:
            tried.append(self._try(ctx, "WordPress REST API",
                                   ctx.origin + "/wp-json/wp/v2/posts?per_page=1"))
        if "Next.js" in tech:
            raw = ctx.main_page()
            m = NEXT_BUILD.search(raw.text)
            if m:
                path = urlsplit(ctx.url).path.strip("/") or "index"
                url = f"{ctx.origin}/_next/data/{m.group(1)}/{path}.json"
                q = urlsplit(ctx.url).query
                tried.append(self._try(ctx, "Next.js page data", url + (f"?{q}" if q else "")))
                self.note("Next.js page-data URLs contain a build id that changes on every site "
                          "deploy - a scraper must re-read it from the page each run.")
        tried = [t for t in tried if t]
        working = [t for t in tried if t["works"]]
        ctx.facts["platform_feeds"] = working
        if not tried:
            raise SkipCheck("nothing to try")
        return {"tried": tried}
