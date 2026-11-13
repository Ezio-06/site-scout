r"""Hidden acceptance checks for spec phase2-quality (checks 1-11; 12 is manual) plus the
phase-1 regression checks (p1, p3-p8; p2/p10 are folded into 11, p9 into 9).

    python tests/hidden/run_checks.py            everything
    python tests/hidden/run_checks.py 3 5 p4     only checks 3, 5 and phase-1 check 4

Run with the project venv's Python (checks call this same interpreter, and look for
pytest / ruff next to it). Only local 127.0.0.1 fake sites are scouted. p1 and p8 need
PyPI access for pip build dependencies. Check 9 runs the full landmine suite three times
(pytest, pytest without Chromium, run_tests.py) - expect several minutes.

INTERFACE ASSUMPTIONS (what the builder must provide - everything else is read from the spec)
  present guards : report.json["facts"]["guards"] = list of guard names judged PRESENT
                   (hand tables + signatures merged, each vendor once). "Possible" guards
                   must NOT be in it, nor in facts["hard_guard"].
  card wording   : a present guard appears on a scout_card.md line containing its name and
                   the word "seen" (not "possible"); a possible-only guard may appear only on
                   lines containing the word "possible".
  skipped count  : report.json has the key "signature_rules_skipped" (int, any depth;
                   first occurrence used) = number of signature rules skipped as invalid.
  signature hook : NONE. Checks 4/5 copy the scout/ package into a temp folder, edit the
                   copy's scout/data/guard_signatures.json, and run that copy (sys.path[0]).
                   So the matcher must load the file via importlib.resources from its own
                   package (spec), and must accept "guards": {} (= matching off, check 5).
  scout.log      : every line of a record carries its level name (DEBUG / INFO / WARNING),
                   message last on the line.
  --output -     : stdout is exactly one JSON document; summary (contains verdict.method)
                   on stderr.
  schema         : docs/report.schema.json must reject schema_version 2 and a missing
                   schema_version.
  exit codes     : README.md and --help each mention 0, 2, 3, 4, 5 and 130.
  pytest ids     : every key of tests/run_tests.py CASES appears in a collected node id;
                   matcher unit tests live in a test file whose name contains sig, guard or waf.
  ci.yml         : parsed with PyYAML if installed, else with a small built-in YAML subset
                   parser (block maps/lists, flow lists, | and > blocks). Unquoted 3.10 is read
                   as the number 3.1, as YAML does, and fails.

Phase-1 string assumptions still used by p3-p6 (unchanged): honest UA starts with
"SiteScout/<VERSION>" + repo URL; log "identity"; one "comparison" purpose; card wording.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from collections import Counter
from itertools import product
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

from hidden_sites import FakeSite  # noqa: E402

VERSION = "1.3.0"
REPO_URL = "https://github.com/Ezio-06/site-scout"
AUTHOR = "Ezio-06 <Ezio-06@users.noreply.github.com>"
# built from pieces so this file never spells the private words / paths itself
# private terms live in an untracked local file (never committed); see check 7
_PT = REPO / "private_terms.txt"
_PL = _PT.read_text(encoding="utf-8").splitlines() if _PT.exists() else []
_FW = next((x[4:] for x in _PL if x.startswith("dir:")), "private-folder-not-configured")
EXCLUDED = [_FW + "/", "holdout/", "scouts/", "old_versions/", "tests/out/", "research/", "specs/", ".venv/"]
_W = [x[5:] for x in _PL if x.startswith("word:")] or ["(?!)"]
PRIVATE_RE = "|".join(_W)
PRIVATE_PATH_RE = _FW + "|" + _FW
EXIT_CODES = (0, 2, 3, 4, 5, 130)

ENV = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    ENV.pop(k, None)

TMP_ROOT = Path(tempfile.mkdtemp(prefix="scout_hidden_"))
BIN = Path(sys.executable).parent
_CACHE: dict = {}
SCOUT_FLAGS = ["--no-pace", "--browser", "never", "--min-delay", "0"]


class Fail(Exception):
    pass


def run3(cmd, cwd=None, timeout=300, env=None):
    """-> (returncode, stdout, stderr)"""
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env or ENV,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout or b""
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return -999, f"TIMEOUT after {timeout}s\n{out}", ""
    except FileNotFoundError as e:
        return -998, "", f"not found: {e}"


def run(cmd, cwd=None, timeout=300, env=None):
    rc, o, e = run3(cmd, cwd, timeout, env)
    return rc, o + e


def tail(s: str, n: int = 15) -> str:
    lines = s.strip().splitlines()
    return "\n      | " + "\n      | ".join(lines[-n:]) if lines else " (no output)"


def need(cond, msg):
    if not cond:
        raise Fail(msg)


def tool(name: str):
    """Executable next to this Python (the venv), else None."""
    p = BIN / (name + (".exe" if os.name == "nt" else ""))
    return p if p.exists() else None


# ------------------------------------------------------------------ helpers
def has_git() -> bool:
    return (REPO / ".git").exists()


def clean_copy() -> Path:
    """Copy of the repo as a public clone would see it (tracked + not-ignored files),
    so pip builds never write build/ or *.egg-info into the real folder."""
    if "copy" in _CACHE:
        return _CACHE["copy"]
    dst = TMP_ROOT / "src"
    files = None
    if has_git():
        rc, out = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO)
        if rc == 0:
            files = [f for f in out.split("\0") if f.strip()]
    if files:
        for f in files:
            s = REPO / f
            if s.is_file():
                d = dst / f
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
    else:
        skip = {".git", ".venv", _FW, "holdout", "scouts", "old_versions", "research",
                "specs", "__pycache__", "build", "dist", "out"}

        def ignore(dirpath, names):
            return [n for n in names if n in skip or n.endswith(".egg-info") or n.endswith(".pyc")]
        shutil.copytree(REPO, dst, ignore=ignore)
    _CACHE["copy"] = dst
    return dst


def ident_kind(entry: dict) -> str:
    v = str(entry.get("identity", "")).lower()
    if "honest" in v or "sitescout" in v:
        return "honest"
    if "browser" in v or "chrome" in v:
        return "browser"
    return "other" if v else "missing"


def is_chrome(ua: str) -> bool:
    return "Chrome/" in ua and "sitescout" not in ua.lower()


def is_exempt(ua: str) -> bool:
    return ua.lower().startswith("python-requests")


def claim_rows(card: str) -> list:
    rows = []
    for ln in card.splitlines():
        if ln.strip().startswith("|") and "honest bot" in ln.lower():
            m = re.search(r"\*\*\s*([a-zA-Z]+)\s*\*\*", ln)
            rows.append((ln.strip(), (m.group(1).lower() if m else "")))
    return rows


def same_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def find_key(obj, key):
    """First value stored under `key` anywhere in a JSON tree, or KeyError."""
    stack = [obj]
    while stack:
        o = stack.pop(0)
        if isinstance(o, dict):
            if key in o:
                return o[key]
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    raise KeyError(key)


class Run:
    """One finished scout run: rc, console text, card, log, report, server hits."""

    def __init__(self, rc, out, err, outdir: Path, hits):
        self.rc, self.stdout, self.stderr, self.outdir, self.hits = rc, out, err, outdir, hits
        self.text = out + err
        cp, lp, rp = outdir / "scout_card.md", outdir / "requests.log.json", outdir / "report.json"
        self.card = cp.read_text(encoding="utf-8") if cp.exists() else None
        self.log = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else None
        self.report = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None

    def require(self, rcs=(0,)):
        need(self.rc in rcs, f"scout exited {self.rc}, want {' or '.join(map(str, rcs))}:{tail(self.text)}")
        need(self.card is not None, f"no scout_card.md in {self.outdir}:{tail(self.text)}")
        need(self.log is not None, f"no requests.log.json in {self.outdir}")
        need(isinstance(self.log, list), "requests.log.json is not a list")
        need(self.report is not None, f"no report.json in {self.outdir}")
        return self

    def guards(self, key="guards") -> list:
        g = ((self.report or {}).get("facts") or {}).get(key) or []
        return [str(x) for x in g]


def scout(site: FakeSite, name: str, extra=(), flags=None, pkg: Path | None = None, url=None) -> Run:
    """Run the scout (repo code, or a temp package copy when pkg is given) against a fake site."""
    out = TMP_ROOT / f"out_{name}"
    args = [url or site.start_url, "--out", out, *(SCOUT_FLAGS if flags is None else flags), *extra]
    if pkg is None:
        cmd, cwd = [sys.executable, REPO / "run_scout.py", *args], REPO
    else:
        cmd, cwd = [sys.executable, runner_script(), pkg, *args], pkg
    rc, o, e = run3(cmd, cwd=cwd, timeout=400)
    return Run(rc, o, e, out, site.snapshot() if site else [])


def runner_script() -> Path:
    """Runs scout.cli.main from a temp package copy, refusing to fall back to the repo's copy."""
    p = TMP_ROOT / "runner" / "run_copy.py"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "import os, sys\n"
            "root = os.path.realpath(sys.argv[1])\n"
            "sys.path.insert(0, root)\n"
            "import scout\n"
            "got = os.path.realpath(scout.__file__)\n"
            "if not got.startswith(root + os.sep):\n"
            "    sys.stderr.write('HIDDEN-CHECK: imported scout from ' + got + ', not the temp copy\\n')\n"
            "    sys.exit(97)\n"
            "from scout.cli import main\n"
            "sys.exit(main(sys.argv[2:]))\n", encoding="utf-8")
    return p


