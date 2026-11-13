"""Signatures for recognising bot guards, challenges and rate-limit hints.

Pure functions, no network. Extend the tables to teach the scout new guards.
"""
from __future__ import annotations

import re

# header/cookie name -> guard or network it reveals
GUARD_HEADERS = {
    "cf-ray": "Cloudflare",
    "cf-cache-status": "Cloudflare",
    "x-amz-cf-id": "Amazon CloudFront",
    "x-akamai-transformed": "Akamai",
    "akamai-grn": "Akamai",
    "x-akamai-request-id": "Akamai",
    "x-sucuri-id": "Sucuri",
    "x-iinfo": "Imperva/Incapsula",
    "x-cdn": "Imperva/Incapsula",
    "x-datadome": "DataDome",
    "x-datadome-cid": "DataDome",
    "x-px": "PerimeterX/HUMAN",
    "x-served-by": "Fastly",
    "x-fastly-request-id": "Fastly",
    "x-vercel-id": "Vercel",
    "x-nf-request-id": "Netlify",
    "x-azure-ref": "Azure Front Door",
    "x-goog-": "Google Cloud",
}

GUARD_COOKIES = {
    "__cf_bm": "Cloudflare Bot Management",
    "cf_clearance": "Cloudflare challenge",
    "__cflb": "Cloudflare",
    "incap_ses": "Imperva/Incapsula",
    "visid_incap": "Imperva/Incapsula",
    "datadome": "DataDome",
    "_px": "PerimeterX/HUMAN",
    "_pxhd": "PerimeterX/HUMAN",
    "ak_bmsc": "Akamai Bot Manager",
    "bm_sz": "Akamai Bot Manager",
    "_abck": "Akamai Bot Manager",
    "aws-waf-token": "AWS WAF",
    "reese84": "Imperva Advanced Bot Protection",
}

# body text -> what it means. Checked case-insensitively on the first ~60 KB.
CHALLENGE_PATTERNS = [
    (r"just a moment\.\.\.|cf-chl-|challenge-platform|cf_chl_opt", "Cloudflare challenge page"),
    (r"attention required!? \| cloudflare", "Cloudflare block page"),
    (r"g-recaptcha|recaptcha/api\.js", "Google reCAPTCHA"),
    (r"hcaptcha\.com|h-captcha", "hCaptcha"),
    (r"challenges\.cloudflare\.com/turnstile", "Cloudflare Turnstile"),
    (r"px-captcha|perimeterx", "PerimeterX/HUMAN challenge"),
    (r"captcha-delivery\.com|datadome", "DataDome challenge"),
    (r"pardon our interruption", "Imperva/Distil block page"),
    (r"request unsuccessful\. incapsula", "Imperva/Incapsula block"),
    (r"access denied.{0,200}reference #", "Akamai access denied"),
    (r"<title>\s*client challenge\s*</title>|/_fs-ch-", "Fastly bot challenge"),
    (r"ddos-guard", "DDoS-Guard check"),
    (r"<title>\s*access denied\s*</title>", "Access denied page"),
    (r"checking (if the site connection is secure|your browser)|please wait while we verify",
     "Browser check page"),
    (r"enable js and disable any ad blocker", "DataDome challenge"),
    (r"are you a robot|verify you are human|unusual traffic", "Generic human check"),
    (r"too many requests|rate limit(ed)? exceeded", "Rate limit message"),
    # 28 Oct: Alibaba-family guard (Daraz, Lazada, AliExpress) - a 1 KB HTTP 200 page that
    # clears its cookie and jumps to a 'punish' page on another host
    (r"/punish/|punish:resource|x5secdata|_____tmd_____|rgv587_flag", "Alibaba-family block page (x5sec)"),
]
_CHALLENGE_RE = [(re.compile(p, re.I | re.S), label) for p, label in CHALLENGE_PATTERNS]

# Some challenge words also appear harmlessly on normal pages (e.g. a recaptcha
# on a contact form). These are only "blocking" when the page is also small.
SOFT_CHALLENGES = {"Google reCAPTCHA", "hCaptcha", "Cloudflare Turnstile",
                   "Generic human check", "Rate limit message"}

RATE_HEADERS = (
    "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "ratelimit-policy",
    "x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset",
)

BLOCK_STATUSES = {401, 403, 407, 429, 503}


def guards_from_headers(headers: dict) -> set:
    found = set()
    lower = {k.lower(): v for k, v in headers.items()}
    for key, label in GUARD_HEADERS.items():
        if key.endswith("-"):
            if any(h.startswith(key) for h in lower):
                found.add(label)
        elif key in lower:
            found.add(label)
    server = lower.get("server", "").lower()
    for word, label in (("cloudflare", "Cloudflare"), ("akamai", "Akamai"),
                        ("ddos-guard", "DDoS-Guard"), ("sucuri", "Sucuri"),
                        ("varnish", "Varnish cache"), ("awselb", "AWS load balancer")):
        if word in server:
            found.add(label)
    return found


def guards_from_cookies(cookie_names) -> set:
    found = set()
    for name in cookie_names:
        for prefix, label in GUARD_COOKIES.items():
            if name.lower().startswith(prefix.lower()):
                found.add(label)
    return found


def challenges_in_body(text: str) -> list:
    head = text[:60_000]
    return [label for rx, label in _CHALLENGE_RE if rx.search(head)]


def rate_headers(headers: dict) -> dict:
    lower = {k.lower(): v for k, v in headers.items()}
    return {k: lower[k] for k in RATE_HEADERS if k in lower}


