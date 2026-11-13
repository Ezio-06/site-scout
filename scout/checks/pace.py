"""Pace check: polite, spaced-out visits that confirm the site stays normal.

Not a stress test. Default: ~10 visits, one every 6 s, spread over a few of
the site's own pages. It stops at the first warning sign. --pace-fast
(opt-in) then steps the pace up gradually to find where trouble starts.
"""
from __future__ import annotations

import statistics

from .. import signals
from .base import Check

POOL_SIZE = 5


class Pace(Check):
    name = "pace"
    title = "Pace check (does the site stay normal?)"
    order = 90

    def should_run(self, ctx):
        cfg, f = ctx.cfg, ctx.facts
        if not cfg.pace:
            return "turned off (--no-pace)"
        if ctx.fetcher.blocked:
            return f"site already pushed back ({ctx.fetcher.blocked})"
        if f.get("robots_allowed") is False:
            return "robots.txt disallows the start page"
        if (f.get("crawl_delay") or 0) > 30:
            return f"site asks for {f['crawl_delay']:g} s between visits - that IS the limit"
        if not ctx.main_page().ok:
            return "start page not available to plain requests"
        if ctx.fetcher.remaining() < 5:
            return "request budget nearly used up"
        return None

    # -------------------------------------------------------------------
    def _pool(self, ctx) -> list:
        # if a direct feed works, that is what the scraper will hit - pace-test it first
        feeds = [f["url"] for f in (ctx.facts.get("feeds") or [])
                 if f.get("method") == "GET" and f["replay"]["how"] == "plain" and ctx.allowed(f["url"])]
        urls = feeds[:2] + [ctx.url]
        for u in (ctx.facts.get("page_links") or []) + [ctx.facts.get("next_link")]:
            if u and u not in urls and ctx.same_site(u) and ctx.allowed(u):
                urls.append(u)
        lst = ctx.facts.get("raw_listing")
        if lst and len(urls) < POOL_SIZE:
            for m in lst.get("_members", [])[:10]:
                a = m if m.name == "a" else m.find("a", href=True)
                if a and a.get("href"):
                    u = ctx.abs(a["href"])
                    if u not in urls and ctx.same_site(u) and ctx.allowed(u):
                        urls.append(u)
                if len(urls) >= POOL_SIZE:
                    break
        return urls[:POOL_SIZE]

    def _sequence(self, ctx, pool, interval, n, state, label) -> str | None:
        """Run n requests at `interval`. Return a warning string or None."""
        slow_streak = 0
        for _ in range(n):
            if ctx.fetcher.remaining() <= 1 or ctx.fetcher.time_left() < interval + 5:
                state["cut_short"] = "budget/time limit"
                return None
            url = pool[state["i"] % len(pool)]
            state["i"] += 1
            r = ctx.get(url, delay=interval, use_cache=False, purpose=f"pace {label}")
            state["log"].append({"interval": interval, "status": r.status, "bytes": r.size,
                                 "ms": round(r.elapsed_ms), "url": url})
            if r.error:
                return f"request failed: {r.error}"
            rh = signals.rate_headers(r.headers)
            if rh:
                state["rate_headers"].update(rh)
            if r.blocked:
                return f"{r.blocked} after {len(state['log'])} pace requests"
            if "retry-after" in {k.lower() for k in r.headers}:
                return f"site sent Retry-After ({rh.get('retry-after')})"
            rem = rh.get("x-ratelimit-remaining") or rh.get("ratelimit-remaining") or \
                rh.get("x-rate-limit-remaining")
            if rem is not None and str(rem).strip().isdigit() and int(rem) <= 2:
                return f"rate-limit allowance nearly used up (remaining={rem})"
            first = state["sizes"].setdefault(url, r.size)
            if first > 5000 and r.size < first * 0.3:
                return f"page came back {r.size} bytes vs {first} before (likely a soft block)"
            state["lat"].append(r.elapsed_ms)
            base = statistics.median(state["lat"][:3]) if len(state["lat"]) >= 3 else None
            if base and r.elapsed_ms > max(4 * base, 2000):
                slow_streak += 1
                if slow_streak >= 2:
                    return f"replies slowed sharply ({r.elapsed_ms:.0f} ms vs ~{base:.0f} ms normal)"
            else:
                slow_streak = 0
        return None

    def run(self, ctx):
        cfg = ctx.cfg
        interval = max(cfg.pace_interval, ctx.facts.get("crawl_delay") or 0, cfg.min_delay)
        pool = self._pool(ctx)
        state = {"i": 0, "log": [], "rate_headers": {}, "sizes": {}, "lat": []}
        main = ctx.main_page()
        if main.ok:   # judge shrinkage against the good start page, not the first pace answer
            state["sizes"][ctx.url] = main.size
        n = min(cfg.pace_requests, ctx.fetcher.remaining() - 1)
        self.note(f"Visiting {len(pool)} of the site's pages, 1 request every {interval:g} s, "
                  f"{n} requests (~{n * interval:.0f} s).")
        warning = self._sequence(ctx, pool, interval, n, state, "gentle")
        clean_interval = None if warning else interval
        tested = [{"interval": interval, "requests": n, "warning": warning}]

        if not warning and cfg.pace_fast:
            for step in cfg.pace_fast_steps:
                if step >= interval:
                    continue
                k = min(cfg.pace_fast_per_step, ctx.fetcher.remaining() - 1)
                if k < 3:
                    break
                w = self._sequence(ctx, pool, step, k, state, f"fast {step:g}s")
                tested.append({"interval": step, "requests": k, "warning": w})
                if w:
                    warning = w
                    break
                clean_interval = step

        dur = sum(e["interval"] for e in state["log"])
        out = {"pool": pool, "tested": tested, "clean_interval": clean_interval,
               "warning": warning, "rate_headers": state["rate_headers"],
               "duration_s": round(dur), "requests": len(state["log"]),
               "latency_ms_median": round(statistics.median(state["lat"])) if state["lat"] else None,
               "cut_short": state.get("cut_short")}
        ctx.facts["pace"] = out
        if state["rate_headers"]:
            ctx.facts.setdefault("rate_headers", {}).update(state["rate_headers"])
            self.note(f"Site states its limits in replies: {state['rate_headers']}.")

        if clean_interval:
            per_min = 60 / clean_interval
            self.note(f"No trouble up to 1 request per {clean_interval:g} s (~{per_min:.0f}/min) over "
                      f"{len(state['log'])} requests / {dur:.0f} s. Short test from one connection - "
                      "treat it as 'fine so far', not a guarantee.")
            if not cfg.pace_fast:
                self.note("Faster paces not tested (add --pace-fast to step up carefully).")
        allowance = bool(warning) and warning.startswith("rate-limit allowance")
        out["stopped_for_allowance"] = allowance
        if allowance:
            lim = (state["rate_headers"].get("x-ratelimit-limit") or state["rate_headers"].get("ratelimit-limit")
                   or state["rate_headers"].get("x-rate-limit-limit") or "?")
            out["stated_limit"] = lim
            self.warn(f"Stopped on purpose before using up the site's stated allowance (limit {lim} per "
                      f"window, {len(state['log'])} pace requests made without any block). The site's own "
                      "number IS the limit: the scraper must stay under it. Window length: check the "
                      "*-reset header in report.json.")
        elif warning:
            self.warn(f"Warning sign during pace check: {warning}. Last pace with no trouble: "
                      + (f"1 per {clean_interval:g} s." if clean_interval else "none - even the gentle pace "
                         "tripped it. Treat the site as strict."))
        recommended = (clean_interval * 2 if cfg.pace_fast and clean_interval else clean_interval)
        if recommended:
            out["recommended_interval"] = recommended
            self.note(f"Suggested scraper pace: 1 request per {recommended:g} s"
                      + (" (2x safety margin over the fastest clean pace)." if cfg.pace_fast else
                         " (the tested gentle pace)."))
        return out