def pkg_copy(name: str, mutate=None) -> Path:
    """Temp folder holding a copy of scout/ whose guard_signatures.json is passed through mutate()."""
    root = TMP_ROOT / f"pkg_{name}"
    if root.exists():
        return root
    shutil.copytree(REPO / "scout", root / "scout",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sig = root / "scout" / "data" / "guard_signatures.json"
    if mutate:
        data = json.loads(sig.read_text(encoding="utf-8"))
        mutate(data)
        sig.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return root


def shipped_signatures() -> dict:
    return json.loads((REPO / "scout" / "data" / "guard_signatures.json").read_text(encoding="utf-8"))


def python_invalid_rules(data: dict) -> int:
    n = 0
    for g in (data.get("guards") or {}).values():
        for grp in g.get("rules") or []:
            for c in grp:
                if "re" in c:
                    try:
                        re.compile(c["re"], re.I)
                    except re.error:
                        n += 1
    return n


def named(names, word) -> list:
    return [n for n in names if word in n.lower()]


def card_lines(card: str, word: str) -> list:
    return [ln.strip() for ln in (card or "").splitlines() if word in ln.lower()]


# ------------------------------------------------------------------ phase 2 checks
def check1():
    """Header aeSecure-code on a rich page -> aeSecure present (seen); vendors reported once."""
    with FakeSite("sigs") as site:
        r = scout(site, "c1_sigs").require()
    present = r.guards()
    need(named(present, "aesecure"), f"aeSecure not in report.json facts.guards: {present}")
    lines = [ln for ln in card_lines(r.card, "aesecure") if "possible" not in ln.lower()]
    need(lines, "scout_card.md never names aeSecure (outside a 'possible' line)")
    need(any("seen" in ln.lower() for ln in lines),
         f"card names aeSecure but not as 'seen': {lines[:3]}")
    dup = [n for n, c in Counter(x.lower() for x in present).items() if c > 1]
    need(not dup, f"names reported more than once in facts.guards: {dup}")
    cf = [n for n in named(present, "cloudflare")
          if not re.search(r"bot management|challenge|turnstile", n, re.I)]
    need(len(cf) == 1, f"cf-ray header must give ONE Cloudflare entry (hand table + signatures merged), "
                       f"got {cf}")
    return f"facts.guards={present}; card: {lines[0][:100]!r}"


def check2():
    """Body-only signature words on a rich page: 'possible' at most, verdict unchanged."""
    with FakeSite("honest") as site:
        ctl = scout(site, "c2_control").require()
    with FakeSite("possible") as site:
        r = scout(site, "c2_possible").require()
    need(not ctl.guards(), f"plain local rich page must have no present guards, got {ctl.guards()}")
    for key in ("guards", "hard_guard"):
        bad = named(r.guards(key), "aesecure") + named(r.guards(key), "airee")
        need(not bad, f"body-only match on a rich page counted as present in facts.{key}: {bad}")
    loose = [ln for w in ("aesecure", "airee") for ln in card_lines(r.card, w) if "possible" not in ln.lower()]
    need(not loose, f"card names aeSecure/Airee on a line without 'possible': {loose[:3]}")
    va, vb = ctl.report.get("verdict") or {}, r.report.get("verdict") or {}
    diff = {k: (va.get(k), vb.get(k)) for k in ("tier", "method", "difficulty", "effort", "effort_bumps")
            if va.get(k) != vb.get(k)}
    need(not diff, f"verdict changed by body-only words (control, with words): {diff}")
    for run_ in (ctl, r):
        need(not (run_.report.get("facts") or {}).get("blocked_at_start"),
             f"rich page treated as blocked: {(run_.report.get('facts') or {}).get('blocked_at_start')}")
    poss = card_lines(r.card, "airee") + card_lines(r.card, "aesecure")
    return (f"tier {vb.get('tier')} = control {va.get('tier')}; not present; "
            f"possible lines: {len(poss)}" + (f" e.g. {poss[0][:80]!r}" if poss else " (none - allowed)"))


def check3():
    """Thin 403 page matching a wafw00f body rule -> guard present."""
    with FakeSite("thinbody") as site:
        r = scout(site, "c3_thin").require(rcs=(0, 4))
    present = r.guards()
    need(named(present, "anubis"), f"Anubis (body rule on a thin 403 page) not in facts.guards: {present}")
    lines = [ln for ln in card_lines(r.card, "anubis") if "possible" not in ln.lower()]
    need(lines, "card does not name Anubis as present")
    return f"rc {r.rc}; facts.guards={present}"


BROKEN_GUARD = "anquanbao"
BROKEN_RE = "aqb_cc/error/("


def _break_one(data):
    for grp in data["guards"][BROKEN_GUARD]["rules"]:
        for c in grp:
            if c.get("t") == "body":
                c["re"] = BROKEN_RE
                return
    raise RuntimeError("no body rule to break")


def check4():
    """Signatures file with one invalid regex: run completes, report counts 1 skipped rule."""
    try:
        re.compile(BROKEN_RE)
        raise RuntimeError("test setup: BROKEN_RE compiles")
    except re.error:
        pass
    base = python_invalid_rules(shipped_signatures())
    good, bad = pkg_copy("orig"), pkg_copy("broken", _break_one)
    with FakeSite("sigs") as site:
        r0 = scout(site, "c4_orig", pkg=good)
    with FakeSite("sigs") as site:
        r1 = scout(site, "c4_broken", pkg=bad)
    for label, r in (("unmodified copy", r0), ("broken copy", r1)):
        need(r.rc != 97, f"{label}: could not run the temp package copy:{tail(r.text)}")
        r.require()
        need("Traceback" not in r.text, f"{label}: traceback on console:{tail(r.text, 25)}")
    counts = []
    for label, r in (("unmodified", r0), ("broken", r1)):
        try:
            counts.append(find_key(r.report, "signature_rules_skipped"))
        except KeyError:
            raise Fail(f"{label} run: report.json has no 'signature_rules_skipped' key")
    need(all(isinstance(c, int) and not isinstance(c, bool) for c in counts), f"counts not ints: {counts}")
    need(counts[0] == base, f"shipped file: {counts[0]} rules skipped, Python's re finds {base} invalid")
    need(counts[1] == base + 1, f"one broken regex: want {base + 1} skipped, report says {counts[1]}")
    need(named(r1.guards(), "aesecure"), f"other signatures stopped working after one bad regex: {r1.guards()}")
    return f"skipped: shipped {counts[0]}, with one bad regex {counts[1]}; aeSecure still found; no traceback"


def _empty(data):
    data["guards"] = {}


def _req_list(r: Run):
    return [(str(e.get("url")), str(e.get("purpose")), str(e.get("identity")), e.get("status")) for e in r.log]


def check5():
    """Same request list with and without signature matching (empty signatures file = off)."""
    on, off = pkg_copy("orig"), pkg_copy("empty", _empty)
    ev = []
    for mode, rcs, word in (("sigs", (0,), "aesecure"), ("thinbody", (0, 4), "anubis")):
        runs = []
        for label, pkg in (("on", on), ("off", off)):
            with FakeSite(mode) as site:
                r = scout(site, f"c5_{mode}_{label}", pkg=pkg)
            need(r.rc != 97, f"could not run the temp package copy:{tail(r.text)}")
            r.require(rcs)
            runs.append(r)
        a, b = runs
        need(named(a.guards(), word), f"{mode}: matching ON did not find {word} - comparison meaningless "
                                      f"({a.guards()})")
        need(not named(b.guards(), word), f"{mode}: empty signatures file still found {word} "
                                          f"({b.guards()}) - switch did not turn matching off")
        la, lb = _req_list(a), _req_list(b)
        if la != lb:
            extra = [x for x in la if x not in lb]
            raise Fail(f"{mode}: requests.log.json differs: {len(la)} with matching vs {len(lb)} without; "
                       f"only-with-matching: {extra[:5]}")
        ha, hb = Counter(a.hits), Counter(b.hits)
        need(ha == hb, f"{mode}: server saw different requests: extra with matching "
                       f"{list((ha - hb).elements())[:5]}, missing {list((hb - ha).elements())[:5]}")
        ev.append(f"{mode}: {len(la)} logged / {sum(ha.values())} served, identical")
    return "; ".join(ev)


_LEVEL = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
_SUMMARY_WORDS = re.compile(r"scout card|method|why|pace|effort", re.I)


def _log_tails(log_text: str, level: str, outdir: Path) -> list:
    tails = []
    for ln in log_text.splitlines():
        m = _LEVEL.search(ln)
        if not m or m.group(1) != level:
            continue
        msg = ln[m.end():].strip(" :-|]\t")
        if len(msg) < 12 or str(outdir) in msg or str(outdir.name) in msg or _SUMMARY_WORDS.search(msg):
            continue
        tails.append(msg[-20:])
    return tails


def _console_has(console: str, tails: list) -> list:
    ends = [ln.rstrip() for ln in console.splitlines()]
    return [t for t in tails if any(e.endswith(t) for e in ends)]


def check6():
    """--quiet hides INFO; scout.log (DEBUG) always written; -v shows DEBUG; no stray print()."""
    runs = {}
    for label, extra in (("default", []), ("quiet", ["--quiet"]), ("verbose", ["-v"])):
        with FakeSite("honest") as site:
            runs[label] = scout(site, f"c6_{label}", extra).require()
    logs = {}
    for label, r in runs.items():
        p = r.outdir / "scout.log"
        need(p.exists(), f"{label} run: no scout.log in the output folder {r.outdir}")
        logs[label] = p.read_text(encoding="utf-8", errors="replace")
    q, ql = runs["quiet"], logs["quiet"]
    need(re.search(r"\bDEBUG\b", ql), "scout.log of the --quiet run has no DEBUG lines")
    info = _log_tails(ql, "INFO", q.outdir)
    need(info, "scout.log has no INFO records to compare with (expected progress messages at INFO)")
    leaked = _console_has(q.text, info)
    need(not leaked, f"--quiet console still shows INFO messages, e.g. {leaked[:3]}:{tail(q.text, 10)}")
    d = runs["default"]
    shown = _console_has(d.text, _log_tails(logs["default"], "INFO", d.outdir))
    need(shown, f"default run: no INFO message from scout.log reached the console:{tail(d.text, 10)}")
    v = runs["verbose"]
    dbg = _console_has(v.text, _log_tails(logs["verbose"], "DEBUG", v.outdir))
    need(dbg, f"-v run: no DEBUG message from scout.log reached the console:{tail(v.text, 10)}")
    offenders = []
    for f in sorted((REPO / "scout").rglob("*.py")):
        if f.name == "cli.py" and f.parent.name == "scout":
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ln.lstrip().startswith("#"):
                continue
            if re.search(r"(?<![\w.])print\s*\(|=\s*print\b(?!_)", ln):
                offenders.append(f"{f.relative_to(REPO).as_posix()}:{i}: {ln.strip()[:70]}")
    need(not offenders, f"print left outside scout/cli.py ({len(offenders)}): {offenders[:6]}")
    return (f"quiet console {len(q.text.splitlines())} lines, 0 of {len(info)} INFO msgs leaked; "
            f"default shows {len(shown)} INFO, -v shows {len(dbg)} DEBUG; no print outside cli.py")


def _validator():
    try:
        import jsonschema
    except ImportError:
        raise Fail("tool missing: jsonschema not installed in this Python (pip install -e .[dev])")
    sp = REPO / "docs" / "report.schema.json"
    need(sp.exists(), "no docs/report.schema.json")
    schema = json.loads(sp.read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    try:
        cls.check_schema(schema)
    except jsonschema.SchemaError as e:
        raise Fail(f"docs/report.schema.json is not a valid JSON Schema: {e.message}")
    return cls(schema)


def _errors(val, doc) -> list:
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:120]}" for e in val.iter_errors(doc)]


