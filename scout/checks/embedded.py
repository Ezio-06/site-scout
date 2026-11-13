"""Clean data blocks already packed inside the raw page (for Google, or for the
site's own app). Reading these beats picking through the layout.

Finds ANY JavaScript data blob (window.state = {...}, __NEXT_DATA__, Redux /
Apollo / Nuxt state, JSON.parse("...")), picks the array that is the actual
result list (not a menu), reads the site's own "total results" number, and
notes which search engine the site uses (each has a known paging limit)."""
from __future__ import annotations

import json
import re

from .base import Check
from .. import datatools, jsontools, listing
from ..htmltools import contains


def _types(obj, acc):
    if isinstance(obj, dict):
        t = obj.get("@type")
        if isinstance(t, list):
            acc.extend(map(str, t))
        elif t:
            acc.append(str(t))
        for v in obj.values():
            _types(v, acc)
    elif isinstance(obj, list):
        for v in obj[:200]:
            _types(v, acc)


class EmbeddedData(Check):
    name = "embedded"
    title = "Data packed inside the page"
    order = 30

    def should_run(self, ctx):
        r = ctx.main_page()
        if not r.ok:
            return "start page not available to plain requests"
        return None

    def run(self, ctx):
        r = ctx.main_page()
        soup = ctx.soup("raw", r.text)
        blocks = []
        expect = ctx.cfg.expect

        # how many items the page visibly shows - used to recognise the real list
        lst = ctx.facts.get("raw_listing")
        if lst is None:
            lst = listing.find_listing(soup)
            ctx.facts["raw_listing"] = lst
        page_count = lst["count"] if lst else None

        # ---- JSON-LD (structured data for search engines)
        ld_types = []
        for i, tag in enumerate(soup.find_all("script", type="application/ld+json")):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            _types(data, ld_types)
            blocks.append(self._describe(f"JSON-LD #{i + 1}", tag.string or "", data, page_count, expect))
        if ld_types:
            uniq = sorted(set(ld_types), key=ld_types.index)
            self.note(f"Structured data for search engines (JSON-LD): {', '.join(uniq[:8])}.")

        # ---- the site's own app state (any framework)
        try:
            states, unreadable = datatools.state_blobs(soup)
        except Exception as e:   # a weird page must not cost us the rest of this check
            states, unreadable = [], []
            self.warn(f"Could not read the page's scripts ({type(e).__name__}).")
        for name, obj, size in states:
            raw = json.dumps(obj, ensure_ascii=False)[:3_000_000]
            blocks.append(self._describe(name, raw, obj, page_count, expect, size=size, app_state=True))
        for label in unreadable:
            self.note(f"Page uses {label} - not read here; a browser or the hidden feed is the route for that part.")

        # ---- microdata / OpenGraph
        micro = len(soup.find_all(attrs={"itemtype": True}))
        og = len(soup.find_all("meta", property=re.compile(r"^og:")))
        if micro:
            self.note(f"{micro} microdata blocks (itemprop attributes) - stable hooks for scraping.")

        # ---- pick the block that holds THE result list
        rich = [b for b in blocks if b.get("list")]
        ctx.facts["embedded_blocks"] = [{k: v for k, v in b.items() if k != "_obj"} for b in blocks]
        best = max(rich, key=lambda b: b["list"]["score"]) if rich else None
        # a list only counts if there is real evidence it is the results (not a menu or link cloud)
        if best and best["list"]["score"] < 40:
            best = None
        ctx.facts["embedded_has_items"] = bool(best)
        if best:
            a = best["list"]
            ctx.facts["embedded_list"] = {"block": best["kind"], "path": a["path"], "length": a["length"],
                                          "keys": a["keys"], "app_state": best.get("app_state", False)}
            arr = datatools.get_path(best["_obj"], a["path"]) or []
            ctx.facts["page1_ids"] = datatools.item_ids(arr)
            self.note(f"'{best['kind']}' holds the result list: {a['length']} items at {a['path']} "
                      f"(fields: {', '.join(map(str, a['keys'][:8]))}). Reading this is cleaner than parsing the layout.")
            if page_count and abs(a["length"] - page_count) > max(2, page_count * 0.15):
                self.warn(f"The packed list has {a['length']} items but the page shows {page_count} - "
                          "they may not be the same list; check before relying on it.")
        elif rich:
            names = ", ".join(sorted({b["kind"] for b in rich})[:4])
            self.note(f"Data blocks with lists found ({names}), but none is clearly the result list "
                      "(they look like menus/links).")
        elif blocks:
            self.note(f"{len(blocks)} data blocks found, but none holds a list of items.")
        else:
            self.note("No packed data blocks - data has to come from the page layout or a feed.")

        # ---- the site's own count of results, and its search engine
        src = best or next((b for b in blocks if b.get("totals")), None)
        if src and src.get("totals"):
            t = src["totals"][0]
            ctx.facts["embedded_total"] = {"value": t["value"], "path": t["path"], "block": src["kind"]}
            self.note(f"The page's data says {t['value']:,} results exist ({src['kind']} {t['path']}).")
        if src and src.get("paging"):
            ctx.facts["embedded_paging"] = src["paging"]
            p = src["paging"]
            if p.get("nbpages") or p.get("totalpages"):
                self.note(f"Paging fields in the data: {', '.join(f'{k}={v}' for k, v in p.items())}.")
        eng = None
        for b in ([best] if best else []) + blocks:
            eng = b.get("backend")
            if eng:
                break
        if eng:
            ctx.facts["search_backend"] = {"name": eng, "evidence": "page data"}
            limit = datatools.KNOWN_LIMITS.get(eng)
            self.note(f"Search engine behind the site: {eng}." + (f" Known landmine - {limit}. "
                      "The reach check tests whether this site has it." if limit else ""))

        # unproven lists stay available: the location check can promote one if the
        # browser turns out to DISPLAY its values (field names don't matter then)
        ctx.scratch["embedded_candidates"] = [(b["kind"], b["_obj"], b["list"], b.get("app_state", False))
                                              for b in blocks if b.get("list")]
        for b in blocks:
            b.pop("_obj", None)
        return {"blocks": blocks, "microdata": micro, "opengraph_tags": og,
                "unreadable_frameworks": unreadable}

    def _describe(self, kind, raw, data, page_count, expect, size=None, app_state=False):
        hits = [e for e in expect if contains(raw, e)]
        tots = datatools.totals(data)
        strong = [t["path"] for t in tots if t["strong"]]
        lst = datatools.pick_listing_array(data, page_count, expect, strong)
        eng = datatools.backend_hint(data) or next((t["backend"] for t in tots if t["backend"]), None)
        # only paging fields next to the list count - a 'page' elsewhere (map widget...) misleads
        paging = datatools.list_paging(data, lst["path"]) if lst else {}
        return {"kind": kind, "bytes": size or len(raw), "parsed": True, "app_state": app_state,
                "top_keys": jsontools.top_keys(data),
                "list": lst, "totals": tots[:4], "paging": paging,
                "backend": eng, "expect_hits": hits, "_obj": data}
