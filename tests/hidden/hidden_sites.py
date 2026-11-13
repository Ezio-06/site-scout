"""Tiny local fake sites for the hidden acceptance checks (127.0.0.1 only).

Every request is recorded as (path, User-Agent) so checks can see what identity
the scout really sent, independent of what it writes in its own log.

Modes
  honest      rich listing page for everyone; robots.txt missing (404)
  robots      robots.txt disallows /private/ for the SiteScout token only, allows * everywhere;
              start page is /private/list (rich page)
  blockhonest any request whose User-Agent contains "SiteScout" gets 403 (every path except
              robots.txt, which is 404 for all); any other identity gets the rich page
  appshell    empty app shell that needs a browser to build itself (used for the no-extras run)

Phase 2 (guard signatures, exit codes)
  sigs        rich page at /list (+ page 2) sent with headers "aeSecure-code: 1" and "cf-ray"
  possible    rich page whose body mentions airee.cloud and aesecure_denied.png; no guard headers
  thinbody    thin 403 page for everyone whose body carries Anubis's script path (a wafw00f body rule)
  blockall    every identity gets a thin 403 "Forbidden" page; robots.txt 404
  robotsall   robots.txt "User-agent: * / Disallow: /"; start page rich
  slow        rich page that takes SLOW_SECONDS to answer (for the Ctrl+C check)
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

START_PATH = {"honest": "/list", "robots": "/private/list", "blockhonest": "/list", "appshell": "/app",
              "sigs": "/list", "possible": "/list", "thinbody": "/list", "blockall": "/list",
              "robotsall": "/list", "slow": "/list"}
PHASE2 = ("sigs", "possible", "thinbody", "blockall", "robotsall", "slow")
SLOW_SECONDS = 4.0

# Anubis (wafw00f body rule  /\.within\.website/x/cmd/anubis/ ) on a page with almost no text
ANUBIS_THIN = ('<!doctype html><html><head><title>Making sure you are not a bot</title>'
               '<script src="/.within.website/x/cmd/anubis/static/js/main.mjs"></script></head>'
               '<body><p>One moment please.</p></body></html>')
# words matching wafw00f BODY rules for AireeCDN ( airee\.cloud ) and aeSecure ( aesecure_denied\.png )
POSSIBLE_FOOTER = (" Images delivered by cdn.airee.cloud. Old banner: /img/aesecure_denied.png was retired"
                   " last year.")
SIG_HEADERS = {"aeSecure-code": "1", "cf-ray": "8d1f2a3b4c5d6e7f-LHR"}

ROBOTS_DISALLOW_SITESCOUT = (
    "User-agent: SiteScout\n"
    "Disallow: /private/\n"
    "\n"
    "User-agent: *\n"
    "Allow: /\n"
)
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"


def rich_page(title: str = "Garden tools", next_href: str = "", footer: str = "") -> str:
    cards = "".join(
        f'<li class="product"><a href="/item/{i}"><h3>Garden tool model {i}</h3></a>'
        f'<p class="desc">Sturdy steel tool number {i} with an ash handle, made for daily use in '
        f'small and large gardens alike.</p><span class="price">$ {20 + i}.50</span></li>'
        for i in range(24))
    nav = "".join(f'<a href="/section/{s}">{s.title()}</a> ' for s in
                  ("spades", "rakes", "hoes", "shears", "gloves", "pots", "seeds", "soil"))
    return (f"<!doctype html><html><head><title>{title}</title></head><body>"
            f"<nav>{nav}</nav><main><h1>{title}</h1><p>Showing 24 products.</p>"
            f"<ul class='results'>{cards}</ul>"
            + (f'<a rel="next" class="next" href="{next_href}">Next page</a>' if next_href else "")
            + f"</main><footer>Local test shop{footer}</footer></body></html>")


APP_SHELL = ('<!doctype html><html><head><title>App</title>'
             '<script type="module" src="/assets/app.js"></script></head>'
             '<body><div id="root"></div><noscript>Please enable JavaScript.</noscript></body></html>')

APP_JS = ("const r=document.getElementById('root');"
          "r.innerHTML='<ul>'+Array.from({length:20},(_, i)=>'<li class=\"row\">Row item '+i+"
          "' with a little text</li>').join('')+'</ul>';")


class FakeSite:
    def __init__(self, mode: str):
        assert mode in START_PATH, mode
        self.mode = mode
        self.hits: list = []          # (path_with_query, user_agent) in arrival order
        self._lock = threading.Lock()
        site = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
                b = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(b)

            def do_HEAD(self):
                self.do_GET()

            def do_POST(self):
                ln = int(self.headers.get("Content-Length", 0) or 0)
                if ln:
                    self.rfile.read(ln)
                self.do_GET()

            def do_GET(self):
                ua = self.headers.get("User-Agent", "") or ""
                with site._lock:
                    site.hits.append((self.path, ua))
                code, body, ctype, *extra = site.respond(urlsplit(self.path).path, ua)
                try:
                    self._send(code, body, ctype, extra[0] if extra else None)
                except (ConnectionError, OSError):
                    pass      # client gave up (e.g. Ctrl+C check) - not our problem

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.start_url = self.base + START_PATH[mode]
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)

    # ------------------------------------------------------------------ routing
    def respond(self, path: str, ua: str):
        html = "text/html; charset=utf-8"
        if self.mode in PHASE2:
            return self.respond2(path, ua)
        if self.mode == "blockhonest":
            if path == "/robots.txt":
                return 404, "not found", "text/plain"
            if "sitescout" in ua.lower():
                return 403, "<html><body>Forbidden</body></html>", html
            if path == "/list":   # page-2 link so the run has a later fetch that must use Chrome
                return 200, rich_page(next_href="/list?page=2"), html
            return 404, "<html><body>not found</body></html>", html

        if path == "/robots.txt":
            if self.mode == "robots":
                return 200, ROBOTS_DISALLOW_SITESCOUT, "text/plain"
            return 404, "not found", "text/plain"
        if self.mode == "honest" and path == "/list":
            return 200, rich_page(), html
        if self.mode == "robots" and path in ("/private/list", "/public/list"):
            return 200, rich_page(), html
        if self.mode == "appshell":
            if path == "/app":
                return 200, APP_SHELL, html
            if path == "/assets/app.js":
                return 200, APP_JS, "application/javascript"
        return 404, "<html><body>not found</body></html>", html

    def respond2(self, path: str, ua: str):
        html = "text/html; charset=utf-8"
        if path == "/robots.txt":
            if self.mode == "robotsall":
                return 200, ROBOTS_DISALLOW_ALL, "text/plain"
            return 404, "not found", "text/plain"
        if path != "/list":
            return 404, "<html><body>not found</body></html>", html
        if self.mode == "sigs":
            return 200, rich_page(next_href="/list?page=2"), html, SIG_HEADERS
        if self.mode == "possible":
            return 200, rich_page(footer=POSSIBLE_FOOTER), html
        if self.mode == "thinbody":
            return 403, ANUBIS_THIN, html
        if self.mode == "blockall":
            return 403, "<html><body>Forbidden</body></html>", html
        if self.mode == "slow":
            time.sleep(SLOW_SECONDS)
        return 200, rich_page(), html

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()

    def snapshot(self) -> list:
        with self._lock:
            return list(self.hits)