def check7():
    """--output -: stdout is the JSON report (schema_version 1, valid); summary on stderr."""
    val = _validator()
    with FakeSite("sigs") as site:
        r = scout(site, "c7_stdout", ["--output", "-"])
    need(r.rc == 0, f"--output - exited {r.rc}:{tail(r.text)}")
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise Fail(f"stdout is not one JSON document ({e}); stdout starts: {r.stdout[:200]!r}")
    need(isinstance(doc, dict), "stdout JSON is not an object")
    sv = doc.get("schema_version")
    need(sv == 1 and type(sv) is int, f"schema_version is {sv!r}, want the integer 1")
    errs = _errors(val, doc)
    need(not errs, f"stdout report fails the schema ({len(errs)}): {errs[:4]}")
    method = str((doc.get("verdict") or {}).get("method") or "")
    need(method and method in r.stderr, f"human summary (verdict method {method!r}) not on stderr:{tail(r.stderr)}")
    need((r.outdir / "scout_card.md").exists(), "scout_card.md not written with --output -")
    for bad_doc, what in ((dict(doc, schema_version=2), "schema_version 2"),
                          ({k: v for k, v in doc.items() if k != "schema_version"}, "no schema_version")):
        need(_errors(val, bad_doc), f"schema accepts a report with {what} - it does not pin the version")
    shapes = []
    for mode, name, rcs in (("blockall", "c7_blocked", (4,)), ("robotsall", "c7_robots", (5,))):
        with FakeSite(mode) as site:
            rr = scout(site, name, ["--output", "-"])
        need(rr.rc in rcs, f"{mode} run exited {rr.rc}, want {rcs}:{tail(rr.text)}")
        if rr.stdout.strip():
            try:
                d2 = json.loads(rr.stdout)
            except json.JSONDecodeError as e:
                raise Fail(f"{mode} (exit {rr.rc}): stdout is not JSON ({e}): {rr.stdout[:150]!r}")
            errs = _errors(val, d2)
            need(not errs, f"{mode} report fails the schema: {errs[:4]}")
            shapes.append(f"{mode} valid")
        else:
            shapes.append(f"{mode}: no stdout JSON (allowed)")
    return f"stdout JSON valid, schema_version 1, summary on stderr, schema pins version; {', '.join(shapes)}"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _interrupt_script() -> Path:
    p = TMP_ROOT / "runner" / "interrupt.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    # Ctrl+C simulated with _thread.interrupt_main(): KeyboardInterrupt in the main thread,
    # fired after import while the scout waits on a slow start page (works on Windows too).
    p.write_text(
        "import _thread, sys, threading\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from scout.cli import main\n"
        "threading.Timer(float(sys.argv[2]), _thread.interrupt_main).start()\n"
        "sys.exit(main(sys.argv[3:]))\n", encoding="utf-8")
    return p


