"""Shared state handed to every check: config, the polite fetcher, the lazy
browser, parsed-page cache, saved snapshots, and `facts` - the blackboard
checks use to pass findings to later checks and to the scout card."""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import htmltools, signals
from .browser import BrowserSession, BrowserUnavailable, Rendered
from .config import BROWSER_HEADERS, ScoutConfig
from .fetcher import Fetched, PoliteFetcher

BOT_TOKEN = "SiteScout"   # the name robots.txt rules can address us by


def _transient(err: str) -> bool:
    """Network hiccups worth one more try - not budget refusals or blocks."""
    return any(w in err for w in ("Timeout", "timed out", "ConnectionError", "Connection reset",
                                  "RemoteDisconnected", "ChunkedEncodingError", "ProtocolError"))


class _DenyAll:
    def can_fetch(self, *a):
        return False


class ScoutContext:
    def __init__(self, cfg: ScoutConfig):
        self.cfg = cfg
        parts = urlsplit(cfg.url)
        self.url = cfg.url
        self.host = parts.hostname or ""
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.fetcher = PoliteFetcher(cfg, self.host)
        self.fetcher.deny = self._deny
        self.facts: dict = {}
        self.results: dict = {}
        self.robots = None                  # set by the robots check (start host)
        self._robots_other: dict = {}       # host -> rules (or None = no robots.txt) for other hosts
        self._browser: BrowserSession | None = None
        self._rendered: dict = {}
        self._soups: dict = {}
        self._main_fail = {}                # identity -> failed start-page fetch (so checks don't re-hammer)
        self.scratch: dict = {}             # in-memory hand-offs between checks (not written to the report)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9.-]+", "_", self.host) or "site"
        self.out_dir = Path(cfg.out_dir) if cfg.out_dir else Path("scouts") / f"{safe}_{stamp}"
        self.snap_dir = self.out_dir / "snapshots"
        self.snap_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- helpers
    def abs(self, href: str) -> str:
        return urljoin(self.url, href)

    def same_site(self, url: str) -> bool:
        h = urlsplit(url).hostname or ""
        return h == self.host or h.endswith("." + self.host) or self.host.endswith("." + h)

    def allowed(self, url: str) -> bool:
        """robots.txt verdict for ANY url - other hosts (api.x.com, search.x.com)
        have their own robots.txt, fetched once per host on first use."""
        rules = self.robots_for(url)
        if rules is None:
            return True
        try:   # both names: our own token and the wildcard - the stricter answer wins
            return rules.can_fetch(BOT_TOKEN, url) and rules.can_fetch("*", url)
        except Exception:
            return True

    def _deny(self, url: str):
        """Last line of defence inside the fetcher: never request a page of the start host
        that its (already read) robots.txt forbids."""
        parts = urlsplit(url)
        if self.robots is None or (parts.hostname or "") != self.host or parts.path == "/robots.txt":
            return None
        if self.allowed(url):
            return None
        self.facts.setdefault("not_fetched_by_rules", []).append(url)
        return "not fetched: the site's bot rules forbid this page"

    def robots_for(self, url: str):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host == self.host:
            return self.robots
        if host in self._robots_other:
            return self._robots_other[host]
        from .checks.robots import _Rules  # late import: checks depend on context, not the reverse
        r = self.get(f"{parts.scheme}://{parts.netloc}/robots.txt", purpose="robots.txt (other host)",
                     escalate=False)
        rules = None
        if r.error is None and r.status < 400 and "<html" not in r.text[:500].lower() and r.text.strip():
            try:
                rules = _Rules(r.text)
                self.save(f"robots_{re.sub(r'[^A-Za-z0-9.-]+', '_', host)}.txt", r.text)
            except Exception:
                rules = None
        elif r.status >= 500:
            rules = _DenyAll()
        self._robots_other[host] = rules
        self.facts.setdefault("robots_other_hosts", {})[host] = (
            "no robots.txt" if rules is None else "server error - treat as off-limits"
            if isinstance(rules, _DenyAll) else "rules found")
        return rules

    def get(self, url: str, **kw) -> Fetched:
        return self.fetcher.get(url, **kw)

    def main_page(self) -> Fetched:
        """The start URL, fetched once and shared by every check.

        A network error on the first try is retried (22 Oct: one read timeout made
        Allbirds 'unreachable' while a later check loaded it fine). A site that
        hangs EVERY time is a real finding (tarpit) - that is recorded, not hidden."""
        ident = self.fetcher.impersonating or "plain"
        if ident in self._main_fail:
            return self._main_fail[ident]
        r = self.fetcher.get(self.url, purpose="main page", allow_when_blocked=True)
        tries = 0
        while r.error and _transient(r.error) and tries < self.cfg.start_retries:
            time.sleep(self.cfg.start_retry_wait * (tries + 1))
            tries += 1
            r = self.fetcher.get(self.url, purpose=f"main page (retry {tries})", allow_when_blocked=True,
                                 use_cache=False)
        if tries:
            self.facts["start_retries"] = {"tries": tries, "recovered": r.error is None, "last_error": r.error}
        if r.error:
            self._main_fail[ident] = r
        elif "identity_checked" not in self.scratch:
            r = self._compare_identity(r)
        return r

    # ------------------------------------------------------------ identity
    def _compare_identity(self, r: Fetched) -> Fetched:
        """The scout starts honest. If the start page refuses that (block, error status, or a
        stripped page), make exactly ONE labelled request with the Chrome identity. If that gets
        in, the rest of the run uses Chrome and the card says so."""
        self.scratch["identity_checked"] = True
        f = self.fetcher
        info = {"requested": self.cfg.identity, "used": f.identity}
        self.facts["identity"] = info
        if self.cfg.identity == "browser":
            info["honest_ok"] = None
            info["note"] = "browser identity chosen with --identity browser; honest identity not tested"
            return r
        thin = 200 <= r.status < 300 and signals.is_thin(r.text)
        refused = bool(r.blocked) or r.status >= 400 or thin
        if not refused:
            info["honest_ok"] = True
            return r
        if f.remaining() < 1:
            info["honest_ok"] = None
            info["note"] = "honest identity refused; no request budget left for a comparison"
            return r
        c = f.get(self.url, purpose="main page (comparison: browser identity)", allow_when_blocked=True,
                  use_cache=False, headers=BROWSER_HEADERS, identity="browser")
        c_in = c.error is None and c.ok and not signals.is_thin(c.text)
        if c_in:
            f.switch_identity("browser")
            f.blocked = None
            f._cache[self.url] = c
            if c.ok and not signals.is_thin(c.text):
                f._good[self.url] = c.size
            info.update(honest_ok=False, used="browser",
                        note=f"honest identity refused ({r.blocked or 'HTTP ' + str(r.status)}); "
                             f"browser identity gets in (HTTP {c.status})")
            return c
        # both refused (or the same stripped app shell) - the identity is not the difference
        info["honest_ok"] = None if (r.blocked or r.status >= 400) else True
        info["note"] = ("honest and browser identities got the same answer - identity is not the difference"
                        if info["honest_ok"] else "honest and browser identities were both refused")
        return r

    def soup(self, key: str, html: str):
        if key not in self._soups:
            self._soups[key] = htmltools.parse(html)
        return self._soups[key]

    def save(self, name: str, data, binary: bool = False) -> str:
        path = self.snap_dir / name
        if binary:
            path.write_bytes(data)
        else:
            path.write_text(data if isinstance(data, str) else str(data),
                            encoding="utf-8", errors="replace")
        return str(path.relative_to(self.out_dir))

    # ------------------------------------------------------------- browser
    def browser_allowed(self) -> bool:
        return self.cfg.browser != "never" and "browser_unavailable" not in self.facts

    def rendered(self, url: str | None = None) -> Rendered | None:
        """An already-rendered page, without triggering a browser load."""
        return self._rendered.get(url or self.url)

    def render(self, url: str | None = None, allow_when_blocked: bool = False) -> Rendered | None:
        """Load a page in the real browser (counts as one request). None if the
        browser is off, unavailable, or the budget/block rules say stop."""
        url = url or self.url
        if url in self._rendered:
            return self._rendered[url]
        if not self.browser_allowed():
            return None
        if self._deny(url):
            return None
        why_not = self.fetcher.refusal(allow_when_blocked)
        if why_not:
            self.facts.setdefault("browser_refused", why_not)
            return None
        if self._browser is None:
            self._browser = BrowserSession(self.cfg)
        self.fetcher.wait_turn()
        self.fetcher.count += 1
        try:
            r = self._browser.render(url)
        except BrowserUnavailable as e:
            self.facts["browser_unavailable"] = str(e)
            return None
        self.fetcher.log.append({"n": self.fetcher.count, "url": url, "status": r.status,
                                 "bytes": len(r.html), "ms": round(r.load_ms),
                                 "purpose": "browser load", "identity": "browser",
                                 "blocked": None, "error": r.error})
        self._rendered[url] = r
        self._canary_after_browser()
        return r

    def _canary_after_browser(self):
        """28 Oct (Daraz): one real-browser visit got the whole connection blocked, and
        every later plain test quietly measured the block page. After the FIRST browser
        load, if plain requests were working, check once that they still are. If not,
        say so loudly and stop - the browser is what tripped the guard."""
        if self.scratch.get("canary_done") or self.fetcher.blocked:
            return
        self.scratch["canary_done"] = True
        main = self.fetcher._cache.get(self.url)
        if main is None or not main.ok or self.fetcher.remaining() < 1:
            return
        again = self.fetcher.get(self.url, use_cache=False, purpose="check plain still works after browser")
        if again.error is None and (again.blocked or not again.ok):
            why = again.blocked or f"HTTP {again.status}"
            self.facts["browser_triggered_block"] = why
            self.fetcher.blocked = f"blocked right after the browser visit ({why})"

    def browser_cookies(self, url: str | None = None) -> str:
        return self._browser.cookie_header(url or self.url) if self._browser else ""

    def close(self):
        if self._browser:
            self._browser.close()
        self.fetcher.close()
