"""Guard signature matcher: passive, merged with the hand tables, never crashes on a bad file.
    pytest tests/test_guard_signatures.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from scout import guards  # noqa: E402
from scout.config import ScoutConfig  # noqa: E402
from scout.report import write  # noqa: E402
from scout.runner import run_scout  # noqa: E402

RICH = ("<html><body><main>" + "".join(f'<div class="product"><a href="/p/{i}"><h3>Item {i}</h3></a></div>'
                                       for i in range(14)) + "</main></body></html>")
THIN = "<html><body>denied</body></html>"


@pytest.fixture(autouse=True)
def fresh_cache():
    guards.load_signatures.cache_clear()
    yield
    guards.load_signatures.cache_clear()


def use_signature_file(monkeypatch, tmp_path, content):
    """Point the loader at a copy of the package data folder holding `content` as the signatures."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "guard_signatures.json").write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr(guards.resources, "files", lambda pkg: tmp_path)
    guards.load_signatures.cache_clear()


# ------------------------------------------------------------------ unit
def test_header_rule_is_present_even_on_rich_page():
    present, possible = guards.match_signatures(200, {"aeSecure-code": "1"}, RICH)
    assert "aeSecure" in present and not possible


def test_header_name_is_case_insensitive():
    present, _ = guards.match_signatures(200, {"AESECURE-CODE": "1"}, RICH)
    assert "aeSecure" in present


def test_body_only_rule_on_rich_page_is_only_possible():
    present, possible = guards.match_signatures(200, {}, RICH + "<p>served by airee.cloud</p>")
    assert not any("Airee" in n for n in present)
    assert any("Airee" in n for n in possible)


def test_body_only_rule_on_thin_page_is_present():
    present, possible = guards.match_signatures(403, {}, "<html><body>airee.cloud</body></html>")
    assert any("Airee" in n for n in present) and not possible


def test_status_condition_in_a_mixed_group():
    present, _ = guards.match_signatures(403, {"Server": "LiteSpeed"}, THIN)
    assert "LiteSpeed" in present
    present, _ = guards.match_signatures(200, {"Server": "LiteSpeed"}, THIN)
    assert "LiteSpeed" not in present


def test_vendor_reported_once_across_hand_tables_and_signatures():
    present, _ = guards.detect(200, {"CF-RAY": "abc", "Server": "cloudflare"}, [], RICH)
    assert [n for n in present if n.lower().startswith("cloudflare")] == ["Cloudflare"]


def test_possible_is_not_listed_when_already_present():
    present, possible = guards.detect(200, {"aeSecure-code": "1"}, [], RICH + "aesecure_denied.png")
    assert "aeSecure" in present and "aeSecure" not in possible


def test_invalid_regex_is_skipped_and_counted(monkeypatch, tmp_path):
    use_signature_file(monkeypatch, tmp_path, {"guards": {
        "broken": {"name": "Broken (Co)", "rules": [[{"t": "header", "name": "X-Bad", "re": "([unclosed"}]]},
        "fine": {"name": "Fine (Co)", "rules": [[{"t": "header", "name": "X-Fine", "re": ".+"}]]},
    }})
    assert guards.skipped_rules() == 1
    present, _ = guards.match_signatures(200, {"X-Fine": "1", "X-Bad": "1"}, RICH)
    assert present == ["Fine"]


def test_empty_guards_file_is_accepted(monkeypatch, tmp_path):
    use_signature_file(monkeypatch, tmp_path, {"guards": {}})
    assert guards.skipped_rules() == 0
    assert guards.match_signatures(200, {"aeSecure-code": "1"}, THIN) == ([], [])


def test_status_rule_reads_code_key(monkeypatch, tmp_path):
    use_signature_file(monkeypatch, tmp_path, {"guards": {
        "s": {"name": "Status guard (Co)", "rules": [[{"t": "status", "code": 405}]]}}})
    assert guards.match_signatures(405, {}, RICH)[0] == ["Status guard"]
    assert guards.match_signatures(200, {}, RICH)[0] == []


def test_shipped_file_loads_with_no_skipped_rules():
    rules, skipped = guards.load_signatures()
    assert len(rules) > 150 and skipped == 0


# ------------------------------------------------------------------ whole run against a local fake site
class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            return self.out(404, "")
        extra = {}
        if self.path.startswith("/aesecure"):
            extra = {"aeSecure-code": "1"}
        body = {"/aesecure": RICH, "/mention": RICH + "<p>cdn: airee.cloud</p>",
                "/thin403": THIN + "<img src='aesecure_denied.png'>"}.get(self.path.split("?")[0], RICH)
        self.out(403 if self.path.startswith("/thin403") else 200, body, extra)

    def out(self, code, body, headers=None):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def scout(url, out, **kw):
    cfg = ScoutConfig(url=url, out_dir=str(out), browser="never", pace=False, min_delay=0.0, **kw)
    ctx = run_scout(cfg, echo=lambda *a: None)
    write(ctx, 0.0)
    return ctx, json.loads((Path(out) / "report.json").read_text(encoding="utf-8")), \
        (Path(out) / "scout_card.md").read_text(encoding="utf-8")


def test_run_header_guard_on_rich_page(site, tmp_path):
    ctx, rep, card = scout(site + "/aesecure", tmp_path / "a")
    assert "aeSecure" in rep["facts"]["guards"]
    assert any("aeSecure" in ln and "seen" in ln for ln in card.splitlines())


def test_run_body_mention_on_rich_page_is_only_possible(site, tmp_path):
    ctx, rep, card = scout(site + "/mention", tmp_path / "m")
    assert not any("Airee" in g for g in rep["facts"]["guards"] + rep["facts"].get("hard_guard", []))
    _, plain, _ = scout(site + "/plain", tmp_path / "p")
    assert rep["verdict"]["tier"] == plain["verdict"]["tier"]
    lines = [ln for ln in card.splitlines() if "Airee" in ln]
    assert lines and all("possible" in ln for ln in lines)


def test_run_thin_403_body_rule_is_present(site, tmp_path):
    ctx, rep, card = scout(site + "/thin403", tmp_path / "t")
    assert "aeSecure" in rep["facts"]["guards"]


def test_run_bad_rule_is_counted_in_report(site, tmp_path, monkeypatch):
    (tmp_path / "pkg").mkdir()
    use_signature_file(monkeypatch, tmp_path / "pkg", {"guards": {"b": {"name": "B", "rules": [
        [{"t": "body", "re": "(("}]]}}})
    ctx, rep, card = scout(site + "/aesecure", tmp_path / "r")
    assert rep["signature_rules_skipped"] == 1


def test_signatures_add_no_requests(site, tmp_path, monkeypatch):
    def urls(out):
        return [e["url"] for e in json.loads((Path(out) / "requests.log.json").read_text())]
    scout(site + "/aesecure", tmp_path / "with")
    (tmp_path / "pkg").mkdir()
    use_signature_file(monkeypatch, tmp_path / "pkg", {"guards": {}})
    scout(site + "/aesecure", tmp_path / "without")
    assert urls(tmp_path / "with") == urls(tmp_path / "without")