def check8():
    """Exit codes 0/2/3/4/5 (+130 on Ctrl+C), documented in README and --help."""
    res, bad = [], []

    def expect(label, got, want, text=""):
        res.append(f"{label}={got}")
        if got != want:
            bad.append(f"{label}: exit {got}, want {want}{tail(text, 6)}")

    with FakeSite("honest") as site:
        r = scout(site, "c8_ok")
    expect("normal", r.rc, 0, r.text)
    rc, out = run([sys.executable, REPO / "run_scout.py", "--no-such-flag", "http://127.0.0.1:9/"], cwd=REPO)
    expect("badflag", rc, 2, out)
    rc, out = run([sys.executable, REPO / "run_scout.py"], cwd=REPO)
    expect("no-url", rc, 2, out)
    port = _free_port()
    rc, out = run([sys.executable, REPO / "run_scout.py", f"http://127.0.0.1:{port}/list", "--out",
                   TMP_ROOT / "out_c8_unreach", *SCOUT_FLAGS], cwd=REPO, timeout=400)
    expect("unreachable", rc, 3, out)
    for name, extra in (("blockall", []), ("blockall-browser", ["--identity", "browser"])):
        with FakeSite("blockall") as site:
            r = scout(site, f"c8_{name}", extra)
        expect(name, r.rc, 4, r.text)
    for mode in ("robots", "robotsall"):
        with FakeSite(mode) as site:
            r = scout(site, f"c8_{mode}")
            hits = site.snapshot()
        expect(mode, r.rc, 5, r.text)
        start = [p for p, _ in hits if same_url(site.base + p.split("?")[0], site.start_url)]
        if start:
            bad.append(f"{mode}: disallowed start page was requested {len(start)}x")
    with FakeSite("slow") as site:
        out_i = TMP_ROOT / "out_c8_interrupt"
        rc, out = run([sys.executable, _interrupt_script(), REPO, "2.0", site.start_url, "--out", out_i,
                       *SCOUT_FLAGS], cwd=REPO, timeout=180)
    expect("ctrl-c", rc, 130, out)
    if not (out_i / "scout_card.md").exists():
        bad.append("ctrl-c: no scout_card.md written")
    rc, help_text = run([sys.executable, REPO / "run_scout.py", "--help"], cwd=REPO)
    readme = (REPO / "README.md").read_text(encoding="utf-8", errors="replace")
    for label, text in (("--help", help_text), ("README.md", readme)):
        miss = [c for c in EXIT_CODES if not re.search(rf"(?<![\w.]){c}(?![\w.])", text)]
        if "exit" not in text.lower() or miss:
            bad.append(f"{label} does not document exit codes (missing {miss})")
    need(not bad, "; ".join(bad) + f"  [all: {', '.join(res)}]")
    return ", ".join(res) + "; documented in --help and README"


def check9():
    """pytest from the repo root passes (and skips browser cases without Chromium); run_tests.py passes."""
    pt = tool("pytest")
    need(pt, f"tool missing: no pytest next to {sys.executable} (pip install -e .[dev])")
    base = [pt, "-p", "no:cacheprovider"]
    rc, out = run([*base, "--collect-only", "-q"], cwd=REPO, timeout=300)
    need(rc == 0, f"pytest --collect-only failed (rc {rc}):{tail(out, 20)}")
    ids = [ln.strip() for ln in out.splitlines() if "::" in ln]
    rc2, names = run([sys.executable, "-c", "import sys; sys.path[:0] = ['tests', '.']; import run_tests; "
                      "print('\\n'.join(run_tests.CASES))"], cwd=REPO, timeout=120)
    need(rc2 == 0, f"could not read CASES from tests/run_tests.py:{tail(names)}")
    cases = [n.strip() for n in names.splitlines() if n.strip()]
    missing = [c for c in cases if not any(c in i for i in ids)]
    need(not missing, f"{len(missing)}/{len(cases)} landmine cases have no pytest test: {missing[:6]}")
    for want, rx in (("tests/test_paging.py", r"test_paging"), ("tests/test_robots_identity.py", r"robots_identity"),
                     ("signature matcher unit tests", r"(sig|guard|waf)[^/:]*\.py::")):
        need(any(re.search(rx, i, re.I) for i in ids), f"pytest collects nothing for {want}")
    rc, out = run(base, cwd=REPO, timeout=2400)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    need(rc == 0 and re.search(r"\bpassed\b", last) and not re.search(r"\bfailed\b|\berror", last),
         f"pytest rc {rc}: {last}{tail(out, 25)}")
    empty = TMP_ROOT / "no_browsers"
    empty.mkdir(exist_ok=True)
    rc, out_nb = run(base, cwd=REPO, timeout=2400, env=dict(ENV, PLAYWRIGHT_BROWSERS_PATH=str(empty)))
    last_nb = out_nb.strip().splitlines()[-1] if out_nb.strip() else ""
    need(rc == 0 and "skipped" in last_nb and not re.search(r"\bfailed\b|\berror", last_nb),
         f"without Chromium, browser cases must be SKIPPED not failed; pytest rc {rc}: {last_nb}{tail(out_nb, 25)}")
    rc, out_rt = run([sys.executable, REPO / "tests" / "run_tests.py"], cwd=REPO, timeout=2400)
    need(rc == 0 and "all passed" in out_rt.lower(), f"python tests/run_tests.py rc {rc}:{tail(out_rt, 20)}")
    return f"{len(ids)} collected ({len(cases)} cases covered); pytest: {last}; no Chromium: {last_nb}; run_tests ok"


# ---- check 10: ruff + ci.yml
_INT = re.compile(r"[-+]?\d+$")
_FLOAT = re.compile(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$")


def _strip_comment(line: str) -> str:
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"" and (i == 0 or line[i - 1] in " \t[{,:-"):
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _split_flow(s: str) -> list:
    parts, cur, depth, q = [], "", 0, None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "'\"":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _scalar(v: str):
    v = v.strip()
    if not v:
        return None
    if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
        return v[1:-1].replace("''", "'") if v[0] == "'" else v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in _split_flow(v[1:-1])]
    if v.startswith("{") and v.endswith("}"):
        out = {}
        for item in _split_flow(v[1:-1]):
            k, _, val = item.partition(":")
            out[_scalar(k)] = _scalar(val)
        return out
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if _INT.match(v):
        return int(v)
    if _FLOAT.match(v):
        return float(v)
    return v


def _split_kv(s: str):
    q, depth = None, 0
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
            continue
        if ch in "'\"" and (i == 0 or s[i - 1] in " [{,"):
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0 and (i + 1 == len(s) or s[i + 1] == " "):
            return s[:i].strip(), s[i + 1:].strip()
    return None


