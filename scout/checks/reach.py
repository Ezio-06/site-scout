"""Reach: can a scraper actually get to ALL the items, page by page?

Claims about paging are worthless until tested (lessons: Daraz page 2 returned
page 1 again; Zameen stops serving pages somewhere past ~17,500 results). This
check FETCHES pages and judges each one:

  1. page 2           - different items from page 1?
  2. the last page    - does it still serve real results?
  3. if not, narrow down where pages stop working (halving the gap each time)

A page only counts as working if it has items, they are not a repeat of
another page, and - when the site says so - its range text ("26 to 50 of ...")
or its page number in the data matches the page that was asked for. Sites
often answer bad deep pages with a 200 'fallback' page, so status codes alone
prove nothing.
"""
from __future__ import annotations

import math
import socket

from .. import datatools, htmltools, listing
from .. import paging as pg
from .base import Check

MAX_PROBES = 10          # page fetches this check may spend
KEEP_FOR_LATER = 12      # leave this many requests for the pace check
REPEAT_SHARE = 0.6       # this share of identical items = "same page again"
MIN_GAP = 3.0           # seconds between reach fetches (gentler than the scout minimum)


class Reach(Check):
    name = "reach"
    title = "Paging and reach (tested)"
    order = 62

    def should_run(self, ctx):
        f = ctx.facts
        if not ctx.main_page().ok:
            return "start page not available to plain requests (browser paging is a v2 item)"
        if f.get("needs_browser") is True:
            return "items only appear in a browser - plain page fetches can't judge paging (v2 item)"
        if not f.get("page_template"):
            return "no address for page 2 was found on the page"
        if ctx.fetcher.blocked:
            return f"site already pushed back ({ctx.fetcher.blocked})"
        if ctx.fetcher.remaining() < 4:
            return "request budget nearly used up"
        return None

    def run(self, ctx):
        f = ctx.facts
        tpl = f["page_template"]
        per = f.get("items_per_page") or (f.get("embedded_list") or {}).get("length")
        total = f.get("total_items")
        last = math.ceil(total / per) if total and per else None
        budget = max(2, min(MAX_PROBES, ctx.fetcher.remaining() - KEEP_FOR_LATER))
        self._seen = {}            # page -> ids
        self._page1_field = None
        out = {"pattern": tpl["pattern"], "per_page": per, "site_total": total, "last_page": last,
               "ran_from": socket.gethostname(), "probes": []}

        p1 = self._read(ctx, ctx.main_page().text)
        self._seen[1] = p1["ids"]
        self._page1_field = p1["page_field"]
        if not p1["ids"]:
            self.warn("Could not tell items apart on page 1 (no IDs or links) - repeats can't be detected; "
                      "results below rely on range text / page numbers only.")

        def probe(n):
            res = self._probe(ctx, tpl, n, per, last)
            out["probes"].append(res)
            return res

        # ---- 1. page 2
        r2 = probe(2)
        if r2["verdict"] in ("inconclusive", "stopped"):
            f["reach"] = {"page2_ok": None, "verdict": r2["verdict"], "evidence": "unknown"}
            self.warn(f"Page 2 could not be judged: {r2['why']}. Paging is UNTESTED, not broken - "
                      "re-test slower (the site may be rate-limiting).")
            out["page2_ok"] = None
            return out
        if r2["verdict"] != "ok":
            f["reach"] = {"page2_ok": False, "verdict": r2["verdict"], "evidence": "verified"}
            self.warn(f"Page 2 does NOT work: {r2['why']}. Paging as the page advertises it is broken - "
                      "the scraper needs another route (feed paging, filters, sort orders).")
            self.note(f"Page 2 tested at {r2['url']}: {r2['why']}.")
            out["page2_ok"] = False
            return out
        self.note(f"Page 2 works: {r2['why']} ({tpl['pattern'].replace('{n}', str(tpl['first'] + tpl['step']))}).")
        out["page2_ok"] = True

        if not last or last <= 2:
            f["reach"] = {"page2_ok": True, "evidence": "verified",
                          "limit": None if not last else "all"}
            if not last:
                self.note("Site total unknown, so the deepest page could not be tested - "
                          "the collector must stop at the first empty/repeated page.")
            return out

        # ---- 2. the last page
        used = 1
        rl = probe(last)
        used += 1
        if rl["verdict"] == "ok":
            f["reach"] = {"page2_ok": True, "last_ok": True, "reachable_items": total,
                          "share": 1.0, "evidence": "verified"}
            self.note(f"The last page (page {last:,}) still serves real results - every item is "
                      "reachable by paging.")
            return out

        # ---- 3. narrow down where pages stop working
        lo, hi = 2, last
        while hi - lo > max(1, last // 100) and used < budget:
            mid = (lo + hi) // 2
            rm = probe(mid)
            used += 1
            if rm["verdict"] == "ok":
                lo = mid
            else:
                hi = mid
                if rm["verdict"] == "stopped":
                    break
        reach_items = lo * per
        share = reach_items / total
        exact = hi - lo <= 1
        f["reach"] = {"page2_ok": True, "last_ok": False, "last_ok_page": lo, "first_bad_page": hi,
                      "reachable_items": reach_items, "share": round(share, 3),
                      "evidence": "verified", "exact": exact}
        out.update(f["reach"])
        where = f"page {lo:,} works, page {hi:,} doesn't" if not exact else f"page {lo:,} is the last that works"
        self.warn(f"Paging stops early: {where} - only about {reach_items:,} of {total:,} items "
                  f"({share:.0%}) are reachable by paging alone. The last page answers: {rl['why']}.")
        eng = (f.get("search_backend") or {}).get("name")
        for cap in (1_000, 10_000):
            if lo * per <= cap <= hi * per:
                self.note(f"The cut-off sits at {cap:,} results - the usual default limit of "
                          f"{eng or 'Algolia/Elasticsearch-style search engines'}.")
        slices = math.ceil(total / (reach_items * 0.8))
        self.note(f"Plan: split the job into slices the site offers (sub-areas, price ranges, categories, "
                  f"sort orders) so each slice has under ~{int(reach_items * 0.8):,} results - at least "
                  f"{slices} slices. The collector must check each slice reaches its own last page.")
        return out

    # ------------------------------------------------------------------
    def _probe(self, ctx, tpl, n, per, last) -> dict:
        url = pg.url_for(tpl, n)
        res = {"page": n, "url": url}
        if not ctx.allowed(url):
            return {**res, "verdict": "robots", "why": "robots.txt disallows this page"}
        r = ctx.get(url, purpose=f"reach p{n}", use_cache=False, escalate=False,
                    delay=max(ctx.cfg.min_delay, MIN_GAP))
        res.update(status=r.status, bytes=r.size)
        if r.error:
            stop = "budget" in r.error or "limit" in r.error or "pushed back" in r.error
            return {**res, "verdict": "stopped" if stop else "inconclusive", "why": r.error}
        if r.blocked and not r.blocked.startswith("HTTP 4"):
            ctx.fetcher.note_block(r.blocked)
            return {**res, "verdict": "stopped", "why": f"site pushed back ({r.blocked})"}
        # Only "this address does not exist" proves the paging route wrong. Any other
        # error is the SITE talking (slow down / not for you / broken server), not the
        # paging (22 Oct: Hacker News answered 419 'too fast' and paging was called broken).
        if r.status in (404, 410):
            return {**res, "verdict": "error", "why": f"HTTP {r.status} - that page address does not exist"}
        if r.status >= 400:
            return {**res, "verdict": "inconclusive",
                    "why": f"HTTP {r.status} - the site refused or failed, which says nothing about paging"}
        page = self._read(ctx, r.text)
        res.update(items=page["count"], range=page["range"], page_field=page["page_field"])

        if page["count"] == 0:
            return {**res, "verdict": "empty", "why": f"HTTP {r.status} but no items (error or 'no results' page)"}
        # a repeat of a page we already have = the site ignored the page number
        for other, ids in self._seen.items():
            if ids and page["ids"] and other != n:
                share = len(set(ids) & set(page["ids"])) / max(1, len(set(page["ids"])))
                if share >= REPEAT_SHARE:
                    return {**res, "verdict": "repeat", "why": f"{share:.0%} the same items as page {other}"}
        range_ok = False
        if page["range"] and per:
            expected = (n - 1) * per + 1
            if abs(page["range"][0] - expected) > per:
                return {**res, "verdict": "wrong page",
                        "why": f"says it shows {page['range'][0]:,}-{page['range'][1]:,}, expected from {expected:,}"}
            range_ok = True
        # the page number in the data is weaker evidence than the visible range text
        if not range_ok and page["page_field"] is not None and self._page1_field is not None:
            want = self._page1_field + (n - 1)
            if page["page_field"] != want:
                return {**res, "verdict": "wrong page",
                        "why": f"its data says page={page['page_field']}, expected {want}"}
        if per and page["count"] < per * 0.5 and last and n < last:
            return {**res, "verdict": "short", "why": f"only {page['count']} items (pages hold {per})"}
        self._seen[n] = page["ids"]
        bits = [f"{page['count']} items"]
        if page["range"]:
            bits.append(f"shows {page['range'][0]:,}-{page['range'][1]:,}")
        if page["ids"]:
            bits.append("none repeated")
        return {**res, "verdict": "ok", "why": ", ".join(bits)}

    def _read(self, ctx, html: str) -> dict:
        """Item identities, item count, range text and data page number of one page."""
        out = {"ids": [], "count": 0, "range": None, "page_field": None}
        try:
            soup = htmltools.parse(html)
        except Exception:
            return out
        emb = ctx.facts.get("embedded_list")
        if emb:
            try:
                blobs, _ = datatools.state_blobs(soup) if emb.get("app_state") else ([], [])
                for name, obj, _size in blobs:
                    if name != emb["block"]:
                        continue
                    items = datatools.get_path(obj, emb["path"])
                    if isinstance(items, list):
                        out["count"] = len(items)
                        out["ids"] = datatools.item_ids(items)
                        pf = datatools.list_paging(obj, emb["path"])
                        out["page_field"] = pf.get("page", pf.get("currentpage"))
                    break
            except Exception:
                pass
        if not out["count"]:
            lst = listing.find_listing(soup)
            if lst:
                out["count"] = lst["count"]
                ids = []
                for m in lst.get("_members", []):
                    a = m if m.name == "a" else m.find("a", href=True)
                    if a is not None and a.get("href"):
                        ids.append("link=" + a["href"].split("#")[0])
                out["ids"] = ids
        out["range"] = pg.range_text(htmltools.visible_text(soup)[:200_000])
        return out
