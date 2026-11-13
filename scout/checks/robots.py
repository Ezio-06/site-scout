"""robots.txt: what the site allows bots to visit, and how slowly."""
from __future__ import annotations

from .base import Check
from ..config import BROWSER_HEADERS
from ..context import BOT_TOKEN

try:
    # Scrapy's robots.txt parser: understands wildcards (*, $) and longest-match rules.
    # Python's built-in parser does not do this consistently across versions.
    from protego import Protego
except ImportError:  # pragma: no cover
    Protego = None

CRAWL_DELAY_CAP = 30.0   # we honour crawl-delay up to this; above it we warn


class Robots(Check):
    name = "robots"
    title = "Bot rules (robots.txt)"
    order = 10

    def run(self, ctx):
        url = ctx.origin + "/robots.txt"
        r = ctx.get(url, purpose="robots.txt")
        if (r.status in (401, 403) or (r.blocked and r.status != 404)) and ctx.fetcher.identity == "honest"                 and ctx.cfg.identity == "honest":
            # robots.txt is unknown for our honest name. Before ANY request under another identity
            # (the start-page comparison), read the rules the browser identity is shown, and obey them.
            r2 = ctx.get(url, purpose="robots.txt (browser identity)", use_cache=False, headers=BROWSER_HEADERS,
                         identity="browser", allow_when_blocked=True, escalate=False)
            if r2.error is None and r2.status < 400 and not r2.blocked:
                r = r2
        out = {"url": url, "status": r.status, "found": False}
        if r.error:
            self.warn(f"Could not read robots.txt ({r.error}).")
            return out

        if r.status >= 500 or r.status == 429:
            # convention: server error on robots.txt means "assume everything is off-limits"
            out["assume_disallowed"] = True
            ctx.facts["robots_allowed"] = False
            self.warn(f"robots.txt answered {r.status}: by convention treat the whole site as off-limits for now.")
            return out
        if r.status >= 400 or r.blocked or "<html" in r.text[:500].lower():
            self.note("No robots.txt rules (missing) - no stated restrictions.")
            ctx.facts["robots_allowed"] = True
            return out

        ctx.save("robots.txt", r.text)
        rp = _Rules(r.text)
        ctx.robots = rp
        if not rp.wildcard_safe:
            self.warn("protego not installed - falling back to Python's basic robots.txt reader, "
                      "which can misread wildcard rules. Run: pip install protego")
        out["found"] = True

        allowed = ctx.allowed(ctx.url)   # our own name AND the wildcard; the stricter wins
        ctx.facts["robots_allowed"] = allowed
        out["start_url_allowed"] = allowed
        if allowed:
            self.note("The start page is allowed for bots.")
        else:
            ctx.facts["rules_forbid_start"] = True
            self.warn("robots.txt DISALLOWS the start page for bots. Scraping it goes against the "
                      "site's stated rules - discuss with the client before taking the job.")

        delay = 0
        for agent in (BOT_TOKEN, "*"):
            delay = max(delay or 0, rp.crawl_delay(agent) or 0)
            rate = rp.request_rate(agent)
            if rate:
                delay = max(delay, rate.seconds / max(rate.requests, 1))
        if delay:
            out["crawl_delay"] = float(delay)
            ctx.facts["crawl_delay"] = float(delay)
            self.note(f"Site asks bots to wait {delay:g} s between visits (crawl-delay).")
            if delay > ctx.cfg.min_delay:
                ctx.cfg.min_delay = min(float(delay), CRAWL_DELAY_CAP)
                self.note(f"Scout slowed itself to 1 request per {ctx.cfg.min_delay:g} s to respect it.")
            if delay > CRAWL_DELAY_CAP:
                self.warn(f"Crawl-delay of {delay:g} s is very strict: a scraper honouring it gets "
                          f"at most {3600 / delay:.0f} pages per hour. Price and plan around this.")

        sitemaps = rp.site_maps()
        out["sitemaps"] = sitemaps
        ctx.facts["sitemaps"] = sitemaps

        lines = [ln.strip() for ln in r.text.splitlines() if ln.strip() and not ln.startswith("#")]
        agents = sorted({ln.split(":", 1)[1].strip() for ln in lines
                         if ln.lower().startswith("user-agent:")})
        out["user_agents_named"] = agents[:30]
        out["disallow_rules"] = sum(1 for ln in lines if ln.lower().startswith("disallow:"))
        named_bots = [a for a in agents if a != "*"]
        if named_bots:
            self.note(f"Rules name {len(named_bots)} specific bots (e.g. {', '.join(named_bots[:4])}).")
        return out


class _Rules:
    """One interface over protego (preferred) or the stdlib parser (fallback)."""

    def __init__(self, text: str):
        self.wildcard_safe = Protego is not None
        if self.wildcard_safe:
            self._p = Protego.parse(text)
        else:
            from urllib.robotparser import RobotFileParser
            self._p = RobotFileParser()
            self._p.parse(text.splitlines())
            self._p.modified()

    def can_fetch(self, agent: str, url: str) -> bool:
        if self.wildcard_safe:
            return self._p.can_fetch(url, agent)
        return self._p.can_fetch(agent, url)

    def crawl_delay(self, agent: str):
        return self._p.crawl_delay(agent)

    def request_rate(self, agent: str):
        return self._p.request_rate(agent)

    def site_maps(self) -> list:
        if self.wildcard_safe:
            return list(self._p.sitemaps)
        return list(self._p.site_maps() or [])