class MiniYaml:
    """Block-style YAML subset, enough for a GitHub workflow. Raises ValueError when unsure."""

    def __init__(self, text: str):
        self.raw = [ln.rstrip("\r") for ln in text.split("\n")]
        if any(ln[:len(ln) - len(ln.lstrip())].count("\t") for ln in self.raw):
            raise ValueError("tab used for indentation (not allowed in YAML)")
        self.lines = [_strip_comment(ln) for ln in self.raw]
        self.i = 0

    @staticmethod
    def _ind(ln):
        return len(ln) - len(ln.lstrip(" "))

    def _next(self):
        while self.i < len(self.lines):
            s = self.lines[self.i].strip()
            if s and s not in ("---", "..."):
                return self.i
            self.i += 1
        return None

    def parse(self):
        if self._next() is None:
            return None
        doc = self._block(self._ind(self.lines[self.i]))
        if self._next() is not None:
            raise ValueError(f"line {self.i + 1}: could not parse {self.lines[self.i].strip()!r}")
        return doc

    def _block(self, ind):
        s = self.lines[self.i].strip()
        return self._seq(ind) if s == "-" or s.startswith("- ") else self._map(ind)

    def _flow_tail(self, v):
        while v[:1] in "[{" and v.count(v[0]) > v.count("]" if v[0] == "[" else "}") and self.i < len(self.lines):
            v += " " + self.lines[self.i].strip()
            self.i += 1
        return v

    def _seq(self, ind):
        out = []
        while self._next() is not None:
            ln = self.lines[self.i]
            cur, s = self._ind(ln), ln.strip()
            if cur > ind:
                raise ValueError(f"line {self.i + 1}: unexpected indentation")
            if cur < ind or not (s == "-" or s.startswith("- ")):
                break
            rest = s[1:].lstrip()
            if not rest:
                self.i += 1
                if self._next() is not None and self._ind(self.lines[self.i]) > ind:
                    out.append(self._block(self._ind(self.lines[self.i])))
                else:
                    out.append(None)
                continue
            off = cur + len(s) - len(rest)
            if rest[0] not in "[{\"'" and _split_kv(rest):
                self.lines[self.i] = " " * off + rest
                self.raw[self.i] = " " * off + self.raw[self.i].lstrip()[1:].lstrip()
                out.append(self._map(off))
            else:
                self.i += 1
                out.append(_scalar(self._flow_tail(rest)))
        return out

    def _map(self, ind):
        out = {}
        while self._next() is not None:
            ln = self.lines[self.i]
            cur, s = self._ind(ln), ln.strip()
            if cur < ind:
                break
            if cur > ind:
                raise ValueError(f"line {self.i + 1}: unexpected indentation")
            if s == "-" or s.startswith("- "):
                break
            kv = _split_kv(s)
            if kv is None:
                raise ValueError(f"line {self.i + 1}: expected 'key: value', got {s!r}")
            k, v = kv
            k = _scalar(k) if k[:1] in "'\"" else k
            self.i += 1
            if v == "":
                if self._next() is not None:
                    nl = self.lines[self.i]
                    ni, ns = self._ind(nl), nl.strip()
                    if ni > ind:
                        out[k] = self._block(ni)
                        continue
                    if ni == ind and (ns == "-" or ns.startswith("- ")):
                        out[k] = self._seq(ind)
                        continue
                out[k] = None
            elif v[0] in "|>":
                out[k] = self._block_scalar(ind, v[0])
            else:
                out[k] = _scalar(self._flow_tail(v))
        return out

    def _block_scalar(self, ind, style):
        body, bind = [], None
        while self.i < len(self.raw):
            r = self.raw[self.i]
            if not r.strip():
                body.append("")
                self.i += 1
                continue
            ri = self._ind(r)
            if ri <= ind or (bind is not None and ri < bind):
                break
            bind = ri if bind is None else bind
            body.append(r[bind:])
            self.i += 1
        while body and body[-1] == "":
            body.pop()
        return "\n".join(body) + "\n" if style == "|" else " ".join(body)


def load_yaml(text: str):
    try:
        import yaml
    except ImportError:
        return MiniYaml(text).parse(), "built-in subset parser (PyYAML not installed)"
    return yaml.safe_load(text), "PyYAML"


_MATRIX_VAR = re.compile(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}")


def _combos(matrix) -> list:
    if not isinstance(matrix, dict):
        return [{}]
    m = dict(matrix)
    inc, exc = m.pop("include", None) or [], m.pop("exclude", None) or []
    keys = list(m)
    vals = [m[k] if isinstance(m[k], list) else [m[k]] for k in keys]
    base = [dict(zip(keys, v)) for v in product(*vals)] if keys else []
    base = [c for c in base if not any(all(c.get(k) == v for k, v in e.items()) for e in exc)]
    extra = []
    for item in inc:
        hit = False
        for c in base:
            if all(k not in keys or c.get(k) == v for k, v in item.items()):
                c.update(item)
                hit = True
        if not hit:
            extra.append(dict(item))
    return base + extra or [{}]


def _applies(cond, family: str) -> bool:
    """Crude `if:` reader: a condition naming exactly one OS family applies to that family
    (or, with '!=', to the other). Anything else applies to every job."""
    if not cond:
        return True
    c = str(cond).lower()
    fams = {f for f, words in (("linux", ("linux", "ubuntu")), ("windows", ("windows",))) if any(w in c for w in words)}
    if len(fams) != 1:
        return True
    return (family in fams) != ("!=" in c)


def ci_problems(doc, text: str) -> tuple:
    probs, seen = [], set()
    if not isinstance(doc, dict):
        return ["ci.yml is not a mapping"], seen
    on = doc.get("on", doc.get(True))
    trig = {on} if isinstance(on, str) else set(on) if isinstance(on, (list, dict)) else set()
    for t in ("push", "pull_request"):
        if t not in trig:
            probs.append(f"not triggered on {t} (on: {on!r})")
    if re.search(r"\bsecrets\.", text):
        probs.append("uses secrets")
    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict) or not jobs:
        return probs + ["no jobs"], seen
    for jname, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for combo in _combos((job.get("strategy") or {}).get("matrix")):
            def sub(s):
                return _MATRIX_VAR.sub(lambda m: str(combo.get(m.group(1), m.group(0))), str(s))
            runs_on = sub(job.get("runs-on", ""))
            fam = "windows" if "windows" in runs_on.lower() else "linux"
            steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
            pys = [sub((s.get("with") or {}).get("python-version", "")) for s in steps
                   if str(s.get("uses", "")).startswith("actions/setup-python")]
            py = pys[0] if pys else "(no setup-python)"
            seen.add((runs_on, py))
            tag = f"{jname} [{runs_on} / py {py}]"
            runs = [sub(s.get("run", "")) if _applies(s.get("if"), fam) else "" for s in steps]

            def first(pred):
                return next((i for i, r in enumerate(runs) if pred(r)), None)
            i_inst = first(lambda r: any({"browser", "tls", "dev"} <= {x.strip() for x in m.split(",")}
                                         for m in re.findall(r"pip\s+install[^\n]*?\.\[([^\]]+)\]", r)))
            pw = (r"playwright\s+install\s+--with-deps\s+chromium" if fam == "linux"
                  else r"playwright\s+install\s+chromium")
            i_pw = first(lambda r: re.search(pw, r))
            i_ruff = first(lambda r: re.search(r"^\s*(python\s+-m\s+)?ruff\s+check\s+\.(\s|$)", r, re.M))
            i_pt = first(lambda r: re.search(r"^\s*(python\s+-m\s+)?pytest(\s|$)", r, re.M))
            for name, idx in (("pip install .[browser,tls,dev]", i_inst), (f"/{pw}/", i_pw),
                              ("ruff check .", i_ruff), ("pytest", i_pt)):
                if idx is None:
                    probs.append(f"{tag}: no step runs {name}")
            if None not in (i_inst, i_pw, i_ruff, i_pt) and not (i_inst < i_pw < i_pt and i_inst < i_ruff):
                probs.append(f"{tag}: steps out of order (install {i_inst}, playwright {i_pw}, "
                             f"ruff {i_ruff}, pytest {i_pt})")
    want = {("ubuntu-latest", "3.10"), ("ubuntu-latest", "3.13"), ("windows-latest", "3.12")}
    if seen != want:
        hint = " (unquoted 3.10 is read as 3.1 - quote it)" if any(p == "3.1" for _, p in seen) else ""
        probs.append(f"matrix runs {sorted(seen)}, want {sorted(want)}{hint}")
    return probs, seen


