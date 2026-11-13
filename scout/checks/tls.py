"""Some guards block Python's own connection 'fingerprint' even with browser
headers. curl_cffi copies a real Chrome's fingerprint. If that gets in, a
cheap plain scraper still works - no real browser needed."""
from __future__ import annotations

from .base import Check


class BrowserIdentity(Check):
    name = "tls"
    title = "Browser-identity requests (curl_cffi)"
    order = 22

    def should_run(self, ctx):
        if not ctx.facts.get("blocked_at_start"):
            return "plain requests already work"
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            return "curl_cffi not installed (pip install site-scout[tls]) - can't test this cheaper route"
        return None

    def run(self, ctx):
        f = ctx.fetcher
        before = ctx.facts["blocked_at_start"]
        err = f.use_impersonation("chrome")
        if err:
            self.warn(err)
            return {"works": False, "error": err}
        r = f.get(ctx.url, purpose="curl_cffi test", allow_when_blocked=True, escalate=False)
        works = r.ok
        out = {"status": r.status, "bytes": r.size, "blocked": r.blocked, "works": works}
        if works:
            # carry on the whole run with the browser identity
            f.blocked = None
            ctx.facts["tls_impersonation"] = True
            ctx.facts["blocked_plain_python"] = before
            ctx.facts.pop("blocked_at_start", None)
            ctx.save("raw_main.html", r.text)
            self.note(f"Plain Python was blocked ({before}), but requests copying Chrome's connection "
                      f"fingerprint (curl_cffi) get in: HTTP {r.status}, {r.size / 1024:.0f} KB. "
                      "A plain scraper works if it uses curl_cffi - no real browser needed.")
            self.note("The rest of this scout run uses curl_cffi.")
        else:
            self.note(f"curl_cffi with Chrome's fingerprint is blocked too (HTTP {r.status}"
                      + (f", {r.blocked}" if r.blocked else "") + ") - the guard checks more than "
                      "the connection; the browser check decides.")
        return out
