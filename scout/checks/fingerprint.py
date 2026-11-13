"""First real look at the page: how it answers, what guards it, what it's built with."""
from __future__ import annotations

import re

from .. import guards as guard_sigs
from .. import signals
from .base import Check, SkipCheck

# marker found in raw HTML (case-insensitive) -> technology
TECH_MARKERS = [
    ("__NEXT_DATA__", "Next.js"), ("/_next/static", "Next.js"),
    ("self.__next_f", "Next.js (app router)"),
    ("__NUXT__", "Nuxt"), ("/_nuxt/", "Nuxt"),
    ("ng-version=", "Angular"), ("data-reactroot", "React"), ("react-dom", "React"),
    ("data-v-", "Vue"), ("__sveltekit", "SvelteKit"), ("___gatsby", "Gatsby"),
    ("__remixContext", "Remix"), ("astro-island", "Astro"),
    ("wp-content/", "WordPress"), ("/wp-json/", "WordPress"),
    ("cdn.shopify.com", "Shopify"), ("Shopify.theme", "Shopify"),
    ("woocommerce", "WooCommerce"), ("Magento_", "Magento"), ("mage/cookies", "Magento"),
    ("cdn11.bigcommerce.com", "BigCommerce"), ("static.wixstatic.com", "Wix"),
    ("static1.squarespace.com", "Squarespace"), ("Drupal.settings", "Drupal"), ("drupal-settings-json", "Drupal"),
    ("prestashop", "PrestaShop"), ("__VIEWSTATE", "ASP.NET WebForms"),
    ("jquery", "jQuery"), ("alpinejs", "Alpine.js"), ("htmx.org", "htmx"),
    ("livewire", "Laravel Livewire"),
]
COOKIE_TECH = {"phpsessid": "PHP", "laravel_session": "Laravel", "csrftoken": "Django",
               "asp.net_sessionid": "ASP.NET", "jsessionid": "Java", "_shopify": "Shopify",
               "connect.sid": "Node/Express", "ci_session": "CodeIgniter"}