def check10():
    """ruff check . passes with the pyproject config; ci.yml parses and has the matrix and steps."""
    pp = REPO / "pyproject.toml"
    data = tomllib.loads(pp.read_text(encoding="utf-8"))
    rf = (data.get("tool") or {}).get("ruff") or {}
    lint = rf.get("lint") or {}
    need(rf.get("line-length") == 110, f"[tool.ruff] line-length is {rf.get('line-length')!r}, want 110")
    sel = set(lint.get("select") or rf.get("select") or []) | set(lint.get("extend-select") or rf.get("extend-select") or [])
    need({"E", "F", "I", "B"} <= sel, f"ruff select {sorted(sel)} lacks some of E, F, I, B")
    priv = re.search(PRIVATE_PATH_RE, pp.read_text(encoding="utf-8"), re.I)
    need(not priv, f"pyproject.toml names a private folder ({priv.group(0) if priv else ''}); "
                   "rely on .gitignore (ruff respects it) instead")
    dev = " ".join((data.get("project", {}).get("optional-dependencies") or {}).get("dev") or []).lower()
    miss = [p for p in ("pytest", "ruff", "jsonschema") if not re.search(rf"\b{p}\b", dev)]
    need(not miss, f"[dev] extra lacks {miss} (has: {dev or 'nothing'})")
    rexe = tool("ruff")
    cmd = [rexe, "check", "."] if rexe else [sys.executable, "-m", "ruff", "check", "."]
    rc, out = run(cmd, cwd=REPO, timeout=300)
    need(rc != -998 and "No module named ruff" not in out, "tool missing: ruff (pip install -e .[dev])")
    need(rc == 0, f"ruff check . failed (rc {rc}):{tail(out, 20)}")
    ci = REPO / ".github" / "workflows" / "ci.yml"
    need(ci.exists(), "no .github/workflows/ci.yml")
    text = ci.read_text(encoding="utf-8")
    try:
        doc, how = load_yaml(text)
    except Exception as e:
        raise Fail(f"ci.yml does not parse: {type(e).__name__}: {e}")
    probs, seen = ci_problems(doc, text)
    need(not probs, f"ci.yml ({how}): " + "; ".join(probs[:6]))
    return f"ruff clean; ci.yml parsed with {how}: {sorted(seen)}, install/playwright/ruff/pytest in order"


def check11():
    """Version 1.3.0 wherever it is shown; commits by Ezio-06 only, no Claude lines."""
    want = f"site-scout {VERSION}"
    rc, out = run([sys.executable, REPO / "run_scout.py", "--version"], cwd=REPO, timeout=60)
    need(rc == 0 and out.strip() == want, f"run_scout.py --version: rc {rc}, {out.strip()!r}, want {want!r}")
    rc, out = run([sys.executable, "-c", "import scout, scout.config as c; print(scout.__version__); "
                   "print(c.HONEST_UA)"], cwd=REPO, timeout=60)
    lines = out.strip().splitlines()
    need(lines and lines[0] == VERSION, f"scout.__version__ is {lines[:1]}, want {VERSION}")
    need(len(lines) > 1 and lines[1].startswith(f"SiteScout/{VERSION}"), f"honest UA {lines[1:2]} lacks {VERSION}")
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    proj = data.get("project", {})
    need("version" not in proj and "version" in proj.get("dynamic", []), "pyproject must keep version dynamic")
    exe = tool("site-scout")
    note = ""
    if exe:
        rc, out = run([exe, "--version"], cwd=TMP_ROOT, timeout=60)
        need(out.strip() == want, f"installed site-scout command says {out.strip()!r} (stale install? "
                                  f"pip install -e .), want {want!r}")
        note = "; venv site-scout command ok"
    stale = []
    if has_git():
        rc, out = run(["git", "grep", "-nIE", r"(site-scout|SiteScout/) ?v?[0-9]+\.[0-9]+\.[0-9]+"], cwd=REPO)
        stale = [ln for ln in out.splitlines() if VERSION not in ln and not ln.startswith("tests/hidden/")]
    need(not stale, f"tracked files show an old version: {stale[:5]}")
    need(has_git(), f"no .git in {REPO}")
    rc, out = run(["git", "log", "--format=%an <%ae>%n%B%n@@END@@"], cwd=REPO, timeout=60)
    need(rc == 0 and out.strip(), f"git log failed or no commits (rc {rc}):{tail(out)}")
    commits = [c.strip() for c in out.split("@@END@@") if c.strip()]
    authors = {c.splitlines()[0].strip() for c in commits}
    need(authors == {AUTHOR}, f"unexpected authors: {sorted(authors - {AUTHOR})}")
    badl = [ln.strip() for ln in out.splitlines() if re.search(r"co-authored-by|claude|generated with", ln, re.I)]
    need(not badl, f"forbidden lines in commit messages: {badl[:5]}")
    return f"--version '{want}', __version__ and UA {VERSION}{note}; {len(commits)} commits by {AUTHOR} only"


# ------------------------------------------------------------------ phase 1 regression checks
def p_check1():
    """Fresh venv, pip install . (no extras), run from another folder."""
    src = clean_copy()
    venv = TMP_ROOT / "venv"
    rc, out = run([sys.executable, "-m", "venv", venv], timeout=300)
    need(rc == 0, f"could not create venv:{tail(out)}")
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    vpy = bindir / ("python.exe" if os.name == "nt" else "python")
    rc, out = run([vpy, "-m", "pip", "install", src], timeout=900)
    need(rc == 0, f"pip install . failed (rc {rc}):{tail(out, 25)}")
    rc, out = run([vpy, "-c", "import importlib.util as u;"
                   "print([m for m in ('playwright','curl_cffi','pytest','ruff','jsonschema') if u.find_spec(m)])"])
    need(rc == 0 and out.strip() == "[]",
         f"core install must NOT pull the extras, found installed: {out.strip()}")
    exe = bindir / ("site-scout.exe" if os.name == "nt" else "site-scout")
    need(exe.exists(), f"no site-scout command at {exe}")
    elsewhere = TMP_ROOT / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    rc, out = run([exe, "--version"], cwd=elsewhere, timeout=60)
    need(rc == 0 and out.strip() == f"site-scout {VERSION}",
         f"site-scout --version gave rc {rc}, output {out.strip()!r}, want 'site-scout {VERSION}'")
    rc, out = run([exe, "--list-checks"], cwd=elsewhere, timeout=60)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    need(rc == 0 and len(lines) >= 5 and "robots" in out.lower(),
         f"--list-checks rc {rc}, {len(lines)} lines:{tail(out)}")
    with FakeSite("appshell") as site:
        outdir = elsewhere / "card_out"
        rc, out = run([exe, site.start_url, "--out", outdir, "--no-pace", "--min-delay", "0"],
                      cwd=elsewhere, timeout=400)
    need(rc == 0, f"run against fake site exited {rc}:{tail(out)}")
    card_p = outdir / "scout_card.md"
    need(card_p.exists(), f"no card written:{tail(out)}")
    card = card_p.read_text(encoding="utf-8")
    low = card.lower()
    nt = [ln for ln in low.splitlines() if "not tested" in ln and ("browser" in ln or "playwright" in ln)]
    need(nt, "card has no line saying browser checks are 'not tested'")
    need(re.search(r"pip install[^\n]*(\[[^\]]*browser|playwright)", low),
         "card has no install hint (e.g. pip install site-scout[browser])")
    need("this check crashed" not in low, "a check crashed in the core install:\n      " +
         "\n      ".join(ln for ln in card.splitlines() if "crashed" in ln.lower())[:800])
    bad = re.search(r"guard_signatures[^\n]*(not found|missing|no such)|FileNotFoundError|Traceback",
                    card + out)
    need(not bad, f"data file / crash problem in output: {bad.group(0) if bad else ''}")
    rep = json.loads((outdir / "report.json").read_text(encoding="utf-8"))
    try:
        n = find_key(rep, "signature_rules_skipped")
    except KeyError:
        n = None
    need(n == 0, f"installed wheel: signature_rules_skipped = {n!r} (signatures not loaded from the package?)")
    return f"venv install ok, --version ok, {len(lines)} checks listed, signatures load, card says not tested + hint"


