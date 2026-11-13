"""Exit codes: 0 finished, 2 usage, 3 unreachable, 4 blocked everywhere, 5 robots forbids, 130 Ctrl+C.
    pytest tests/test_exit_codes.py
"""
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from scout import cli, context  # noqa: E402
from scout.fetcher import PoliteFetcher  # noqa: E402

PAGE = ("<html><body><main>" + "".join(f'<a href="/p/{i}">Item {i}</a>' for i in range(14))
        + "</main></body></html>")
ROBOTS = {
    "/named/": "User-agent: SiteScout\nDisallow: /\n",
    "/star/": "User-agent: *\nDisallow: /\n",
}


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            return self.out(404, "")
        if self.path.startswith("/blocked/"):
            return self.out(403, "<html><body>Forbidden</body></html>")
        self.out(200, PAGE)

    def out(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class RobotsSite(Site):
    def do_GET(self):
        if self.path == "/robots.txt":
            return self.out(200, self.server.robots)
        return super().do_GET()


def serve(handler, robots=None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.robots = robots
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(scope="module")
def site():
    srv, url = serve(Site)
    yield url
    srv.shutdown()


FAST = ["--no-pace", "--browser", "never", "--min-delay", "0"]


def run(url, tmp_path, *extra):
    return cli.main([url, *FAST, "--quiet", "--out", str(tmp_path), *extra])


def test_finished_run_is_0(site, tmp_path):
    assert run(site + "/list", tmp_path) == 0


def test_bad_flag_and_missing_url_are_2():
    for args in (["--no-such-flag"], []):
        r = subprocess.run([sys.executable, str(ROOT / "run_scout.py"), *args], capture_output=True,
                           text=True, cwd=str(ROOT))
        assert r.returncode == 2, r.stderr


def test_unreachable_start_page_is_3(tmp_path, monkeypatch):
    monkeypatch.setattr(context.time, "sleep", lambda s: None)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert run(f"http://127.0.0.1:{port}/list", tmp_path) == 3


def test_blocked_for_every_identity_is_4(site, tmp_path):
    assert run(site + "/blocked/x", tmp_path / "a") == 4
    assert run(site + "/blocked/x", tmp_path / "b", "--identity", "browser") == 4


@pytest.mark.parametrize("prefix", ["/named/", "/star/"])
def test_robots_disallowing_start_page_is_5(prefix, tmp_path):
    srv, url = serve(RobotsSite, ROBOTS[prefix])
    try:
        assert run(url + prefix + "list", tmp_path) == 5
    finally:
        srv.shutdown()


def test_ctrl_c_while_loading_start_page_writes_card_and_is_130(site, tmp_path, monkeypatch):
    real = PoliteFetcher.get

    def get(self, url, **kw):
        if kw.get("purpose") == "main page":
            raise KeyboardInterrupt
        return real(self, url, **kw)

    monkeypatch.setattr(PoliteFetcher, "get", get)
    assert run(site + "/list", tmp_path) == 130
    assert (tmp_path / "scout_card.md").exists() and (tmp_path / "report.json").exists()


def test_help_lists_every_exit_code():
    r = subprocess.run([sys.executable, str(ROOT / "run_scout.py"), "--help"], capture_output=True,
                       text=True, cwd=str(ROOT))
    for code in ("0", "2", "3", "4", "5", "130"):
        assert any(ln.split()[:1] == [code] for ln in r.stdout.splitlines()), code