GENERATOR = re.compile(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', re.I)
LANG = re.compile(r"<html[^>]*\slang=[\"']?([A-Za-z-]+)", re.I)
PASSWORD = re.compile(r"<input[^>]+type=[\"']?password", re.I)
LOGIN_URL = re.compile(r"/(login|signin|sign-in|auth|account/login)\b", re.I)
HARD_GUARDS = {"DataDome", "PerimeterX/HUMAN", "Akamai Bot Manager", "Imperva/Incapsula",
               "Imperva Advanced Bot Protection", "Cloudflare challenge", "AWS WAF", "DDoS-Guard"}


class Fingerprint(Check):
    name = "fingerprint"
    title = "First look: response, guards, technology"
    order = 20

    def run(self, ctx):
        r = ctx.main_page()
        if r.error and ctx.facts.get("rules_forbid_start"):
            raise SkipCheck("start page not requested - the site's bot rules forbid it")
        if r.error:
            ctx.facts["unreachable"] = r.error
            self.warn(f"Start page could not be loaded: {r.error}")
            raise SkipCheck("start page unreachable")

        ctx.save("raw_main.html", r.text)
        h = {k.lower(): v for k, v in r.headers.items()}
        out = {
            "status": r.status, "final_url": r.final_url, "redirects": r.redirects,
            "bytes": r.size, "truncated": r.truncated, "ms": round(r.elapsed_ms),
            "content_type": r.content_type, "server": h.get("server"),
            "powered_by": h.get("x-powered-by"), "compression": h.get("content-encoding"),
            "cache_control": h.get("cache-control"), "etag": "etag" in h,
            "last_modified": "last-modified" in h, "cookies": r.cookies,
        }

        # ---- guards / blocks
        present, possible = guard_sigs.detect(r.status, r.headers, r.cookies, r.text)
        guards = set(present)
        challenges = signals.challenges_in_body(r.text)
        out["guards"] = sorted(guards)
        out["challenge_markers"] = challenges
        out["guards_possible"] = possible
        ctx.facts["guards"] = sorted(guards)
        ctx.facts["guards_possible"] = possible
        ctx.facts["hard_guard"] = sorted(guards & HARD_GUARDS)
        rate = signals.rate_headers(r.headers)
        if rate:
            out["rate_headers"] = rate
            ctx.facts.setdefault("rate_headers", {}).update(rate)
            self.note(f"Site advertises rate-limit info in its replies: {rate}.")

        if r.blocked:
            ctx.facts["blocked_at_start"] = r.blocked
            self.warn(f"Plain request was BLOCKED on the first visit ({r.blocked}). "
                      "A plain scraper will not work as-is; the browser check will see if a real browser gets in.")
        else:
            self.note(f"Plain request works: HTTP {r.status}, {r.size / 1024:.0f} KB in {r.elapsed_ms:.0f} ms.")
        if guards:
            self.note(f"Sits behind: {', '.join(sorted(guards))}.")
        if possible:
            self.note(f"A possible guard (only a word on a content-rich page, so not counted as present): "
                      f"{', '.join(possible)}.")
        if ctx.facts["hard_guard"]:
            self.warn(f"Bot-management product detected ({', '.join(ctx.facts['hard_guard'])}) - "
                      "expect blocks at volume even if the first visits pass.")
        if r.redirects:
            self.note(f"Redirected {len(r.redirects)}x to {r.final_url}.")

        # ---- technology
        low = r.text[:400_000].lower()
        tech = []
        for marker, label in TECH_MARKERS:
            if marker.lower() in low and label not in tech:
                tech.append(label)
        for c in r.cookies:
            for k, label in COOKIE_TECH.items():
                if c.lower().startswith(k) and label not in tech:
                    tech.append(label)
        gen = GENERATOR.search(r.text[:50_000])
        if gen:
            out["generator"] = gen.group(1)
            for word in ("WordPress", "Shopify", "Drupal", "Joomla", "Wix", "Squarespace",
                         "PrestaShop", "Magento", "Ghost", "Hugo", "Gatsby", "Next.js"):
                if word.lower() in gen.group(1).lower() and word not in tech:
                    tech.append(word)
            tech.append(f"generator: {gen.group(1)}")
        out["tech"] = tech
        ctx.facts["tech"] = tech
        if tech:
            self.note(f"Built with: {', '.join(tech[:8])}.")
        if "ASP.NET WebForms" in tech:
            self.warn("ASP.NET WebForms: paging often works through hidden form posts (__VIEWSTATE), "
                      "not links - more fiddly to scrape.")
        lang = LANG.search(r.text[:5000])
        if lang:
            out["lang"] = lang.group(1)

        # ---- login wall
        login = (r.status == 401 or bool(LOGIN_URL.search(r.final_url or "")) or
                 (bool(PASSWORD.search(r.text)) and r.size < 60_000))
        out["login_wall"] = login
        ctx.facts["login_wall"] = login
        if login:
            self.warn("Looks like a login wall (password form / login redirect). Data behind logins "
                      "needs the client's own account and their explicit permission.")

        if out["etag"] or out["last_modified"]:
            self.note("Replies carry change markers (ETag/Last-Modified) - see caching check.")

        # ---- does the site filter by the default library identity?
        # (skipped while running honest: that identity is already not a browser's, and the
        # scout should not send a second, unlabelled identity)
        if not r.blocked and r.ok and ctx.fetcher.identity == "browser":
            bare = ctx.get(ctx.url, plain_session=True, purpose="UA filter test")
            if bare.error is None:
                filt = bare.status != r.status or bool(bare.blocked) or bare.size < r.size * 0.3
                out["ua_filter"] = {"bare_status": bare.status, "bare_bytes": bare.size,
                                    "filters_default_ua": filt}
                ctx.facts["ua_filtering"] = filt
                if filt:
                    self.note(f"Site treats the default Python identity differently "
                              f"(HTTP {bare.status}, {bare.size / 1024:.0f} KB) - scrapers must send "
                              "browser-like headers.")
                else:
                    self.note("Site does not filter the default Python identity.")
        return out
