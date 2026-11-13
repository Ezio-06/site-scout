"""report.json: schema_version, JSON Schema, and `--output -` (stdout is ONLY the JSON).
    pytest tests/test_machine_output.py
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads((ROOT / "docs" / "report.schema.json").read_text(encoding="utf-8"))
PAGE = ("<html><body><main>" + "".join(f'<a href="/p/{i}">Item {i}</a>' for i in range(14))
        + "</main></body></html>")


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        gone = self.path == "/robots.txt"
        b = b"" if gone else PAGE.encode()
        self.send_response(404 if gone else 200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/list"
    srv.shutdown()


def run_cli(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(ROOT / "run_scout.py"), *args], capture_output=True,
                          text=True, encoding="utf-8", env=env, cwd=str(ROOT))


def test_schema_is_valid_and_pins_version():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema_version"] == {"const": 1, "description": SCHEMA["properties"][
        "schema_version"]["description"]}
    assert "schema_version" in SCHEMA["required"]


def test_output_dash_stdout_is_only_json(site, tmp_path):
    r = run_cli([site, "--output", "-", "--no-pace", "--browser", "never", "--min-delay", "0",
                 "--out", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)                 # the whole of stdout parses
    assert report["schema_version"] == 1
    jsonschema.validate(report, SCHEMA)
    assert "METHOD" in r.stderr and report["verdict"]["method"] in r.stderr
    assert (tmp_path / "scout_card.md").exists() and (tmp_path / "report.json").exists()
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report


def test_normal_run_keeps_summary_on_stdout(site, tmp_path):
    r = run_cli([site, "--no-pace", "--browser", "never", "--min-delay", "0", "--out", str(tmp_path)])
    assert r.returncode == 0 and "METHOD" in r.stdout


def test_written_report_validates(site, tmp_path):
    run_cli([site, "--no-pace", "--browser", "never", "--min-delay", "0", "--out", str(tmp_path)])
    jsonschema.validate(json.loads((tmp_path / "report.json").read_text(encoding="utf-8")), SCHEMA)