def p_check3():
    """Default run sends the honest SiteScout UA."""
    with FakeSite("honest") as site:
        r = scout(site, "p3_honest").require()
    card, log, hits = r.card, r.log, r.hits
    need(hits, "fake site saw no requests")
    start = [ua for p, ua in hits if p.split("?")[0] == "/list"]
    robots = [ua for p, ua in hits if p.split("?")[0] == "/robots.txt"]
    need(start, "start page /list never requested")
    need(robots, "robots.txt never requested")
    for label, uas in (("start page", start), ("robots.txt", robots)):
        bad = [ua for ua in uas if not is_exempt(ua) and
               not (ua.startswith(f"SiteScout/{VERSION}") and REPO_URL in ua)]
        need(not bad, f"{label} got non-honest UA(s): {sorted(set(bad))[:3]}")
    first = next((u for u in start if not is_exempt(u)), "")
    need(first.startswith(f"SiteScout/{VERSION}") and REPO_URL in first, f"first start-page UA {first!r}")
    chrome = [(p, ua) for p, ua in hits if is_chrome(ua)]
    need(not chrome, f"site accepts honest bots but Chrome UA was used anyway: {chrome[:3]}")
    missing = [e.get("n") for e in log if "identity" not in e]
    need(not missing, f"requests.log.json entries without 'identity': n={missing[:10]}")
    start_logs = [e for e in log if same_url(str(e.get("url", "")), site.start_url)]
    need(start_logs and ident_kind(start_logs[0]) == "honest",
         f"start-page log entry identity is {start_logs[0].get('identity') if start_logs else None!r}")
    rows = claim_rows(card)
    ok = any(("accepts an honest bot identity" in r_.lower() and ev == "verified") or
             ("blocks honest bots" in r_.lower() and ev == "broken") for r_, ev in rows)
    need(ok, f"card lacks 'Site accepts an honest bot identity' = verified; rows: {rows}")
    return f"UA {first!r}; {len(hits)} requests, none Chrome; claim verified"


def p_check4():
    """robots.txt disallows SiteScout (allows *): stricter answer wins; now exit 5."""
    with FakeSite("robots") as site:
        r = scout(site, "p4_robots").require(rcs=(5,))
    need(any(p.startswith("/robots.txt") for p, _ in r.hits), "robots.txt was never fetched")
    lines = [ln for ln in r.card.lower().splitlines()
             if "robots" in ln and "disallow" in ln and "not disallow" not in ln]
    need(lines, "card does not say robots.txt disallows the page")
    bad_log = [e.get("url") for e in r.log if "/private/" in str(e.get("url", ""))]
    need(not bad_log, f"disallowed URL(s) in requests.log.json: {bad_log[:5]}")
    bad_hits = [p for p, _ in r.hits if p.startswith("/private/")]
    need(not bad_hits, f"fake site received disallowed request(s): {bad_hits[:5]}")
    return f"exit 5; card: {lines[0].strip()[:90]!r}; 0 disallowed requests"


def p_check5():
    """Honest UA gets 403, Chrome gets the page: one labelled comparison, then Chrome; exit 0."""
    with FakeSite("blockhonest") as site:
        r = scout(site, "p5_blockhonest").require()
    card, log, hits = r.card, r.log, r.hits
    comp = [i for i, e in enumerate(log) if "comparison" in str(e.get("purpose", "")).lower()]
    need(len(comp) == 1, f"want exactly 1 log entry labelled 'comparison', got {len(comp)}: "
         f"{[log[i].get('purpose') for i in comp]}  (purposes seen: "
         f"{sorted({str(e.get('purpose')) for e in log})[:15]})")
    ci = comp[0]
    need(ident_kind(log[ci]) == "browser", f"comparison entry identity is {log[ci].get('identity')!r}")
    need(same_url(str(log[ci].get("url", "")), site.start_url),
         f"comparison request was not for the start page: {log[ci].get('url')}")
    before = [e for e in log[:ci] if ident_kind(e) == "honest" and same_url(str(e.get("url", "")), site.start_url)
              and e.get("status") == 403]
    need(before, "no honest-identity 403 on the start page logged before the comparison")
    later = log[ci + 1:]
    need(later, "no requests after the comparison - rest of the run did not happen")
    honest_later = [(e.get("n"), e.get("purpose")) for e in later if ident_kind(e) == "honest"]
    need(not honest_later, f"honest identity used after the comparison: {honest_later[:5]}")
    missing = [e.get("n") for e in log if "identity" not in e]
    need(not missing, f"log entries without 'identity': n={missing[:10]}")
    first_chrome = next((i for i, (_, ua) in enumerate(hits) if is_chrome(ua)), None)
    need(first_chrome is not None, "the fake site never saw a Chrome UA")
    need(any("sitescout" in ua.lower() and p.split("?")[0] == "/list" for p, ua in hits[:first_chrome]),
         "the honest UA never tried the start page before Chrome was used")
    late_honest = [p for p, ua in hits[first_chrome:] if "sitescout" in ua.lower()]
    need(not late_honest, f"SiteScout UA sent after switching to Chrome: {late_honest[:5]}")
    n_chrome = sum(1 for _, ua in hits if is_chrome(ua))
    need(n_chrome >= 2, f"only {n_chrome} Chrome request(s): the rest of the run did not use Chrome")
    low = card.lower()
    need("blocks honest bots" in low, "card does not say 'site blocks honest bots'")
    need(re.search(r"identity\W{0,6}browser", low), "card does not say 'identity: browser'")
    rows = claim_rows(card)
    ok = any(("blocks honest bots" in r_.lower() and ev == "verified") or
             ("accepts an honest bot identity" in r_.lower() and ev == "broken") for r_, ev in rows)
    need(ok, f"claims table lacks honest-identity claim with the blocked result; rows: {rows}")
    return f"1 comparison (n={log[ci].get('n')}), {len(later)} later requests none honest, {n_chrome} Chrome hits"


def p_check6():
    """--identity browser: SiteScout UA never sent."""
    with FakeSite("blockhonest") as site:
        r = scout(site, "p6_browser", ["--identity", "browser"]).require()
    need(r.hits, "fake site saw no requests")
    honest = [p for p, ua in r.hits if "sitescout" in ua.lower()]
    need(not honest, f"SiteScout UA sent with --identity browser: {honest[:5]}")
    honest_log = [e.get("n") for e in r.log if ident_kind(e) == "honest"]
    need(not honest_log, f"log says honest identity for entries {honest_log[:5]}")
    got = [e for e in r.log if same_url(str(e.get("url", "")), site.start_url) and e.get("status") == 200]
    need(got, "start page never answered 200 - run did not get in with the browser identity")
    return f"{len(r.hits)} requests, 0 SiteScout UAs, start page 200"


