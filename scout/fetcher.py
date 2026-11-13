"""The only door to the network for plain (non-browser) requests.

Every check goes through PoliteFetcher, so the politeness rules are enforced
in one place: a minimum gap between requests, a total request budget, a
download size cap, a run-time cap, and an emergency stop when the site starts
pushing back.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

from . import signals
from .config import ScoutConfig, headers_for

log = logging.getLogger("scout")
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", re.I)


@dataclass
class Fetched:
    url: str
    final_url: str = ""
    status: int = 0
    headers: dict = field(default_factory=dict)
    content: bytes = b""
    text: str = ""
    elapsed_ms: float = 0.0
    redirects: list = field(default_factory=list)
    cookies: list = field(default_factory=list)
    truncated: bool = False
    blocked: str | None = None     # reason if this response looks like a block
    error: str | None = None       # network error / budget refusal

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300 and not self.blocked

    @property
    def size(self) -> int:
        return len(self.content)

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", self.headers.get("content-type", "")).lower()

    @property
    def is_json(self) -> bool:
        if "json" in self.content_type:
            return True
        head = self.text[:1].strip()
        return head in ("{", "[") and self.size < 20_000_000

    def json(self):
        import json
        try:
            return json.loads(self.text)
        except Exception:
            return None


class PoliteFetcher:
    def __init__(self, cfg: ScoutConfig, host: str):
        self.cfg = cfg
        self.host = host
        self.session = requests.Session()
        self.identity = "browser" if cfg.identity == "browser" else "honest"
        self.session.headers.update(headers_for(self.identity))
        self.deny = None               # optional callback: url -> reason string if bot rules forbid it
        if cfg.proxy:
            self.session.proxies = {"http": cfg.proxy, "https": cfg.proxy}
        self.count = 0
        self.started = time.monotonic()
        self._last = 0.0
        self.blocked: str | None = None
        self.impersonating: str | None = None
        self.log: list = []
        self._cache: dict = {}
        self._good: dict = {}          # url -> size of its first good (non-thin) answer

    # ---------------------------------------------------------------- budget
    def remaining(self) -> int:
        return self.cfg.max_requests - self.count

    def time_left(self) -> float:
        return self.cfg.max_runtime - (time.monotonic() - self.started)

    def refusal(self, allow_when_blocked=False) -> str | None:
        if self.count >= self.cfg.max_requests:
            return "request budget used up"
        if self.time_left() <= 0:
            return "run time limit reached"
        if self.blocked and self.cfg.stop_on_block and not allow_when_blocked:
            return f"stopped: site pushed back earlier ({self.blocked})"
        return None

    def note_block(self, reason: str):
        """Browser or checks can report a block found outside this fetcher."""
        if not self.blocked:
            log.info("stopping further requests: %s", reason)
            self.blocked = reason

    def wait_turn(self, delay: float | None = None):
        gap = self.cfg.min_delay if delay is None else delay
        wait = self._last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    # ----------------------------------------------------------------- fetch
    def get(self, url: str, *, headers: dict | None = None, purpose: str = "",
            delay: float | None = None, use_cache: bool = True,
            allow_when_blocked: bool = False, plain_session: bool = False,
            method: str = "GET", data: str | None = None,
            escalate: bool = True, identity: str | None = None) -> Fetched:
        """escalate=False: a 401/403 here is expected (e.g. a feed needing a login)
        and should not trigger the global stop. 429/503/challenge pages always do."""
        cacheable = use_cache and not headers and not plain_session and method == "GET"
        if cacheable and url in self._cache:
            return self._cache[url]

        why_not = self.refusal(allow_when_blocked)
        if why_not:
            return Fetched(url=url, error=why_not)
        if self.deny:
            forbidden = self.deny(url)
            if forbidden:
                return Fetched(url=url, error=forbidden)

        self.wait_turn(delay)
        self.count += 1
        res = Fetched(url=url)
        t0 = time.perf_counter()
        try:
            if plain_session:
                # a bare request with library defaults (used to test UA filtering)
                r = requests.get(url, timeout=self.cfg.timeout, stream=True,
                                 proxies=self.session.proxies or None)
            else:
                r = self.session.request(method, url, headers=headers, data=data,
                                         timeout=self.cfg.timeout, stream=True,
                                         allow_redirects=True)
            chunks, total = [], 0
            for chunk in r.iter_content(64 * 1024):
                chunks.append(chunk)
                total += len(chunk)
                if total > self.cfg.max_body_bytes:
                    res.truncated = True
                    break
            r.close()
            res.content = b"".join(chunks)
            res.status = r.status_code
            res.final_url = r.url
            res.headers = dict(r.headers)
            res.redirects = [(getattr(h, "status_code", 0), getattr(h, "headers", {}).get("Location", ""))
                             for h in (getattr(r, "history", None) or [])]
            res.cookies = sorted({getattr(c, "name", c) for c in self.session.cookies} |
                                 {getattr(c, "name", c) for c in r.cookies})
            res.text = self._decode(res.content, r)
        except Exception as e:  # requests or curl_cffi network errors
            res.error = f"{type(e).__name__}: {str(e)[:200]}"
        res.elapsed_ms = (time.perf_counter() - t0) * 1000

        if res.error is None:
            res.blocked = signals.looks_blocked(res.status, res.text, res.size)
            if not res.blocked and not plain_session:
                res.blocked = self._shrank(url, res)
            same_site = urlsplit(res.final_url or url).hostname == self.host
            serious = escalate or res.status in (429, 503) or not res.blocked.startswith("HTTP") \
                if res.blocked else False
            if res.blocked and same_site and not plain_session and serious:
                self.note_block(res.blocked)

        self.log.append({
            "n": self.count, "url": url, "status": res.status, "bytes": res.size,
            "ms": round(res.elapsed_ms), "purpose": purpose,
            "identity": identity or ("browser" if self.impersonating else self.identity),
            "blocked": res.blocked, "error": res.error,
        })
        log.debug("request %d: %s %sB %.0fms %s %s%s", self.count, res.status or "ERR", res.size,
                  res.elapsed_ms, purpose or "-", url[:120], f" ({res.error})" if res.error else "")
        if cacheable and res.error is None:
            self._cache[url] = res
        return res

    def _shrank(self, url: str, res: Fetched) -> str | None:
        """28 Oct (Daraz): a page that was rich a minute ago comes back as an empty
        200. Whatever the guard, 'same address, content gone' is a soft block - judged
        against the FIRST good answer, never the previous one (40 identical block pages
        in a row look perfectly steady)."""
        good = self._good.get(url)
        if good is None:
            if 200 <= res.status < 300 and not signals.is_thin(res.text):
                self._good[url] = res.size
            return None
        # no size floor needed: the new answer must ALSO be thin (no real text, few links),
        # so an ordinary small page that varies a little never trips this
        if res.size < good * 0.3 and signals.is_thin(res.text):
            return f"page shrank to {res.size:,} bytes from {good:,} (soft block)"
        return None

    def switch_identity(self, identity: str):
        """Every following request uses this identity (cookies are kept, cache is dropped)."""
        self.identity = identity
        self.session.headers.update(headers_for(identity))
        self._cache.clear()

    def use_impersonation(self, target: str = "chrome") -> str | None:
        """Switch every following request to curl_cffi, which copies a real
        browser's connection fingerprint (some guards block Python's own).
        Returns an error string if curl_cffi is unavailable."""
        try:
            from curl_cffi import requests as cffi
        except ImportError:
            return "curl_cffi not installed (pip install site-scout[tls])"
        sess = cffi.Session(impersonate=target)
        if self.cfg.proxy:
            sess.proxies = {"http": self.cfg.proxy, "https": self.cfg.proxy}
        self.session.close()
        self.session = sess
        self.impersonating = target
        self.identity = "browser"
        self._cache.clear()
        return None

    @staticmethod
    def _decode(content: bytes, r) -> str:
        enc = None
        ctype = r.headers.get("Content-Type", "")
        if "charset=" in ctype.lower():
            enc = r.encoding
        else:
            m = _META_CHARSET.search(content[:4096])
            if m:
                enc = m.group(1).decode("ascii", "ignore")
        try:
            return content.decode(enc or "utf-8", errors="replace")
        except LookupError:
            return content.decode("utf-8", errors="replace")

    def close(self):
        self.session.close()