def looks_blocked(status: int, text: str, size: int) -> str | None:
    """Return a reason string if this response looks like a block, else None.

    Rule (22 Oct, from the holdout run): a challenge WORD only proves the guard is
    PRESENT. It proves a BLOCK only when the page is also thin - little visible
    text, few links. Real pages mention challenges harmlessly all the time: a demo
    menu link "Cloudflare Turnstile Demo", Cloudflare's script injected on every
    page, a captcha on a contact form, Zameen's "STRAT_WITH_CAPTCHA" setting.
    Size in bytes was the old test and is a bad one (a 16 KB page can be rich)."""
    if status in BLOCK_STATUSES:
        return f"HTTP {status}"
    thin = is_thin(text)
    hits = challenges_in_body(text)
    if hits and thin:
        return hits[0]
    if thin and JS_GATE.search(text[:60_000]):
        if status == 200 and not hits and app_shell(text):
            return None      # a normal app that builds the page with code - "needs a browser", not "blocked"
        return "JavaScript-only gate (page shows nothing without running code)"
    if thin and status == 202:
        return "HTTP 202 holding page (typical of a bot check)"
    if thin and redirect_only(text):
        return "Redirect-only page (no content, its code just sends the visitor elsewhere - typical of a bot check)"
    return None


def redirect_only(text: str) -> bool:
    """28 Oct (Daraz product pages): a 200 page with no content whose only job is to
    navigate away - `location = ...`, `location.replace(...)`, or clicking a hidden
    link. A guard's 'deny' hop looks exactly like this, whatever the vendor. A real
    app shell that builds the page with its own code is not this (checked first)."""
    head = text[:60_000]
    if app_shell(head):
        return False
    return bool(_NAVIGATE.search(head))


_NAVIGATE = re.compile(r"<script[^>]*>(?:(?!</script>).)*?(?:\blocation(?:\.href)?\s*=[^=]|\blocation\.(?:replace|assign)\s*\(|"
                       r"\.click\s*\(\s*\))", re.I | re.S)


def richness(text: str) -> dict:
    """How much real content a page has: visible characters and links."""
    body = text[:2_000_000]
    return {"visible_chars": len(re.sub(r"\s+", " ", _TAGS.sub(" ", body)).strip()),
            "links": len(_LINK.findall(body))}


def is_thin(text: str) -> bool:
    """Little visible text AND few links = a holding/challenge page, not content."""
    r = richness(text)
    return r["visible_chars"] < THIN_CHARS and r["links"] < THIN_LINKS


def app_shell(text: str) -> bool:
    """An empty page that loads the site's OWN code to build itself (React/Ember/Vue
    apps). Challenge pages load the guard's code instead."""
    head = text[:200_000]
    if CHALLENGE_SCRIPT.search(head):
        return False
    srcs = _SCRIPT_SRC.findall(head)
    own = [s for s in srcs if s.startswith(("/", "./")) and not s.startswith("//")]
    return bool(own) or bool(_MOUNT.search(head)) or 'type="module"' in head or "type='module'" in head


THIN_CHARS = 1500
THIN_LINKS = 10
_LINK = re.compile(r"<a\s[^>]*href=", re.I)
_SCRIPT_SRC = re.compile(r"<script[^>]+src=[\"']([^\"']+)", re.I)
_MOUNT = re.compile(r'<(div|main|body)[^>]+(id=["\'](root|app|__next|__nuxt|svelte|main-app|ember-app)["\']|'
                    r'class=["\'][^"\']*ember-application)', re.I)
CHALLENGE_SCRIPT = re.compile(r"challenges\.cloudflare\.com|/cdn-cgi/challenge-platform|captcha-delivery\.com|"
                              r"px-cdn|perimeterx|awswaf|token\.awswaf|/_fs-ch-|incapsula|_Incapsula_|"
                              r"akam/|/akam|ddos-guard|hcaptcha|recaptcha", re.I)


# tiny page whose only message is "turn on JavaScript" - typical of challenge pages
JS_GATE = re.compile(r"enable javascript|javascript is (disabled|required)|requires javascript|"
                     r"turn on javascript", re.I)
_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)


# A control that OPENS something (menu, filter group, accordion) or lives in page
# chrome is never "load more" (22 Oct: Nike's "+ More Brand" filter expanders).
# Same rule in the HTML check (structure) and in the browser before pressing.
DISCLOSURE_ATTRS = ("aria-expanded", "aria-controls", "aria-haspopup")
CHROME_TAGS = ("nav", "header", "footer", "aside", "form")
NOT_LOAD_MORE_JS = ("el => ['aria-expanded','aria-controls','aria-haspopup'].some(a => el.hasAttribute(a)) || "
                    "!!el.closest('nav,header,footer,aside,form,[role=navigation],[role=dialog],[role=menu]')")


def not_load_more(el) -> bool:
    """BeautifulSoup element version of NOT_LOAD_MORE_JS."""
    if any(el.has_attr(a) for a in DISCLOSURE_ATTRS):
        return True
    if el.find_parent(list(CHROME_TAGS)) is not None:
        return True
    return el.find_parent(attrs={"role": ["navigation", "dialog", "menu"]}) is not None


# text of "load more" style buttons (shared by structure check and browser)
LOAD_MORE = re.compile(r"load more|show more|more results|view more|see more|more offers|"
                       r"показать (еще|ещё|больше)|загрузить (еще|ещё)|mehr laden|voir plus|ver más|"
                       r"^\W*(more|plus|mehr|más|mais|ещё|еще)\W*$", re.I)   # bare "More" buttons too