def p_check7():
    """git: allowlisted folders untracked, no private words."""
    need(has_git(), f"no .git in {REPO} - repo not initialised yet")
    rc, out = run(["git", "ls-files"], cwd=REPO, timeout=60)
    need(rc == 0, f"git ls-files failed:{tail(out)}")
    files = [f.strip() for f in out.splitlines() if f.strip()]
    need(files, "git ls-files lists nothing - nothing tracked")
    bad = [f for f in files if any(f.startswith(x) for x in EXCLUDED)]
    need(not bad, f"{len(bad)} tracked file(s) under excluded folders: {bad[:8]}")
    named_ = [f for f in files if re.search(PRIVATE_RE, f, re.I)]
    need(not named_, f"tracked file names contain private words: {named_[:5]}")
    rc, out = run(["git", "grep", "-niE", PRIVATE_RE], cwd=REPO, timeout=60)
    need(rc == 1, f"git grep found private words (rc {rc}):{tail(out, 12)}")
    # untracked new files (ci.yml, schema, new tests) will be committed too - scan them as well
    rc, out = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, timeout=60)
    leaks = []
    for f in [x.strip() for x in out.splitlines() if x.strip()]:
        p = REPO / f
        if p.is_file() and p.stat().st_size < 2_000_000:
            if re.search(PRIVATE_RE, f + "\n" + p.read_text(encoding="utf-8", errors="ignore"), re.I):
                leaks.append(f)
    need(not leaks, f"new (untracked, not ignored) files contain private words: {leaks[:5]}")
    not_ignored = []
    for x in EXCLUDED:
        if (REPO / x).exists():
            r, _ = run(["git", "check-ignore", "-q", x], cwd=REPO, timeout=30)
            if r != 0:
                not_ignored.append(x)
    need(not not_ignored, f".gitignore does not ignore: {not_ignored}")
    gone = [x for x in (_FW, "holdout", "scouts", "old_versions", "research", "specs", "tests/out", ".venv")
            if not (REPO / x).exists()]
    need(not gone, f"private folders must stay on disk but are missing: {gone}")
    return f"{len(files)} tracked files, none excluded, no private words (tracked or new), folders kept"


def p_check8():
    """Wheel ships data files; MIT license; extras declared (incl. dev); wafw00f credited."""
    src = clean_copy()
    wdir = TMP_ROOT / "wheel"
    rc, out = run([sys.executable, "-m", "pip", "wheel", src, "--no-deps", "-w", wdir], timeout=900)
    need(rc == 0, f"pip wheel failed:{tail(out, 25)}")
    wheels = list(wdir.glob("*.whl"))
    need(len(wheels) == 1, f"want 1 wheel, got {[w.name for w in wheels]}")
    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        for req in ("scout/data/guard_signatures.json", "scout/data/WAFW00F_LICENSE"):
            need(req in names, f"wheel is missing {req}")
        sig = json.loads(z.read("scout/data/guard_signatures.json"))
        need(sig, "guard_signatures.json in wheel is empty")
        tops = {n.split("/")[0] for n in names}
        extra_tops = [t for t in tops if t != "scout" and not t.endswith(".dist-info")]
        need(not extra_tops, f"wheel ships unexpected top-level items: {sorted(extra_tops)}")
        priv = [n for n in names if re.search(PRIVATE_RE, n, re.I)]
        need(not priv, f"wheel file names contain private words: {priv[:5]}")
        meta = z.read(next(n for n in names if n.endswith(".dist-info/METADATA"))).decode("utf-8", "replace")
        ep_name = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        eps = z.read(ep_name).decode() if ep_name else ""
        lic_files = [n for n in names if ".dist-info/" in n and "licen" in n.lower().rsplit("/", 1)[-1]]
        lic_text = " ".join(z.read(n).decode("utf-8", "replace") for n in lic_files)
    need(re.search(rf"^Version: {re.escape(VERSION)}\s*$", meta, re.M), f"wheel METADATA Version is not {VERSION}")
    need(re.search(r"^site-scout\s*=", eps, re.M), "no 'site-scout' console script in wheel")
    for extra, pkgs in (("browser", ["playwright"]), ("tls", ["curl_cffi"]), ("dev", ["pytest", "ruff", "jsonschema"])):
        need(re.search(rf"^Provides-Extra: {extra}\s*$", meta, re.M), f"extra [{extra}] not declared")
        for pkg in pkgs:
            reqs = [ln for ln in meta.splitlines() if ln.lower().startswith("requires-dist:")
                    and re.search(rf":\s*{pkg.replace('_', '[-_]')}\b", ln, re.I)]
            need(reqs, f"{pkg} not listed for extra [{extra}]")
            need(all(f'extra == "{extra}"' in ln or f"extra == '{extra}'" in ln for ln in reqs),
                 f"{pkg} is a core dependency, must be only in [{extra}]: {reqs}")
    core = sorted(re.split(r"[\s<>=!~;\[(]", ln.split(":", 1)[1].strip())[0].lower()
                  for ln in meta.splitlines() if ln.lower().startswith("requires-dist:") and "extra ==" not in ln)
    want_core = ["beautifulsoup4", "chompjs", "lxml", "protego", "requests"]
    note = "" if core == want_core else f" (NOTE soft rule 'no new runtime deps': core deps now {core})"
    lic = REPO / "LICENSE"
    need(lic.exists(), "no LICENSE file")
    lt = lic.read_text(encoding="utf-8", errors="replace")
    need("MIT" in lt and "Permission is hereby granted, free of charge" in lt, "LICENSE is not the MIT text")
    need("Copyright (c) 2026 Ezio-06" in lt, "LICENSE lacks 'Copyright (c) 2026 Ezio-06'")
    need(re.search(r"^License(-Expression)?: MIT\s*$|License :: OSI Approved :: MIT", meta, re.M)
         or "MIT" in lic_text, "wheel metadata does not declare MIT")
    credit = ""
    for n in ("README.md", "NOTICE", "NOTICE.md", "NOTICE.txt"):
        p = REPO / n
        if p.exists():
            credit += p.read_text(encoding="utf-8", errors="replace")
    need("wafw00f" in credit.lower() and "bsd" in credit.lower(), "README/NOTICE does not credit wafw00f (BSD-3)")
    return f"{wheels[0].name}: data files, MIT, extras browser/tls/dev, wafw00f credited{note}"


CHECKS = {"1": check1, "2": check2, "3": check3, "4": check4, "5": check5, "6": check6, "7": check7,
          "8": check8, "9": check9, "10": check10, "11": check11,
          "p1": p_check1, "p3": p_check3, "p4": p_check4, "p5": p_check5, "p6": p_check6,
          "p7": p_check7, "p8": p_check8}


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pick = [a.lower() for a in argv] or list(CHECKS)
    unknown = [n for n in pick if n not in CHECKS]
    if unknown:
        print(f"unknown check(s): {unknown}; valid: {' '.join(CHECKS)}")
        return 2
    results = {}
    for n in pick:
        t0 = time.time()
        try:
            info = CHECKS[n]()
            results[n] = True
            print(f"PASS  check {n:>3} ({time.time() - t0:.0f}s): {info}", flush=True)
        except Fail as e:
            results[n] = False
            print(f"FAIL  check {n:>3} ({time.time() - t0:.0f}s): {e}", flush=True)
        except Exception as e:
            results[n] = False
            print(f"FAIL  check {n:>3} ({time.time() - t0:.0f}s): check crashed {type(e).__name__}: {e}", flush=True)
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    failed = [n for n, ok in results.items() if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
