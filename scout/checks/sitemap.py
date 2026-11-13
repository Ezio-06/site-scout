"""Sitemap: often a free list of every page, and a size estimate for quoting."""
from __future__ import annotations

import gzip
import re
from collections import Counter
from urllib.parse import urlsplit

from .base import Check

LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
LASTMOD = re.compile(r"<lastmod>", re.I)


def _text(r) -> str:
    data = r.content
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data).decode("utf-8", "replace")
        except Exception:
            return ""
    return r.text


class Sitemap(Check):
    name = "sitemap"
    title = "Sitemap (page list)"
    order = 15

    def run(self, ctx):
        candidates = list(ctx.facts.get("sitemaps") or []) or [ctx.origin + "/sitemap.xml"]
        r, url = None, None
        for url in candidates[:2]:
            r = ctx.get(url, purpose="sitemap", escalate=False)
            if r.ok and LOC.search(_text(r)[:200_000]):
                break
            r = None
        if r is None:
            self.note("No sitemap found - pages have to be discovered by following links.")
            ctx.facts["sitemap_urls"] = 0
            return {"found": False}

        body = _text(r)
        locs = LOC.findall(body)
        is_index = "<sitemapindex" in body[:2000].lower()
        out = {"found": True, "url": url, "is_index": is_index, "has_lastmod": bool(LASTMOD.search(body))}

        if is_index:
            out["child_sitemaps"] = len(locs)
            out["child_examples"] = locs[:5]
            child = ctx.get(locs[0], purpose="sitemap child", escalate=False) if locs else None
            if child is not None and child.ok:
                cbody = _text(child)
                child_locs = LOC.findall(cbody)
                out["urls_in_first_child"] = len(child_locs)
                est = len(child_locs) * len(locs)
                out["estimated_urls"] = est
                locs = child_locs
                self.note(f"Sitemap index with {out['child_sitemaps']} parts; "
                          f"first part lists {len(child_locs):,} pages -> roughly {est:,} pages total.")
            else:
                self.note(f"Sitemap index with {len(locs)} parts (could not open a part).")
                locs = []
        else:
            out["urls"] = len(locs)
            self.note(f"Sitemap lists {len(locs):,} pages.")

        if locs:
            sections = Counter()
            for u in locs:
                seg = (urlsplit(u).path.strip("/").split("/") or [""])[0]
                sections["/" + seg + ("/" if seg else "")] += 1
            out["sections"] = dict(sections.most_common(8))
            out["examples"] = locs[:5]
            top = ", ".join(f"{k} ({v:,})" for k, v in sections.most_common(4))
            self.note(f"Main sections by URL: {top}.")
        if out["has_lastmod"]:
            self.note("Pages carry 'last modified' dates - a scraper can re-visit only what changed.")
        ctx.facts["sitemap_urls"] = out.get("estimated_urls", out.get("urls", 0))
        return out
