"""Logging: every scout.log line carries its level and ends with the message; console follows the flags.
    pytest tests/test_logging.py
"""
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from scout import cli  # noqa: E402
from scout.config import ScoutConfig  # noqa: E402
from scout.runner import run_scout  # noqa: E402

PAGE = ("<html><body><main>" + "".join(f'<a href="/p/{i}">Item {i}</a>' for i in range(14))
        + "</main></body></html>")


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = (b"" if self.path == "/robots.txt" else PAGE.encode())
        self.send_response(404 if self.path == "/robots.txt" else 200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/list"
    srv.shutdown()


def test_run_writes_scout_log_with_level_names(site, tmp_path):
    run_scout(ScoutConfig(url=site, out_dir=str(tmp_path), browser="never", pace=False, min_delay=0.0))
    lines = (tmp_path / "scout.log").read_text(encoding="utf-8").splitlines()
    assert lines
    assert all(re.search(r"\b(DEBUG|INFO|WARNING|ERROR)\b", ln) for ln in lines)
    assert any(" DEBUG " in ln and "request 1:" in ln for ln in lines)
    assert any(" INFO " in ln and ln.endswith("  - First look: response, guards, technology ...") for ln in lines)


def test_log_does_not_leak_between_runs(site, tmp_path):
    run_scout(ScoutConfig(url=site, out_dir=str(tmp_path / "a"), browser="never", pace=False, min_delay=0.0))
    first = (tmp_path / "a" / "scout.log").read_text(encoding="utf-8")
    run_scout(ScoutConfig(url=site, out_dir=str(tmp_path / "b"), browser="never", pace=False, min_delay=0.0))
    assert (tmp_path / "a" / "scout.log").read_text(encoding="utf-8") == first
    assert not any(isinstance(h, logging.FileHandler) for h in logging.getLogger("scout").handlers)


def test_quiet_hides_info_on_console(site, tmp_path, capsys):
    cli.main([site, "--quiet", "--no-pace", "--browser", "never", "--min-delay", "0", "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert "INFO" not in err
    assert " DEBUG " in (tmp_path / "scout.log").read_text(encoding="utf-8")


def test_default_console_shows_info_and_verbose_shows_debug(site, tmp_path, capsys):
    cli.main([site, "--no-pace", "--browser", "never", "--min-delay", "0", "--out", str(tmp_path / "a")])
    err = capsys.readouterr().err
    assert "INFO" in err and "DEBUG" not in err
    cli.main([site, "-v", "--no-pace", "--browser", "never", "--min-delay", "0", "--out", str(tmp_path / "b")])
    assert "DEBUG" in capsys.readouterr().err
