"""Landmine: robots.txt is refused to the honest identity (403) but, shown to Chrome, disallows the page,
which also 403s the honest identity. The scout must read the rules BEFORE any comparison request and
must not request the disallowed page at all. Run: python tests/test_robots_identity.py"""
import json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
from scout.config import ScoutConfig          # noqa: E402
from scout.report import write                # noqa: E402
from scout.runner import run_scout            # noqa: E402

HITS = []
PAGE = "<html><body><main>" + "".join(f'<div class="product"><a href="/p/{i}"><h3>Item {i}</h3></a></div>'
                                      for i in range(12)) + "</main></body></html>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        honest = "SiteScout" in self.headers.get("User-Agent", "")
        HITS.append((self.path, "honest" if honest else "browser"))
        if self.path == "/robots.txt":
            return self.out(403, "no") if honest else self.out(200, "User-agent: *\nDisallow: /list\n", "text/plain")
        if honest:
            return self.out(403, "<html><body>Forbidden</body></html>")
        self.out(200, PAGE)

    def out(self, code, body, ct="text/html"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


srv = ThreadingHTTPServer(("127.0.0.1", 8801), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
out = tempfile.mkdtemp()
cfg = ScoutConfig(url="http://127.0.0.1:8801/list", out_dir=out, browser="never", pace=False, min_delay=0.0)
ctx = run_scout(cfg, echo=lambda *a: None)
write(ctx, 0)
card = (Path(out) / "scout_card.md").read_text(encoding="utf-8")
log = json.loads((Path(out) / "requests.log.json").read_text())
errs = []
if any(p == "/list" for p, _ in HITS):
    errs.append(f"the disallowed page was requested: {HITS}")
if any(e["url"].endswith("/list") for e in log):
    errs.append("requests.log.json lists the disallowed page")
if sum("comparison" in e["purpose"] for e in log) > 1:
    errs.append("more than one comparison request")
if "did not request" in card.lower() and any(e["url"].endswith("/list") for e in log):
    errs.append("card claims it did not request the page, but the log shows it did")
if not any("robots" in l.lower() and "disallow" in l.lower() for l in card.splitlines()):
    errs.append("card does not say the rules disallow the page")
print("FAIL: " + "; ".join(errs) if errs else "ok    robots refused to honest identity, disallowing to Chrome")
sys.exit(1 if errs else 0)
