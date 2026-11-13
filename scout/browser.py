"""Lazy, shared real-browser session (Playwright + Chromium).

Opened only when a check actually needs it, opened once, reused, closed at
the end. Heavy assets (images, fonts, video) are skipped to save time and
data. Every behind-the-scenes data request the page makes is recorded.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import signals
from .config import BROWSER_UA, ScoutConfig

CHALLENGE_WAIT_S = 12   # how long to let a "checking your browser" page finish

JSON_BODY_CAP = 2_000_000   # keep at most this much of each captured JSON body
MAX_CAPTURED = 80           # captured data responses per page


class BrowserUnavailable(RuntimeError):
    pass


@dataclass
class NetCall:
    url: str
    method: str
    resource_type: str
    status: int
    content_type: str
    size: int
    request_headers: dict
    post_data: str | None
    body: str | None            # only kept for JSON-like responses
    phase: str = "load"         # load | scroll


@dataclass
class Rendered:
    url: str
    final_url: str = ""
    status: int = 0
    html: str = ""
    text: str = ""
    load_ms: float = 0.0
    calls: list = field(default_factory=list)
    cookies: list = field(default_factory=list)
    scroll: dict = field(default_factory=dict)
    click: dict = field(default_factory=dict)
    console_errors: int = 0
    challenge_wait_s: int = 0
    error: str | None = None


class BrowserSession:
    def __init__(self, cfg: ScoutConfig):
        self.cfg = cfg
        self._pw = None
        self._browser = None
        self._ctx = None
        self.pages_loaded = 0

    def _start(self):
        if self._browser:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserUnavailable(
                "Playwright not installed. Run:  pip install site-scout[browser]  then  "
                "python -m playwright install chromium") from e
        try:
            self._pw = sync_playwright().start()
            launch = {"headless": True}
            if self.cfg.proxy:
                launch["proxy"] = {"server": self.cfg.proxy}
            self._browser = self._pw.chromium.launch(**launch)
            self._ctx = self._browser.new_context(
                user_agent=BROWSER_UA, locale="en-US",
                viewport={"width": 1366, "height": 900})
            if self.cfg.block_heavy_assets:
                self._ctx.route("**/*", self._route)
        except Exception as e:
            self.close()
            raise BrowserUnavailable(
                f"Could not start Chromium ({type(e).__name__}: {str(e).splitlines()[0][:160]}). "
                "Try:  python -m playwright install chromium") from e

    @staticmethod
    def _route(route):
        if route.request.resource_type in ("image", "font", "media"):
            return route.abort()
        return route.continue_()

    def render(self, url: str, scroll: bool = True) -> Rendered:
        self._start()
        out = Rendered(url=url)
        calls: list = []
        phase = {"now": "load"}
        doc_status = {"last": 0}

        def on_response(resp):
            if len(calls) >= MAX_CAPTURED:
                return
            req = resp.request
            rtype = req.resource_type
            if rtype not in ("xhr", "fetch", "document", "other", "eventsource", "websocket"):
                return
            if rtype == "document":
                try:
                    if req.frame != page.main_frame:
                        return
                except Exception:
                    return
                doc_status["last"] = resp.status
            ctype = (resp.headers.get("content-type") or "").lower()
            body, size = None, 0
            try:
                raw = resp.body()
                size = len(raw)
                looks_json = "json" in ctype or raw[:1] in (b"{", b"[")
                if looks_json and rtype != "document":
                    body = raw[:JSON_BODY_CAP].decode("utf-8", "replace")
            except Exception:
                pass
            keep = {k: v for k, v in req.headers.items()
                    if k.lower() not in ("cookie", "user-agent", "accept-encoding")}
            calls.append(NetCall(
                url=resp.url, method=req.method, resource_type=rtype, status=resp.status,
                content_type=ctype, size=size, request_headers=keep,
                post_data=(req.post_data or None) and req.post_data[:2000],
                body=body, phase=phase["now"]))

        page = self._ctx.new_page()
        page.on("response", on_response)
        errors = {"n": 0}
        page.on("console", lambda m: errors.__setitem__("n", errors["n"] + (m.type == "error")))
        t0 = time.perf_counter()
        try:
            resp = page.goto(url, wait_until="domcontentloaded",
                             timeout=self.cfg.browser_timeout_ms)
            out.status = resp.status if resp else 0
            try:   # sites with live connections never go idle - cap the wait
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            # a challenge page ("checking your browser") may solve itself and reload
            waited = 0
            while waited < CHALLENGE_WAIT_S and self._challenged(page):
                page.wait_for_timeout(1000)
                waited += 1
            if waited:
                out.challenge_wait_s = waited
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
            out.status = doc_status["last"] or out.status
            out.load_ms = (time.perf_counter() - t0) * 1000
            out.final_url = page.url
            out.html = page.content()
            out.text = page.evaluate("() => document.body ? document.body.innerText : ''")

            if scroll and self.cfg.scroll_rounds > 0:
                phase["now"] = "scroll"
                before_calls = len(calls)
                h0 = page.evaluate("() => document.body ? document.body.scrollHeight : 0")
                heights = [h0]
                for _ in range(self.cfg.scroll_rounds):
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1800)
                    heights.append(page.evaluate(
                        "() => document.body ? document.body.scrollHeight : 0"))
                out.scroll = {
                    "heights": heights,
                    "grew": heights[-1] > heights[0] * 1.15,
                    "new_data_calls": len(calls) - before_calls,
                }
            if self.cfg.click_more > 0:
                phase["now"] = "click"
                out.click = self._press_load_more(page, calls)
            out.cookies = sorted({c["name"] for c in self._ctx.cookies()})
        except Exception as e:
            out.error = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            out.console_errors = errors["n"]
            out.calls = calls
            try:
                page.close()
            except Exception:
                pass
        self.pages_loaded += 1
        return out

    def _press_load_more(self, page, calls) -> dict:
        """Press a 'load more' button a couple of times and record what it does:
        fetch new data (a feed), go to another page (a link), or just reveal
        items that were already in the page (client-side)."""
        text = "() => document.body ? document.body.innerText.length : 0"
        res = {"found": False}
        try:
            loc = page.locator("button, a, [role=button], input[type=button]").filter(
                has_text=signals.LOAD_MORE)
            target = None
            for i in range(min(loc.count(), 10)):
                el = loc.nth(i)
                if el.is_visible() and not el.evaluate(signals.NOT_LOAD_MORE_JS):
                    target = el
                    break
            if target is None:
                return res
            res.update(found=True, label=(target.inner_text() or "").strip()[:60])
            before_text, before_calls, before_url = page.evaluate(text), len(calls), page.url
            clicks = 0
            for _ in range(self.cfg.click_more):
                if not target.is_visible():
                    break
                target.scroll_into_view_if_needed(timeout=3000)
                target.click(timeout=5000)
                clicks += 1
                page.wait_for_timeout(2000)
                if page.url != before_url:
                    break
            new = [c for c in calls[before_calls:]]
            res.update(clicks=clicks, text_before=before_text, text_after=page.evaluate(text),
                       navigated_to=page.url if page.url != before_url else None,
                       new_requests=len(new), new_data_calls=sum(1 for c in new if c.body),
                       still_visible=target.is_visible())
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:150]}"
        return res

    def cookie_header(self, url: str) -> str:
        """The browser's cookies for `url`, as a Cookie header (for reuse in plain requests)."""
        if not self._ctx:
            return ""
        try:
            return "; ".join(f"{c['name']}={c['value']}" for c in self._ctx.cookies(url))
        except Exception:
            return ""

    @staticmethod
    def _challenged(page) -> bool:
        try:
            html = page.content()
        except Exception:   # page is mid-navigation - still working on it
            return True
        hits = signals.challenges_in_body(html)
        return any(h not in signals.SOFT_CHALLENGES for h in hits)

    def close(self):
        for obj, meth in ((self._ctx, "close"), (self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._pw = self._browser = self._ctx = None
