"""Hidden data feeds: the behind-the-scenes requests the page makes for its
data. If one can be called directly (without a browser), it's the jackpot:
clean, fast, cheap data."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from .. import datatools, htmltools, jsontools
from ..htmltools import contains
from .base import Check, SkipCheck

TRACKERS = re.compile(
    r"google-analytics|googletagmanager|doubleclick|googlesyndication|facebook\.|fbcdn|"
    r"sentry|segment\.(io|com)|hotjar|clarity\.ms|mixpanel|amplitude|newrelic|nr-data|"
    r"datadoghq|intercom|zendesk|hubspot|optimizely|cookielaw|onetrust|cookiebot|"
    r"yandex\.(ru|com)/(metrika|watch)|mc\.yandex|tiktok|snapchat|bing\.com/|criteo|"
    r"recaptcha|hcaptcha|cloudflareinsights|/cdn-cgi/|stripe\.com|paypal|"
    # ad-tech / consent plumbing (22 Oct: an ad 'cookie_sync' call was taken for the data feed)
    r"cookie.?sync|user.?sync|usersync|prebid|pubmatic|adnxs|rubiconproject|openx|taboola|outbrain|"
    r"amazon-adsystem|adsystem|adservice|\.ay\.delivery|consent|cmp\.|trustarc|didomi|quantcast", re.I)
PAGING_PARAMS = {"page", "p", "offset", "start", "skip", "cursor", "after", "before",
                 "limit", "per_page", "perpage", "pagesize", "page_size", "size", "count",
                 "from", "pagenum", "pageindex", "first", "last"}
AUTH_HEADERS = re.compile(r"authorization|x-api-key|api-key|x-csrf|csrf|x-xsrf|token|x-auth|"
                          r"x-client|x-app|signature|x-requested-with", re.I)
MAX_REPLAY = 4


class HiddenFeeds(Check):
    name = "network"
    title = "Hidden data feeds"
    order = 50

    def run(self, ctx):
        out = {"api_hints_in_source": ctx.facts.get("api_hints") or []}
        r = ctx.rendered()
        if ctx.facts.get("browser_blocked"):
            raise SkipCheck("the browser was blocked too")
        if r is None or r.error:
            if out["api_hints_in_source"]:
                self.note(f"Browser not used, but the page source mentions "
                          f"{len(out['api_hints_in_source'])} data-feed-like URLs "
                          f"(e.g. {out['api_hints_in_source'][0]}). Run with --browser always to test them.")
                return out
            raise SkipCheck("browser was not used, no feed hints in source")

        page_total, page_ids_text = self._page_reference(ctx)
        shown = (r.text or "") + " " + htmltools.visible_text(ctx.soup("raw", page_ids_text)) if page_ids_text else (r.text or "")
        cands, telemetry = [], []
        for c in r.calls:
            if c.resource_type == "document" or not c.body or c.status >= 400:
                continue
            if TRACKERS.search(c.url) or c.size < 150:
                continue
            try:
                data = json.loads(c.body)
            except Exception:
                continue
            arrays = jsontools.item_arrays(data)
            sample_keys = arrays[0]["keys"] if arrays else jsontools.top_keys(data)
            if datatools.looks_like_telemetry(c.url, c.method, c.status, sample_keys):
                telemetry.append(_short(c.url, 70))   # the page REPORTING to the site - never replay
                continue
            score = (arrays[0]["length"] * 10 if arrays else 0) + min(c.size / 1000, 200)
            hits = [e for e in ctx.cfg.expect if contains(c.body, e)]
            score += 1000 * len(hits)
            if ctx.same_site(c.url):
                score *= 1.5
            q = parse_qs(urlsplit(c.url).query)
            paging = sorted(k for k in q if k.lower() in PAGING_PARAMS)
            engine = datatools.backend(c.url, c.post_data, data, c.request_headers)
            auth = sorted(h for h in c.request_headers if AUTH_HEADERS.search(h))
            role = self._role(data, arrays, page_total, page_ids_text, shown)
            if role["role"] in ("side list", "unrelated"):
                score *= 0.05          # promoted box / widget / config - never the main route
            elif role["role"] == "main list":
                score *= 3
            cands.append({"url": c.url, "method": c.method, "status": c.status, "bytes": c.size,
                          "phase": c.phase, "item_arrays": arrays, "expect_hits": hits,
                          "paging_params": paging, "engine": engine, "auth_like_headers": auth,
                          **role, "score": round(score, 1), "_call": c})
        cands.sort(key=lambda x: -x["score"])
        out["captured_data_calls"] = len(cands)
        # the same feed called for page 1, 2, 3... counts once (saves replay requests)
        grouped = {}
        for c in cands:
            u = urlsplit(c["url"])
            rest = sorted((k, v) for k, v in parse_qs(u.query).items() if k.lower() not in PAGING_PARAMS)
            key = (c["method"], u.netloc, u.path, str(rest))
            if key in grouped:
                grouped[key]["calls"] += 1
                if c["phase"] in ("scroll", "click"):
                    grouped[key]["phase"] = c["phase"]
            else:
                c["calls"] = 1
                grouped[key] = c
        cands = list(grouped.values())
        if not cands:
            self.note("The page made no useful data requests while loading - data is in the HTML itself.")
            ctx.facts["feeds"] = []
            return out

        for i, cand in enumerate(cands[:MAX_REPLAY]):
            if cand.get("role") == "side list":   # don't spend requests on a promoted box
                cand["replay"] = {"works": False, "how": None, "why": "side list - not replayed"}
            else:
                cand["replay"] = self._replay(ctx, cand)
            ctx.save(f"feed_{i + 1}.json", (cand["_call"].body or "")[:300_000])
        for c in cands:
            c.pop("_call", None)
        out["feeds"] = cands[:10]
        out["telemetry_skipped"] = telemetry
        # A feed is the data route only with EVIDENCE it carries the page's items:
        # its count matches the page's, its items appear on the page, or it holds the
        # user's --expect text. "It replays and returns JSON" is not evidence
        # (22 Oct: config files and ad calls were promoted to 'main feed').
        good = [c for c in cands[:MAX_REPLAY]
                if c.get("replay", {}).get("works") and c["item_arrays"]
                and (str(c.get("role", "")).startswith("main") or c["expect_hits"])]
        for c in cands[:MAX_REPLAY]:
            if c.get("replay", {}).get("works") and c not in good:
                c["not_used_because"] = ("no list of items in it" if not c["item_arrays"]
                                         else f"no evidence it carries the page's items ({c.get('role')})")
        ctx.facts["feeds"] = good
        ctx.facts["feeds_seen"] = cands[:MAX_REPLAY]

        for c in cands[:MAX_REPLAY]:
            arr = c["item_arrays"][0] if c["item_arrays"] else None
            desc = (f"{c['method']} {_short(c['url'])} - {c['bytes'] / 1024:.0f} KB"
                    + (f", called {c['calls']}x while loading/scrolling/clicking" if c["calls"] > 1 else "")
                    + (f", list of {arr['length']} at {arr['path']}" if arr else ""))
            rep = c.get("replay", {})
            how = "not replayed (side list)" if rep.get("why") == "side list - not replayed" else {"plain": "works with a plain request",
                   "with_headers": f"works when copying the browser's headers ({', '.join(c['auth_like_headers']) or 'standard'})",
                   }.get(rep.get("how"), f"does NOT work outside the browser ({rep.get('why', '?')})")
            self.note(f"Feed: {desc}; {how}."
                      + (f" Contains your text: {', '.join(repr(e) for e in c['expect_hits'])}."
                         if c["expect_hits"] else ""))
            if c["paging_params"]:
                self.note(f"  pages through results with: {', '.join(c['paging_params'])}.")
            if c.get("role_why"):
                self.note(f"  {c['role'].upper()}: {c['role_why']}.")
            if c.get("not_used_because"):
                self.note(f"  not counted as a data route: {c['not_used_because']}.")
            if c.get("engine"):
                lim = datatools.KNOWN_LIMITS.get(c["engine"])
                self.note(f"  answered by {c['engine']}" + (f" ({lim})" if lim else "") + ".")
                if c["engine"] == "GraphQL":
                    self.note("  GraphQL = one address, the question is sent in the request body.")
            if c["phase"] == "scroll":
                self.note("  loaded on scroll - this feed is what powers endless scrolling.")
            if c["phase"] == "click":
                self.note("  loaded when 'load more' was pressed - this feed is the button's data source.")
        if telemetry:
            self.note(f"Ignored {len(telemetry)} analytics/tracking request(s) (the page reporting views "
                      f"to the site, e.g. {telemetry[0]}) - not data, and never replayed.")
        if good:
            self.note("A direct feed for the main result list works - the scraper can skip the page layout.")
        elif any(c.get("role") == "side list" for c in cands[:MAX_REPLAY]):
            self.note("The feeds seen only carry side lists (promoted/recommended boxes), not the main results.")
        return out

    # ------------------------------------------------------------------
    def _page_reference(self, ctx):
        """The page's own count of results, and the raw page text (to look for feed item IDs)."""
        total = (ctx.facts.get("embedded_total") or {}).get("value")
        raw = ctx.main_page()
        text = raw.text if raw.ok else ""
        if not total and text:
            from .structure import text_total
            lst = ctx.facts.get("raw_listing")
            total = text_total(htmltools.visible_text(ctx.soup("raw", text)), lst["count"] if lst else 1)
        return total, text

    @staticmethod
    def _role(data, arrays, page_total, page_text, shown_text="") -> dict:
        """Is this feed THE result list, or a side box (promoted ads, 'similar items'),
        or unrelated (config, translations, ads)?
        Judged by its own result count vs the page's, then by whether its items'
        values are what the page shows, then by item-ID overlap."""
        tots = datatools.totals(data, limit=3)
        feed_total = tots[0]["value"] if tots else None
        out = {"feed_total": feed_total, "page_total": page_total}
        if feed_total and page_total:
            ratio = feed_total / page_total
            out["total_ratio"] = round(ratio, 3)
            if ratio < 0.2:
                return {**out, "role": "side list",
                        "role_why": f"it reports {feed_total:,} results while the page reports {page_total:,}"}
            if 0.8 <= ratio <= 1.25:
                return {**out, "role": "main list",
                        "role_why": f"its result count ({feed_total:,}) matches the page's ({page_total:,})"}
        if arrays and shown_text:
            items = datatools.get_path(data, arrays[0]["path"]) or []
            share = datatools.value_overlap(items, shown_text)
            if share is not None:
                out["values_on_page"] = round(share, 2)
                if share >= 0.5:
                    return {**out, "role": "main list (likely)",
                            "role_why": f"{share:.0%} of its items' values are shown on the page"}
                if share < 0.1:
                    return {**out, "role": "unrelated",
                            "role_why": f"only {share:.0%} of its items' values appear on the page - config/ads/other box"}
        if arrays and page_text:
            items = datatools.get_path(data, arrays[0]["path"]) or []
            ids = [i.split("=", 1)[1] for i in datatools.item_ids(items)]
            ids = [i for i in ids if len(i) >= 4]
            if ids:
                share = sum(1 for i in ids if i in page_text) / len(ids)
                out["ids_in_page"] = round(share, 2)
                if share >= 0.5:
                    return {**out, "role": "main list (likely)",
                            "role_why": f"{share:.0%} of its items also appear in the page"}
                return {**out, "role": "unclear",
                        "role_why": f"only {share:.0%} of its items appear in the first page - may be a side box"}
        return {**out, "role": "unclear", "role_why": ""}

    def _replay(self, ctx, cand) -> dict:
        call = cand["_call"]
        if not ctx.allowed(call.url):
            return {"works": False, "how": None, "why": "robots.txt disallows this address"}
        method, body = call.method, call.post_data
        if method not in ("GET", "POST"):
            return {"works": False, "why": f"{method} not replayed"}

        def attempt(headers):
            r = ctx.get(call.url, headers=headers, method=method, data=body,
                        use_cache=False, purpose="feed replay", escalate=False)
            if r.error or r.blocked or r.status >= 400:
                return False, f"HTTP {r.status or r.error}" + (f" ({r.blocked})" if r.blocked else "")
            data = r.json()
            if data is None:
                return False, "answer was not JSON"
            if jsontools.item_arrays(data) or len(r.text) > call.size * 0.5:
                return True, ""
            return False, "answer much smaller than in the browser"

        ok, why = attempt({"Accept": "application/json, text/plain, */*",
                           **({"Content-Type": call.request_headers.get("content-type", "application/json")}
                              if body else {})})
        if ok:
            return {"works": True, "how": "plain"}
        hdrs = {k: v for k, v in call.request_headers.items() if not k.startswith(":")}
        ok2, why2 = attempt(hdrs)
        if ok2:
            return {"works": True, "how": "with_headers"}
        why2 = why2 + "; probably needs the browser's cookies/session or a signed token"
        return {"works": False, "how": None, "why": why2}


def _short(url: str, n: int = 90) -> str:
    return url if len(url) <= n else url[: n - 3] + "..."
