"""Where does the data live: in the raw page, or only after the page runs its
own code in a browser? Decides plain scraping vs browser scraping."""
from __future__ import annotations

import re

from .. import datatools, htmltools, listing, signals
from .base import Check

SHELL_ROOT = re.compile(r'<div[^>]+id=["\'](root|app|__next|__nuxt|svelte|main-app)["\'][^>]*>\s*</div>', re.I)
API_HINT = re.compile(r"""["'`]((?:https?:)?//[^"'`\s]+/(?:api|graphql|v\d)/[^"'`\s]*|/(?:api|graphql)/?[^"'`\s]*)["'`]""")
THIN_TEXT = 800        # visible characters below this = "nearly empty page"


class DataLocation(Check):
    name = "location"
    title = "Where the data lives (raw page vs browser)"
    order = 40

    def run(self, ctx):
        cfg = ctx.cfg
        raw = ctx.main_page()
        blocked_plain = bool(ctx.facts.get("blocked_at_start")) or not raw.ok
        out = {"expect": {}}

        raw_text, raw_listing = "", None
        if not blocked_plain:
            soup = ctx.soup("raw", raw.text)
            raw_text = htmltools.visible_text(soup)
            raw_listing = listing.find_listing(soup)
            script_bytes = sum(len(s.string or "") for s in soup.find_all("script"))
            out["raw"] = {"visible_chars": len(raw_text), "html_bytes": raw.size,
                          "script_share": round(script_bytes / max(raw.size, 1), 2),
                          "empty_app_shell": bool(SHELL_ROOT.search(raw.text)),
                          "listing": _public(raw_listing)}
            ctx.facts["raw_listing"] = raw_listing
            hints = sorted(set(m.group(1) for m in API_HINT.finditer(raw.text[:600_000])))[:15]
            out["api_hints_in_source"] = hints
            ctx.facts["api_hints"] = hints

        # ----- expected snippets in the raw page
        raw_html = raw.text if not blocked_plain else ""
        hay_text = (htmltools.norm(raw_text), htmltools.norm_num(raw_text))
        hay_html = (htmltools.norm(raw_html), htmltools.norm_num(raw_html))
        for e in cfg.expect:
            where = []
            if htmltools.has_text(*hay_text, e):
                where.append("raw page text")
            elif htmltools.has_text(*hay_html, e):
                where.append("raw page source (inside scripts/attributes)")
            out["expect"][e] = where

        # ----- decide whether a browser load is needed (plain first, browser only if useful)
        reasons = []
        if cfg.browser == "always":
            reasons.append("--browser always")
        if blocked_plain:
            reasons.append("plain request blocked")
        if not blocked_plain and len(raw_text) < THIN_TEXT:
            reasons.append("raw page nearly empty")
        if not blocked_plain and out["raw"]["empty_app_shell"]:
            reasons.append("empty app shell")
        if any(not w for w in out["expect"].values()):
            reasons.append("expected text missing from raw page")
        if not blocked_plain and raw_listing is None and not ctx.facts.get("embedded_has_items"):
            reasons.append("no item list found in raw page")
        out["browser_reasons"] = reasons

        rendered = None
        # 28 Oct (Daraz): the browser is the heavy part - a real browser visit got the
        # connection blocked and every plain test after it measured the block page.
        # When plain requests already deliver a real page and the browser is only
        # wanted to look for missing expected text, it waits for its own room at the
        # END of the run (BrowserRoom), so it can never spoil the plain-request tests.
        deferrable = (cfg.browser == "auto" and not blocked_plain
                      and reasons == ["expected text missing from raw page"]
                      and (raw_listing or ctx.facts.get("embedded_has_items") or len(raw_text) >= THIN_TEXT))
        if deferrable:
            ctx.scratch["browser_room"] = {"out": out, "raw_text": raw_text, "raw_listing": raw_listing}
            self.note("The raw page has real content, only some expected text is missing - the browser check "
                      "runs last, in its own room, so a browser-triggered block can't spoil the plain tests.")
        elif reasons and cfg.browser == "never":
            self.warn(f"A browser check would help ({', '.join(reasons)}) but it is turned off "
                      "(--browser never).")
        elif reasons:
            rendered = ctx.render(allow_when_blocked=blocked_plain)
            if rendered is None:
                why = ctx.facts.get("browser_unavailable") or ctx.facts.get("browser_refused")
                self.warn(f"Wanted a browser check ({', '.join(reasons)}) but could not run it: {why}")
        else:
            self.note("Raw page already has the content - no browser needed (pass --browser always "
                      "to also hunt for hidden data feeds).")

        if rendered is not None:
            out.update(self._compare(ctx, rendered, raw_text, raw_listing, blocked_plain))

        # ----- verdict
        needs = self._verdict(ctx, out, raw_text, raw_listing, rendered, blocked_plain)
        ctx.facts["needs_browser"] = needs
        out["needs_browser"] = needs
        return out

    # ------------------------------------------------------------------
    def _compare(self, ctx, r, raw_text, raw_listing, blocked_plain):
        res = {}
        if r.error:
            self.warn(f"Browser load failed: {r.error}")
            res["browser_error"] = r.error
            return res
        ctx.save("rendered_main.html", r.html)
        block = signals.looks_blocked(r.status, r.html, len(r.html))
        if not block and not blocked_plain and len(raw_text) >= THIN_TEXT and signals.is_thin(r.html):
            # 28 Oct: a browser shows at least what the raw page has. An empty browser page next to a
            # rich raw page means the browser was turned away (a guard's hop away, an empty deny page)
            block = "browser got an empty page where plain requests got real content"
        res["browser"] = {"status": r.status, "visible_chars": len(r.text),
                          "load_ms": round(r.load_ms), "blocked": block,
                          "data_calls": sum(1 for c in r.calls if c.body),
                          "scroll": r.scroll, "cookies": r.cookies}
        guards = signals.guards_from_cookies(r.cookies)
        if guards:
            ctx.facts["guards"] = sorted(set(ctx.facts.get("guards") or []) | guards)
        if block:
            ctx.facts["browser_blocked"] = block
            self.warn(f"Even the real browser was blocked ({block}).")
            return res
        if r.challenge_wait_s:
            ctx.facts["challenge_solved_in_browser"] = r.challenge_wait_s
            self.note(f"A 'checking your browser' page cleared by itself after ~{r.challenge_wait_s} s.")
        if blocked_plain:
            ctx.facts["browser_passes_block"] = True
            self.note("Plain requests are blocked but a real browser gets in.")
            cookie = ctx.browser_cookies()
            if cookie:
                again = ctx.get(ctx.url, headers={"Cookie": cookie}, use_cache=False,
                                allow_when_blocked=True, escalate=False, purpose="cookie reuse")
                works = again.ok
                ctx.facts["cookie_reuse_works"] = works
                res["cookie_reuse"] = {"status": again.status, "works": works}
                if works:
                    self.note("Plain requests WORK when they carry the browser's cookies - use the browser "
                              "only to collect cookies, then scrape with fast plain requests "
                              "(cookies expire: refresh them when blocks return).")
                else:
                    self.note(f"Reusing the browser's cookies in plain requests does not work "
                              f"(HTTP {again.status}) - every page needs the real browser.")

        if not blocked_plain and not ctx.facts.get("embedded_has_items"):
            self._promote_packed_list(ctx, r.text)
        soup = ctx.soup("rendered", r.html)
        rend_listing = listing.find_listing(soup)
        ctx.facts["rendered_listing"] = rend_listing
        res["browser"]["listing"] = _public(rend_listing)
        rend_hay = (htmltools.norm(r.text), htmltools.norm_num(r.text))
        for e in ctx.cfg.expect:
            if htmltools.has_text(*rend_hay, e):
                res.setdefault("expect_rendered", []).append(e)
        ratio = len(r.text) / max(len(raw_text), 1)
        res["text_ratio"] = round(ratio, 2)
        ctx.facts["text_ratio"] = ratio
        if not blocked_plain:
            self.note(f"Browser shows {len(r.text):,} visible characters vs {len(raw_text):,} in the "
                      f"raw page (x{ratio:.1f}).")
        if not blocked_plain and rend_listing and (
                raw_listing is None or rend_listing["count"] > raw_listing["count"] * 1.5):
            self.note(f"The item list ({rend_listing['count']} items) only fully appears in the browser.")
        return res

    def _promote_packed_list(self, ctx, shown_text):
        """A list packed in the raw page IS the data if the browser displays its values -
        whatever its field names (22 Oct: quotes.toscrape /js/ keeps quotes in
        `var data = [...]` with text/author fields, which the shop-shaped picker ignored)."""
        best = None
        for kind, obj, lst, app_state in ctx.scratch.get("embedded_candidates") or []:
            items = datatools.get_path(obj, lst["path"]) or []
            share = datatools.value_overlap(items, shown_text)
            if share is not None and share >= 0.5 and lst["length"] >= 3 and (best is None or share > best[0]):
                best = (share, kind, obj, lst, app_state)
        if not best:
            return
        share, kind, obj, lst, app_state = best
        ctx.facts["embedded_has_items"] = True
        ctx.facts["embedded_list"] = {"block": kind, "path": lst["path"], "length": lst["length"],
                                      "keys": lst["keys"], "app_state": app_state,
                                      "evidence": f"the browser shows {share:.0%} of its items"}
        ctx.facts["page1_ids"] = datatools.item_ids(datatools.get_path(obj, lst["path"]) or [])
        self.note(f"The raw page already carries the data: '{kind}' {lst['path']} ({lst['length']} items) - "
                  f"the browser only displays it ({share:.0%} of its items are shown). Read the packed list, "
                  "no browser needed.")

    def _verdict(self, ctx, out, raw_text, raw_listing, rendered, blocked_plain):
        expect = out["expect"]
        rend_hits = set(out.get("expect_rendered", []))
        if blocked_plain:
            if ctx.facts.get("browser_passes_block"):
                return True
            return None   # unknown: we never saw the content
        if ctx.facts.get("embedded_has_items") and not [e for e, w in expect.items() if not w]:
            return False  # the data is packed in the raw page - the browser only paints it
        if expect:
            missing_raw = [e for e, w in expect.items() if not w]
            for e, w in expect.items():
                if w:
                    self.note(f"'{e}' found in the {w[0]} - plain requests can get it.")
                elif e in rend_hits:
                    self.note(f"'{e}' only appears after the browser runs the page's code.")
                elif ctx.facts.get("browser_blocked"):
                    self.warn(f"'{e}' is not in the raw page, and the browser was blocked - still unknown "
                              "whether it appears once the page runs its code.")
                elif rendered is not None:
                    self.warn(f"'{e}' not found anywhere (raw or browser). Check spelling, or the "
                              "data is on another page / behind interaction.")
            if not missing_raw:
                return False
            if ctx.facts.get("browser_blocked"):
                # never turn "the browser was blocked" into "the data needs a browser" or "it isn't there"
                return False if len(missing_raw) < len(expect) else None
            if missing_raw and any(e in rend_hits for e in missing_raw):
                return True
        if rendered is None:
            if raw_listing or ctx.facts.get("embedded_has_items"):
                return False   # short page, but the item list is right there in the raw HTML
            if len(raw_text) < THIN_TEXT or out.get("raw", {}).get("empty_app_shell"):
                self.warn("Raw page is nearly empty and the browser could not be used - "
                          "data probably needs a browser or a hidden feed.")
                return None
            return False
        ratio = ctx.facts.get("text_ratio", 1)
        rl = ctx.facts.get("rendered_listing")
        if len(raw_text) < THIN_TEXT and len(rendered.text) > THIN_TEXT:
            return True
        if rl and (raw_listing is None or rl["count"] > raw_listing["count"] * 1.5):
            return True
        if ratio > 1.5:
            self.note("Part of the content is added by the page's code - check whether that part matters.")
            return "partly"
        return False


class BrowserRoom(Check):
    """The heavy part's own room: a browser load that DataLocation postponed so the
    plain-request tests (paging, pace) ran first on a clean connection."""
    name = "browser_room"
    title = "Browser check (run last, in its own room)"
    order = 95

    def should_run(self, ctx):
        if not ctx.scratch.get("browser_room"):
            return "nothing was waiting for the browser"
        return None

    def run(self, ctx):
        d = ctx.scratch.pop("browser_room")
        loc, out = DataLocation(), d["out"]
        rendered = ctx.render()
        if rendered is None:
            why = ctx.facts.get("browser_unavailable") or ctx.facts.get("browser_refused")
            self.warn(f"Wanted a browser check (expected text missing from raw page) but could not run it: {why}")
        else:
            out.update(loc._compare(ctx, rendered, d["raw_text"], d["raw_listing"], False))
        needs = loc._verdict(ctx, out, d["raw_text"], d["raw_listing"], rendered, False)
        ctx.facts["needs_browser"] = needs
        out["needs_browser"] = needs
        self.notes += loc.notes
        self.warnings += loc.warnings
        return out


def _public(lst):
    if not lst:
        return None
    return {k: v for k, v in lst.items() if not k.startswith("_")}
