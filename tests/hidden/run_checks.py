r"""Hidden acceptance checks.

  d1-d10  spec phase3-docs-release (checks 1-10; 11 is manual)
  1-11    spec phase2-quality (12 is manual)
  p1, p3-p8  phase-1 regression checks (p2/p10 folded into 11, p9 into 9)

    python tests/hidden/run_checks.py            everything (d-checks first, d8 after p1, d10 last)
    python tests/hidden/run_checks.py d1 d7      only phase-3 checks 1 and 7
    python tests/hidden/run_checks.py 3 5 p4     only phase-2 checks 3, 5 and phase-1 check 4

PHASE 3 (docs/release) - what the d-checks read; nothing touches the network
  README sections : '## ' headings, matched in order: why / example|sample / quick start|install /
                    usage / what it checks / tested / responsible / comparison|vs / architecture /
                    limitation (+ a roadmap|planned heading before credits) / credit|licence.
                    A section runs to the next matched one, so '## Adding a check' may follow
                    Architecture. Headings inside code fences are ignored.
  flags table     : a pipe table in Usage whose first header cell says Flag/Option; every flag in
                    any of its cells counts. Exit-code table: first header cell says Code/Exit.
  check table     : the 'What it checks' table column whose header says Check/Name.
  example excerpt : fenced block, '>' quote or pipe-table lines inside the example section; each
                    must be a line of examples/books.toscrape.com/scout_card.md ('...' allowed).
  comparison      : a pipe table naming caniscrape, scraperecon, ScrapeProbe, wafw00f (as columns
                    or rows). Cells starting yes/no/partial/unknown (or a description) are compared
                    with a truth table built from research reports 01 and 06 (local, untracked).
                    A row with no matching research fact must say no/not found for competitors.
  holdout         : numbers are read at run time from holdout/runs/20261022-004000/score.md, and
                    real (non-practice) site names from holdout/ - never written to tracked files.
  example gaps    : requests.log.json has no timestamps, so the 2 s gap per request cannot be
                    proven from committed files; d4 checks config min_delay >= 2 and that the
                    run took at least (requests - 1) x min_delay.
  CI on origin    : not checked here (the lead checks the CI run on origin/main).

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
from urllib.parse import unquote, urlsplit
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
# ID-based noreply (project git rule; the plain noreply form does not link to the profile)
AUTHOR = "Ezio-06 <74005455+Ezio-06@users.noreply.github.com>"
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


def _path_q(url) -> str:
    # each FakeSite gets a fresh ephemeral port, so compare path+query only, never scheme/host/port
    sp = urlsplit(str(url))
    return sp.path + (f"?{sp.query}" if sp.query else "")


def _req_list(r: Run):
    return [(_path_q(e.get("url")), str(e.get("purpose")), str(e.get("identity")), e.get("status")) for e in r.log]


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
    """Version 1.3.0 wherever it is shown; commits by Ezio-06 only, no AI attribution lines."""
    want = f"site-scout {VERSION}"
    rc, out = run([sys.executable, REPO / "run_scout.py", "--version"], cwd=REPO, timeout=60)
    need(rc == 0 and out.strip() == want, f"run_scout.py --version: rc {rc}, {out.strip()!r}, want {want!r}")
    rc, out = run([sys.executable, "-c", "import scout, scout.config as c; print(scout.__version__); "
                   "print(c.HONEST_UA)"], cwd=REPO, timeout=60)
    lines = out.strip().splitlines()
    need(lines and lines[0] == VERSION, f"scout.__version__ is {lines[:1]}, want {VERSION}")
    need(len(lines) > 1 and lines[1].startswith(f"SiteScout/{VERSION}"), f"honest UA {lines[1:2]} lacks {VERSION}")
    # how the version is sourced (static or dynamic) is not in the spec; only what is shown is checked
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
    badl = [ln.strip() for ln in out.splitlines() if re.search(r"co-authored-by|generated with|" + "cla" + "ude", ln, re.I)]
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


# ================================================================== phase 3: docs + release (d1-d10)
SPEC_URL = "https://books.toscrape.com/catalogue/category/books_1/index.html"
EXAMPLE = REPO / "examples" / "books.toscrape.com"
README = REPO / "README.md"
PRACTICE = ("toscrape", "scrapethissite", "webscraper", "httpbin")
COMPETITORS = ("caniscrape", "scraperecon", "scrapeprobe", "wafw00f")
RESEARCH = REPO / "research" / "site-scout-landscape"
HOLDOUT_RUN = REPO / "holdout" / "runs" / "20261022-004000"
# names of AI vendors/tools, built from pieces so this file never spells them
AI_TOOL_RE = "|".join(["cla" + "ude", "anth" + "ropic", "chat" + "gpt", "open" + "ai", "co" + "pilot",
                       "gem" + "ini"])
EARLIER = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "p1", "p3", "p4", "p5", "p6", "p7", "p8"]
RESULTS: dict = {}


def _read(p: Path) -> str:
    need(p.exists(), f"missing {p.relative_to(REPO).as_posix()}")
    return p.read_text(encoding="utf-8", errors="replace")


def _fence_mask(lines) -> list:
    """True for lines inside (or opening/closing) a fenced code block."""
    mask, fence = [], None
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^(`{3,}|~{3,})", s)
        if m and fence is None:
            fence = m.group(1)
            mask.append(True)
        elif m and s.startswith(fence) and not s.strip(fence[0]):
            fence = None
            mask.append(True)
        else:
            mask.append(fence is not None)
    return mask


def md_headings(text: str) -> list:
    """[(line index, level, title)] outside fenced code blocks."""
    lines = text.splitlines()
    mask = _fence_mask(lines)
    out = []
    for i, ln in enumerate(lines):
        h = None if mask[i] else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
        if h:
            out.append((i, len(h.group(1)), h.group(2).strip()))
    return out


def md_tables(lines, mask=None) -> list:
    """[(first line index, header cells, [row cells])] for pipe tables outside code fences."""
    mask = mask or [False] * len(lines)
    tables, i = [], 0

    def cells(ln):
        s = ln.strip()
        s = s[1:] if s.startswith("|") else s
        s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
        return [c.strip() for c in re.split(r"(?<!\\)\|", s)]
    while i < len(lines) - 1:
        a, b = lines[i], lines[i + 1]
        if (not mask[i] and "|" in a and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", b)):
            head, rows, j = cells(a), [], i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip() and not mask[j]:
                rows.append(cells(lines[j]))
                j += 1
            tables.append((i, head, rows))
            i = j
        else:
            i += 1
    return tables


README_SECTIONS = [  # (number, label, heading regex) - section 1 is the title block
    (2, "Why it exists", r"\bwhy\b"),
    (3, "example card", r"example|sample"),
    (4, "Quick start", r"quick\s*start|install|getting started|set ?up"),
    (5, "Usage", r"^usage|^use\b|how to use|^using\b"),
    (6, "What it checks", r"what it checks|^checks\b"),
    (7, "How it's tested", r"tested|testing|accuracy"),
    (8, "Responsible use", r"responsible|ethic|polite"),
    (9, "Comparison", r"compar|alternative|\bvs\b|other tools"),
    (10, "Architecture", r"architecture"),
    (11, "Limitations", r"limitation"),
    (12, "Credits and licence", r"credit|licen[cs]e"),
]


def readme_layout() -> dict:
    """Finds the '## ' heading of each spec section, in order. A section runs to the start of the
    next found section, so extra '## ' headings (e.g. 'Adding a check') belong to the one before."""
    if "readme" in _CACHE:
        return _CACHE["readme"]
    text = _read(README)
    lines = text.splitlines()
    mask = _fence_mask(lines)
    hs = md_headings(text)
    h2 = [(i, t) for i, lvl, t in hs if lvl == 2]
    found, probs, prev = {}, [], -1
    for num, label, rx in README_SECTIONS:
        cands = [k for k, (_, t) in enumerate(h2) if re.search(rx, t, re.I)]
        after = [k for k in cands if k > prev]
        if after:
            prev = after[0]
            found[num] = h2[prev][0]
        elif cands:
            probs.append(f"section {num} ({label}) out of order: '## {h2[cands[0]][1]}' comes too early")
        else:
            probs.append(f"section {num} ({label}) missing: no '## ' heading matches /{rx}/")
    starts = sorted(found.values())
    spans = {}
    for num, st in found.items():
        nxt = [s for s in starts if s > st]
        spans[num] = (st, nxt[0] if nxt else len(lines))
    lay = dict(text=text, lines=lines, mask=mask, hs=hs, h2=h2, spans=spans, probs=probs)
    _CACHE["readme"] = lay
    return lay


def section(num: int) -> str:
    lay = readme_layout()
    if num not in lay["spans"]:
        raise Fail(f"README section {num} not found ({'; '.join(lay['probs'][:3])})")
    a, b = lay["spans"][num]
    return "\n".join(lay["lines"][a:b])


def section_lines(num: int) -> tuple:
    lay = readme_layout()
    if num not in lay["spans"]:
        raise Fail(f"README section {num} not found ({'; '.join(lay['probs'][:3])})")
    a, b = lay["spans"][num]
    return lay["lines"][a:b], lay["mask"][a:b]


def gh_slug(title: str) -> str:
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    t = re.sub(r"[`*_~]", "", t).strip().lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")


def md_links(lines, mask) -> list:
    """Link/image targets outside code fences (inline, reference-style, <img src>, <a href>)."""
    out = []
    for ln, inside in zip(lines, mask):
        if inside:
            continue
        ln = re.sub(r"`[^`]*`", "", ln)
        out += re.findall(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", ln)
        out += re.findall(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", ln)
        out += re.findall(r"<(?:img|a)\b[^>]*?\b(?:src|href)\s*=\s*[\"']([^\"']+)", ln, re.I)
    return out


def _ignored(rel: str) -> bool:
    if not has_git():
        return False
    rc, _ = run(["git", "check-ignore", "-q", rel], cwd=REPO, timeout=30)
    return rc == 0


def d_check1():
    """README: sections 1-12 in order, <= 260 lines, required content in each, relative links resolve."""
    lay = readme_layout()
    lines, probs = lay["lines"], list(lay["probs"])
    if len(lines) > 260:
        probs.append(f"README has {len(lines)} lines, max 260")
    # 1. name, CI badge, licence badge, one-line pitch
    first = next((ln for ln in lines if ln.strip()), "")
    if not re.match(r"^#\s+Site Scout\b", first):
        probs.append(f"first line is {first[:60]!r}, want '# Site Scout'")
    top_end = lay["h2"][0][0] if lay["h2"] else len(lines)
    top = "\n".join(lines[:top_end])
    imgs = re.findall(r"!\[([^\]]*)\]\(\s*([^)\s]+)", top) + \
        [("", u) for u in re.findall(r"<img\b[^>]*?src\s*=\s*[\"']([^\"']+)", top, re.I)]
    if not any("Ezio-06/site-scout/actions/workflows/ci.yml" in u for _, u in imgs):
        probs.append("no CI badge for Ezio-06/site-scout/actions/workflows/ci.yml above the first '## '")
    if not any(re.search(r"licen[cs]e", a + " " + u, re.I) for a, u in imgs):
        probs.append("no licence badge above the first '## '")
    pitch = [ln for ln in lines[1:top_end] if ln.strip() and not ln.lstrip().startswith(("#", "[!", "!", "<", "---"))
             and len(ln.split()) >= 5]
    if not pitch:
        probs.append("no one-line pitch (a sentence of 5+ words) between the title and the first '## '")
    # per-section content (only for sections that were found)
    want = {
        2: [(r"protect", "'protected' (what other tools answer)"), (r"\ball\b", "'ALL the data'"),
            (r"pag|reach", "paging reach"), (r"feed", "hidden feeds"), (r"evidence", "evidence level")],
        3: [(r"\]\(\s*\.?/?examples/books\.toscrape\.com/scout_card\.md\s*\)", "link to the full example card")],
        4: [(r"pipx install git\+https://github\.com/Ezio-06/site-scout", "pipx install git+https://github.com/Ezio-06/site-scout"),
            (r"\bpip install\b", "pip install"), (r"\[browser", "[browser] extra"), (r"\[tls|,\s*tls\]", "[tls] extra"),
            (r"playwright install chromium", "playwright install chromium"), (r"setup\.bat", "setup.bat"),
            (r"scout\.bat", "scout.bat")],
        5: [(r"--output -", "--output -"), (r"\]\(\s*\.?/?docs/report\.schema\.json\s*\)", "link to docs/report.schema.json")],
        7: [(r"\]\(\s*\.?/?docs/holdout\.md\s*\)", "link to docs/holdout.md"), (r"95\s?%", "headline 95%"),
            (r"landmine", "landmine fake sites"), (r"hidden", "hidden acceptance checks"),
            (r"before", "checks written BEFORE the code"),
            (r"1\.1\b|(earlier|older|previous) version", "that the holdout ran on an earlier version (v1.1)")],
        8: [(r"SiteScout/", "honest SiteScout/<ver> identity"), (r"robots\.txt", "robots.txt"),
            (r"captcha", "no CAPTCHA solving"), (r"payload|attack", "no attack payloads"), (r"passive", "passive guard matching"),
            (r"label", "one labelled browser-identity comparison"), (r"\b60\b", "request cap (60)"),
            (r"\b2(\.0)?\s?(s\b|sec|seconds)", "request gap (2 s)")],
        10: [(r"```mermaid", "a mermaid diagram"), (r"fetcher\.py", "the layout list"),
             (r"^#{2,4}\s+Adding a check", "'Adding a check'")],
        11: [(r"quota", "quotas limitation"), (r"network", "results hold for the network it ran from"),
             (r"feed", "feed-paging reach (roadmap)"), (r"batch", "batch mode (roadmap)"),
             (r"starter", "starter-scraper output (roadmap)"), (r"fresh[^\n]{0,60}holdout|holdout[^\n]{0,60}fresh",
                                                                "fresh holdout (roadmap)")],
        12: [(r"wafw00f", "wafw00f credit"), (r"BSD", "BSD-3"), (r"\bMIT\b", "MIT licence"),
             (r"^Built with help from AI coding tools\.\s*$", "the exact line 'Built with help from AI coding tools.'")],
    }
    for num, items in want.items():
        if num not in lay["spans"]:
            continue
        body = section(num)
        for rx, what in items:
            if not re.search(rx, body, re.I | re.M):
                probs.append(f"section {num} lacks {what}")
    if 2 in lay["spans"]:
        words = len(" ".join(section(2).splitlines()[1:]).split())
        if not 15 <= words <= 130:
            probs.append(f"'Why it exists' is {words} words; spec says 3-5 lines (15-130 words accepted)")
    if 10 in lay["spans"]:
        mm = re.search(r"```mermaid\s*\n(.*?)```", section(10), re.S | re.I)
        if mm:
            body = mm.group(1).lower()
            miss = [w for w in ("cli", "runner", "check", "fetcher", "report") if w not in body]
            if miss or body.count("-->") + body.count("---") + body.count("==>") < 4:
                probs.append(f"mermaid diagram lacks {miss or 'enough arrows'} (CLI -> runner -> checks -> fetcher -> report)")
    if 11 in lay["spans"]:
        a = lay["spans"][11][0]
        b = lay["spans"].get(12, (len(lines),))[0]
        if not any(a <= i < b and re.search(r"roadmap|planned", t, re.I) for i, _, t in lay["hs"]):
            probs.append("no roadmap heading in or after 'Limitations' (before credits)")
    bad_ai = re.findall(AI_TOOL_RE, lay["text"], re.I)
    if bad_ai:
        probs.append(f"README names an AI tool/vendor ({len(bad_ai)}x); use only 'Built with help from AI coding tools.'")
    # links
    slugs = Counter()
    anchors = set()
    for _, _, t in lay["hs"]:
        s = gh_slug(t)
        anchors.add(s if not slugs[s] else f"{s}-{slugs[s]}")
        slugs[s] += 1
    bad_links, n_rel = [], 0
    for tgt in md_links(lines, lay["mask"]):
        if re.match(r"^[a-z][a-z0-9+.-]*:", tgt, re.I):
            continue
        if tgt.startswith("#"):
            if tgt[1:].lower() not in anchors:
                bad_links.append(f"{tgt} (no such heading)")
            continue
        n_rel += 1
        path = tgt.split("#")[0].split("?")[0]
        rel = unquote(path)
        rel = rel[2:] if rel.startswith("./") else rel
        p = (REPO / rel).resolve()
        if REPO.resolve() not in p.parents and p != REPO.resolve():
            bad_links.append(f"{tgt} (outside the repo)")
        elif not p.exists():
            bad_links.append(f"{tgt} (missing)")
        elif _ignored(rel.rstrip("/")):
            bad_links.append(f"{tgt} (git-ignored: broken in a public clone)")
    if bad_links:
        probs.append(f"broken relative links: {bad_links[:6]}")
    need(not probs, f"{len(probs)} problem(s): " + " | ".join(probs[:12]))
    return f"{len(lines)} lines, 12 sections in order, content present, {n_rel} relative links ok"


def help_text() -> str:
    if "help" not in _CACHE:
        rc, out = run([sys.executable, REPO / "run_scout.py", "--help"], cwd=REPO, timeout=60)
        need(rc == 0, f"run_scout.py --help exited {rc}:{tail(out)}")
        _CACHE["help"] = out
    return _CACHE["help"]


def help_flags() -> tuple:
    """-> ([set of option strings per flag], {flag: choices}, set of exit codes) from --help."""
    groups, choices, codes, state = [], {}, set(), None
    for ln in help_text().splitlines():
        low = ln.strip().lower()
        if re.match(r"^(options|optional arguments):", low):
            state = "opt"
            continue
        if re.match(r"^exit codes?:", low):
            state = "exit"
            continue
        if ln and not ln[0].isspace():
            state = None
            continue
        if state == "opt":
            m = re.match(r"^  (-\S.*?)(?:\s{2,}|$)", ln)
            if m:
                head = m.group(1)
                opts = re.findall(r"(?:^|,\s*)(--?[A-Za-z][\w-]*)", head)
                if opts:
                    groups.append(set(opts))
                    ch = re.search(r"\{([^}]+)\}", head)
                    if ch:
                        for o in opts:
                            choices[o] = set(ch.group(1).split(","))
        elif state == "exit":
            m = re.match(r"^\s+(\d+)\s", ln)
            if m:
                codes.add(int(m.group(1)))
    return groups, choices, codes


FLAG_RE = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")


def d_check2():
    """README flags table = flags in --help (both ways); exit-code table = codes in --help."""
    groups, choices, codes = help_flags()
    need(groups and len(groups) >= 10, f"could not read flags from --help (got {groups})")
    need(codes, "could not read exit codes from --help")
    lines, mask = section_lines(5)
    tables = md_tables(lines, mask)
    flag_tabs = [t for t in tables if t[1] and re.search(r"flag|option", t[1][0], re.I)]
    need(flag_tabs, "Usage section has no flags table (first header cell 'Flag' or 'Option')")
    rows = [r for t in flag_tabs for r in t[2]]
    in_table = set()
    for r in rows:
        in_table |= set(FLAG_RE.findall(" ".join(r)))
    all_help = set().union(*groups)
    missing = [sorted(g, key=len)[-1] for g in groups if not g & in_table]
    extra = sorted(in_table - all_help)
    probs = []
    if missing:
        probs.append(f"flags in --help but not in the README table: {missing}")
    if extra:
        probs.append(f"flags in the README table that --help lacks: {extra}")
    for flag, ch in choices.items():
        for r in rows:
            txt = " ".join(r)
            for val in re.findall(rf"{re.escape(flag)}[ =]([\w-]+)", txt):
                if val not in ch:
                    probs.append(f"table shows {flag} {val}, --help allows {sorted(ch)}")
    # defaults stated in the table must be the real defaults (from the committed default run's config)
    try:
        cfg = json.loads(_read(EXAMPLE / "report.json")).get("config") or {}
    except (json.JSONDecodeError, Fail):
        cfg = {}
    for flag, key in (("--min-delay", "min_delay"), ("--max-requests", "max_requests"),
                      ("--pace-interval", "pace_interval"), ("--pace-requests", "pace_requests")):
        if key not in cfg:
            continue
        for r in rows:
            txt = " ".join(r)
            if flag in txt:
                for v in re.findall(r"default[:\s]+`?(\d+(?:\.\d+)?)", txt, re.I):
                    if float(v) != float(cfg[key]):
                        probs.append(f"table says {flag} default {v}, real default {cfg[key]}")
    code_tabs = [t for t in md_tables(*readme_layout_lines()) if t[1] and re.search(r"code|exit", t[1][0], re.I)]
    need(code_tabs, "README has no exit-code table (first header cell 'Code' or 'Exit code')")
    readme_codes = set()
    for t in code_tabs:
        for r in t[2]:
            m = re.match(r"^\**`?(\d+)`?\**$", r[0].strip())
            if m:
                readme_codes.add(int(m.group(1)))
    if readme_codes != codes:
        probs.append(f"README exit codes {sorted(readme_codes)} != --help {sorted(codes)}")
    need(not probs, " | ".join(probs[:8]))
    return f"{len(groups)} flags match both ways; exit codes {sorted(codes)} match"


def readme_layout_lines():
    lay = readme_layout()
    return lay["lines"], lay["mask"]


def list_checks() -> list:
    if "checks" not in _CACHE:
        rc, out = run([sys.executable, REPO / "run_scout.py", "--list-checks"], cwd=REPO, timeout=60)
        need(rc == 0, f"--list-checks exited {rc}:{tail(out)}")
        _CACHE["checks"] = [ln.split()[0] for ln in out.splitlines() if ln.strip()]
    return _CACHE["checks"]


def landmine_count() -> int:
    if "landmines" not in _CACHE:
        pt = tool("pytest")
        cmd = [pt] if pt else [sys.executable, "-m", "pytest"]
        rc, out = run([*cmd, "-p", "no:cacheprovider", "--collect-only", "-q"], cwd=REPO, timeout=300)
        need(rc == 0, f"pytest --collect-only failed (rc {rc}):{tail(out)}")
        ids = [ln.strip() for ln in out.splitlines() if "::" in ln]
        _CACHE["landmines"] = sum(1 for i in ids if re.search(r"::test_landmines?\[", i))
    return _CACHE["landmines"]


def _doc_texts(names=("README.md", "docs/design.md", "docs/holdout.md")) -> dict:
    return {n: (REPO / n).read_text(encoding="utf-8", errors="replace") for n in names if (REPO / n).exists()}


def _num_mentions(text: str, rxs) -> list:
    out = []
    for rx in rxs:
        for m in re.finditer(rx, text, re.I):
            out.append((m.group(1).replace(",", ""), text[max(0, m.start() - 30):m.end() + 10].replace("\n", " ")))
    return out


def d_check3():
    """Numbers in README/docs match the code: checks, landmines, signatures (171), CI matrix; design.md exists."""
    probs = []
    docs = _doc_texts()
    need("README.md" in docs, "no README.md")
    for n in ("docs/design.md", "docs/holdout.md"):
        if n not in docs:
            probs.append(f"{n} missing")
    names = list_checks()
    lm = landmine_count()
    need(lm > 0, "pytest collects no landmine cases")
    nsig = len(shipped_signatures().get("guards") or {})
    # what-it-checks table (its 'Check' column) = --list-checks
    lines, mask = section_lines(6)
    tab_names = set()
    for _, head, rows in md_tables(lines, mask):
        col = next((i for i, h in enumerate(head) if re.search(r"check|name", h, re.I)), None)
        if col is None:
            continue
        tab_names |= {re.sub(r"[`*\s]", "", r[col]) for r in rows if col < len(r) and r[col].strip()}
    if set(names) - tab_names:
        probs.append(f"'What it checks' table lacks {sorted(set(names) - tab_names)} (--list-checks has {len(names)})")
    if tab_names - set(names):
        probs.append(f"'What it checks' table names checks --list-checks lacks: {sorted(tab_names - set(names))}")
    seen = {"landmine": [], "signature": [], "check": []}
    for name, txt in docs.items():
        for v, ctx in _num_mentions(txt, [r"\b(\d+)\s+(?:[\w-]+\s+){0,2}landmines?\b", r"\b(\d+)-(?:site|case)\s+landmine",
                                          r"landmines?[\w\s-]{0,25}?\(\s*(\d+)\s*\)"]):
            seen["landmine"].append((name, v, ctx))
            if int(v) != lm:
                probs.append(f"{name}: landmine count {v} != {lm} pytest landmine cases ({ctx!r})")
        for v, ctx in _num_mentions(txt, [r"\b(\d[\d,]*)\s+(?:[\w-]+\s+){0,2}signatures?\b"]):
            seen["signature"].append((name, v, ctx))
            if int(v) != nsig:
                probs.append(f"{name}: signature count {v} != {nsig} guards in guard_signatures.json ({ctx!r})")
        if name == "docs/holdout.md":
            continue
        for m in re.finditer(r"\b(\d+)\s+(?:built-in\s+|independent\s+|separate\s+)?checks\b", txt, re.I):
            before = txt[max(0, m.start() - 40):m.start()].lower()
            if re.search(r"hidden|acceptance|landmine|unit|pytest|holdout|scored|per-check|ci\b", before):
                continue
            seen["check"].append((name, m.group(1)))
            if int(m.group(1)) != len(names):
                probs.append(f"{name}: '{m.group(0)}' but --list-checks has {len(names)}")
    if not any(n == "README.md" for n, *_ in seen["landmine"]):
        probs.append(f"README never states the landmine count ({lm})")
    if not any(n == "README.md" for n, *_ in seen["signature"]):
        probs.append(f"README never states the signature count ({nsig})")
    # CI matrix in 'How it's tested'
    ci = REPO / ".github" / "workflows" / "ci.yml"
    doc, _how = load_yaml(_read(ci))
    combos = []
    for job in (doc.get("jobs") or {}).values():
        if isinstance(job, dict):
            combos += _combos((job.get("strategy") or {}).get("matrix"))
    pys = {str(c.get("python") or c.get("python-version") or "") for c in combos} - {""}
    oss = {str(c.get("os") or "").lower() for c in combos}
    sec7 = section(7)
    stated = set(re.findall(r"(?<![\d.])(3\.\d{1,2})(?!\d|\.\d)", sec7))
    if sec7 and stated != pys:
        probs.append(f"'How it's tested' names Python {sorted(stated)}, ci.yml matrix runs {sorted(pys)}")
    for fam, words in (("ubuntu", r"linux|ubuntu"), ("windows", r"windows"), ("macos", r"mac\s?os|macos|\bmac\b")):
        in_ci = any(fam in o for o in oss)
        in_doc = bool(re.search(words, sec7, re.I))
        if in_ci != in_doc:
            probs.append(f"'How it's tested' {'omits' if in_ci else 'claims'} {fam} (ci.yml: {sorted(oss)})")
    # design.md: topics from the spec, ~1.5 pages
    if "docs/design.md" in docs:
        d = docs["docs/design.md"]
        words = len(d.split())
        if words > 900:
            probs.append(f"docs/design.md is {words} words (spec: max ~1.5 pages, 900 words accepted)")
        for rx, what in ((r"verified", "evidence levels"), (r"broken", "evidence levels"), (r"fetcher", "one-door fetcher"),
                         (r"landmine", "landmine method"), (r"honest", "honest-identity decision"),
                         (r"passive", "passive guard matching"), (r"thin", "thin-page rule")):
            if not re.search(rx, d, re.I):
                probs.append(f"docs/design.md lacks {what} ({rx})")
    need(not probs, " | ".join(probs[:10]))
    return (f"checks {len(names)}, landmines {lm}, signatures {nsig}, CI {sorted(pys)} on {sorted(oss)} - "
            f"all stated numbers match")


def d_check4():
    """Committed example: schema-valid, version 1.3.0, honest identity, <= 60 requests, polite config."""
    val = _validator()
    rep = json.loads(_read(EXAMPLE / "report.json"))
    log = json.loads(_read(EXAMPLE / "requests.log.json"))
    card = _read(EXAMPLE / "scout_card.md")
    probs = []
    errs = _errors(val, rep)
    if errs:
        probs.append(f"report.json fails docs/report.schema.json ({len(errs)}): {errs[:3]}")
    blob = json.dumps(rep, ensure_ascii=False)
    vers = set(re.findall(r"SiteScout/(\d+\.\d+\.\d+)", blob + card))
    if vers != {VERSION}:
        probs.append(f"versions in report/card: {sorted(vers) or 'none'}, want only {VERSION}")
    if str(rep.get("url", "")).rstrip("/") != SPEC_URL.rstrip("/"):
        probs.append(f"report url {rep.get('url')!r}, spec wants {SPEC_URL}")
    cfg = rep.get("config") or {}
    for key, ok, what in (("min_delay", lambda v: isinstance(v, (int, float)) and v >= 2, ">= 2"),
                          ("max_requests", lambda v: isinstance(v, int) and v <= 60, "<= 60"),
                          ("identity", lambda v: v == "honest", "'honest'"),
                          ("pace_fast", lambda v: v is False, "false (default)"),
                          ("proxy", lambda v: v is None, "null (default)"),
                          ("browser", lambda v: v == "auto", "'auto' (default)")):
        if not ok(cfg.get(key)):
            probs.append(f"config {key} = {cfg.get(key)!r}, want {what}")
    need(isinstance(log, list) and log, "requests.log.json is empty or not a list")
    if len(log) > 60:
        probs.append(f"{len(log)} requests logged, max 60")
    if rep.get("requests") != len(log):
        probs.append(f"report says {rep.get('requests')} requests, log has {len(log)}")
    ns = [e.get("n") for e in log]
    if ns != list(range(1, len(log) + 1)):
        probs.append(f"log numbering is not 1..{len(log)} (trimmed or edited?): {ns[:12]}")
    comps = [e for e in log if ident_kind(e) == "browser"]
    unl = [e.get("n") for e in comps if "comparison" not in str(e.get("purpose", "")).lower()]
    if unl or len(comps) > 1:
        probs.append(f"browser-identity requests not labelled as the one comparison: n={unl or [e.get('n') for e in comps]}")
    other = [(e.get("n"), e.get("identity")) for e in log if ident_kind(e) not in ("honest", "browser")]
    if other:
        probs.append(f"requests without the SiteScout identity: {other[:5]}")
    hosts = {urlsplit(str(e.get("url", ""))).hostname for e in log}
    if hosts != {"books.toscrape.com"}:
        probs.append(f"log touches hosts {sorted(map(str, hosts))}, want only books.toscrape.com")
    md = cfg.get("min_delay") or 0
    el = rep.get("elapsed_s")
    if isinstance(el, (int, float)) and isinstance(md, (int, float)) and el < (len(log) - 1) * md:
        probs.append(f"run took {el} s for {len(log)} requests: less than {len(log) - 1} gaps x {md} s")
    if has_git():
        rc, out = run(["git", "ls-files", "examples/"], cwd=REPO, timeout=30)
        tracked = [f for f in out.splitlines() if f.strip()]
        snaps = [f for f in tracked if "/snapshots/" in f]
        if snaps:
            probs.append(f"snapshots are committed: {snaps[:3]}")
        for f in ("scout_card.md", "report.json", "requests.log.json"):
            if f"examples/books.toscrape.com/{f}" not in tracked:
                probs.append(f"examples/books.toscrape.com/{f} is not committed")
    need(not probs, " | ".join(probs[:10]))
    return (f"schema-valid, SiteScout/{VERSION}, {len(log)} requests all honest, min_delay {md}, "
            f"elapsed {el} s >= {len(log) - 1} x {md} s. NOT verifiable from committed files: per-request "
            f"timestamps (the log has none), so each individual gap >= 2 s rests on the fetcher's min_delay")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def d_check5():
    """README example excerpt: every excerpt line is a line of the committed scout_card.md."""
    card = {_norm(ln) for ln in _read(EXAMPLE / "scout_card.md").splitlines() if ln.strip()}
    lines, mask = section_lines(3)
    ex = []
    for ln, inside in zip(lines[1:], mask[1:]):
        s = ln.strip()
        if inside:
            if not re.match(r"^(`{3,}|~{3,})", s):
                ex.append(ln)
        elif s.startswith(">"):
            ex.append(re.sub(r"^>\s?", "", s))
        elif s.startswith("|"):
            ex.append(s)
    ex = [_norm(x) for x in ex if x.strip() and not re.fullmatch(r"(\.\.\.|…|\[\.\.\.\]|<!--.*-->)", x.strip())]
    need(len(ex) >= 5, f"example excerpt has {len(ex)} line(s) (fenced block, quote or table in section 3); want >= 5")
    bad = [x for x in ex if x not in card]
    need(not bad, f"{len(bad)}/{len(ex)} excerpt lines are not in scout_card.md: {bad[:4]}")
    need(any("**verified**" in x for x in ex), "excerpt shows no verified claim row")
    return f"{len(ex)} excerpt lines, all found in examples/books.toscrape.com/scout_card.md"


def holdout_rows() -> dict:
    """{row name: (pct, fraction)} of the out-of-sample column in the scored run (read-only)."""
    txt = _read(HOLDOUT_RUN / "score.md")
    rows = {}
    for ln in txt.splitlines():
        m = re.match(r"^\|\s*\*\*(\w+)\*\*\s*\|\s*(\d+%)\s*\(([\d.]+/[\d.]+)\)", ln)
        if m:
            rows[m.group(1).lower()] = (m.group(2), m.group(3))
    need("overall" in rows, "could not read the out-of-sample column from holdout score.md")
    return rows


def holdout_names() -> list:
    """Site labels from the holdout files that are NOT practice/test sites (never written anywhere)."""
    txt = ""
    for p in (REPO / "holdout" / "sites.json", HOLDOUT_RUN / "score.md"):
        if p.exists():
            txt += p.read_text(encoding="utf-8", errors="replace")
    labels = set()
    for host in re.findall(r"https?://([A-Za-z0-9.-]+)", txt):
        parts = host.lower().split(".")
        if len(parts) < 2:
            continue
        lab = parts[-3] if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac") and len(parts[-1]) == 2 \
            else parts[-2]
        if not any(p in host.lower() for p in PRACTICE) and lab not in ("github", "example"):
            labels.add(lab)
    return sorted(labels)


def d_check6():
    """docs/holdout.md: method, out-of-sample table (95%, 109.5/115), date, version, 3 caveats; no real site names."""
    txt = _read(REPO / "docs" / "holdout.md")
    probs = []
    rows = holdout_rows()
    lines = txt.splitlines()
    for name, (pct, frac) in rows.items():
        hit = [ln for ln in lines if "|" in ln and re.search(rf"\b{name}\b", ln, re.I) and frac in ln]
        if not hit:
            probs.append(f"no table row for '{name}' with the out-of-sample {pct} ({frac})")
    for rx, what in ((r"95\s?%", "95%"), (r"109\.5\s*/\s*115", "109.5/115"),
                     (r"22 Oct(ober)? 2026|2026-10-22|Oct(ober)? 22,? 2026", "the date (22 Oct 2026)"),
                     (r"v?1\.1\b", "the version (v1.1)"), (r"out[- ]of[- ]sample", "'out-of-sample'"),
                     (r"blind", "scored blind"), (r"retire", "lockbox rule: fixed-against sites retired"),
                     (r"in[- ]sample", "retired sites counted as in-sample"), (r"label", "labelled sites"),
                     (r"(earlier|older|previous) version", "caveat 1: run on an earlier version"),
                     (r"seen[^.\n]{0,30}twice|twice[^.\n]{0,30}seen|no longer[^.\n]{0,20}(true|clean|real)?\s*holdout",
                      "caveat 2: sites seen twice / no longer a true holdout"),
                     (r"fresh[^.\n]{0,80}(batch|holdout|sites)", "caveat 3: a fresh labelled batch"),
                     (r"roadmap|planned", "caveat 3: ...on the roadmap")):
        if not re.search(rx, txt, re.I):
            probs.append(f"lacks {what}")
    for v, ctx in _num_mentions(txt, [r"\b(\d+)\s+out[- ]of[- ]sample sites"]):
        if v != "35":
            probs.append(f"says {v} out-of-sample sites, score.md says 35 ({ctx!r})")
    names = holdout_names()
    scan = {"docs/holdout.md": txt}
    try:
        scan["README 'How it's tested'"] = section(7)
    except Fail:
        pass
    for where, t in scan.items():
        leaked = [n for n in names if re.search(rf"\b{re.escape(n)}\b", t, re.I)]
        if leaked:
            probs.append(f"{where} names non-practice holdout site(s): {leaked}")
    need(not probs, " | ".join(probs[:10]))
    return f"{len(rows)} per-check rows match score.md, date/version/caveats present, 0 of {len(names)} real site names"


# competitor truth table from research/site-scout-landscape 01 + 06.
# columns: caniscrape, scraperecon, scrapeprobe, wafw00f
#   Y = research says it has it, P = partial / judgment call (any cell ok), N = research says it lacks it,
#   U = research does not say (cell must read no / not found / unknown)
# The feature is read "has X"; a row worded "No X" / "Never X" / "Without X" flips yes/no.
COMP_RULES = [
    ("passive matching", r"passive|no attack|without attack|non-?intrusive", "UUUN", False),
    ("attack payloads", r"payload|attack|malicious|aggressive|intrusive", "UUUY", True),
    ("captcha", r"captcha", "YNNU", True),
    ("conditional GET", r"304|conditional|change marker|etag|not.changed", "NNNN", True),
    ("paging reach", r"paging|pagination|page 2|last page|\breach", "NNPN", True),
    ("feed replay", r"feed|xhr|hidden api|\bapi\b|replay|endpoint", "NNNN", True),
    ("evidence levels", r"evidence|confidence|verified", "NPPU", True),
    ("landmine/holdout", r"landmine|fake (adversarial )?sites?|test suite|holdout|accuracy", "UUUU", True),
    ("identity comparison", r"comparison|labell?ed", "UUUU", True),
    ("crawl-delay", r"crawl.?delay", "YUYU", True),
    ("robots", r"robots", "YYYU", True),
    ("sitemap", r"sitemap", "UYYU", True),
    ("honest UA", r"honest|identif|user.?agent|\bua\b", "NUYU", True),
    ("concurrent bursts", r"concurren|simultaneous|parallel|burst|at once|workers", "YYYU", True),
    ("polite gaps", r"polite|gentle|\bgaps?\b|delay between|spaced", "PNPN", True),
    ("rate/pace test", r"pace|pacing|rate|throttl|requests per|speed", "YPUN", True),
    ("licence", r"licen[cs]e", "L", True),
    ("guard detection", r"guard|\bwafs?\b|anti-?bot|bot.?(protection|management|detection)|vendor|protection|firewall",
     "YYYY", True),
    ("TLS impersonation", r"\btls\b|curl.?cffi|impersonat|ja3", "YYNU", True),
    ("tech stack", r"\btech|framework|\bcms\b|wappalyzer", "UUYU", True),
    ("JS/raw location", r"javascript|\bjs\b|render|browser|playwright|raw page|raw html|where the data", "YNUN", True),
    ("embedded JSON", r"embedded|__next_data__|json-ld|app.?state|packed", "UYUU", True),
    ("difficulty score", r"score|0.?10|difficulty|rating", "YUUU", True),
    ("ToS/legal", r"\btos\b|terms|gdpr|legal|jurisdiction", "UUYU", True),
    ("effort/time", r"effort|projection|estimate|how long", "YUYU", True),
    ("honeypot", r"honeypot", "YUUU", True),
    ("proxy", r"prox(y|ies)", "YNNU", True),
    ("bulk data", r"bulk|open.?data|registry", "UUYU", True),
    ("sample scrape", r"sample", "UUYU", True),
    ("markdown report", r"markdown|\.md\b|\bcard\b", "UUYU", True),
    ("JSON output", r"json|machine.?readable", "UYYP", True),
    ("exit codes", r"exit.?codes?", "UUUU", True),
    ("batch mode", r"batch|many urls|list of urls", "UUUP", True),
    ("item structure", r"selector|fragil|load.?more|item list|structure", "NNNN", True),
    ("recommendation", r"recommend|advice|\bmethod\b", "YYPU", True),
    ("change detection", r"change detection|scan.to.scan|\bdiff\b|cloud|sync|telemetry", "YUUU", True),
    ("repo metadata", r"stars|created|last (push|commit|update)|maintained|\bage\b|commits|version|pypi", "PPPP", True),
    ("install/CLI", r"install|\bpip\b|\bcli\b|command.?line|open.?source", "PYPP", True),
]
LICENCES = {"caniscrape": "MIT", "scraperecon": "MIT", "scrapeprobe": "MIT", "wafw00f": "BSD"}


def cell_kind(c: str) -> str:
    s = re.sub(r"[*_`]", "", c).strip().lower()
    if not s or re.match(r"^(\?|unknown|not stated|not documented|not checked|n/?a\b|unclear|not verified)", s):
        return "UNKNOWN"
    if re.match(r"^(no\b|none\b|not found|not seen|never\b|✗|✘|❌|—|–|-$)", s):
        return "NO"
    if re.match(r"^(partial|partly|some\b|limited|~|◐|basic|only\b|opt-in)", s):
        return "PARTIAL"
    if re.match(r"^(yes\b|y$|✓|✔|✅|☑)", s):
        return "YES"
    return "DESC"


def comparison_table() -> list:
    """[(feature text, {competitor: cell})] from the README comparison table (either orientation)."""
    try:
        lines, mask = section_lines(9)
    except Fail:
        lines, mask = readme_layout_lines()
    for _, head, rows in md_tables(lines, mask):
        hl = [h.lower() for h in head]
        cols = {c: i for c in COMPETITORS for i, h in enumerate(hl) if c in re.sub(r"[`*\[\]]", "", h)}
        if len(cols) >= 3:
            return [(r[0], {c: (r[i] if i < len(r) else "") for c, i in cols.items()}) for r in rows], cols
        firsts = [re.sub(r"[`*\[\]]", "", r[0]).lower() for r in rows]
        rmap = {c: k for c in COMPETITORS for k, f in enumerate(firsts) if c in f}
        if len(rmap) >= 3:
            feats = []
            for j, h in enumerate(head[1:], 1):
                feats.append((h, {c: (rows[k][j] if j < len(rows[k]) else "") for c, k in rmap.items()}))
            return feats, rmap
    raise Fail("no comparison table naming at least 3 of caniscrape, scraperecon, ScrapeProbe, wafw00f")


def d_check7():
    """Comparison table: competitor cells agree with research reports 01 + 06; 'not found in the tools we checked'."""
    files = sorted(RESEARCH.glob("01-*.md")) + sorted(RESEARCH.glob("06-*.md"))
    need(len(files) >= 2, f"research reports 01/06 not found in {RESEARCH} (local folder, never committed)")
    research = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    per_tool = {}
    for c in COMPETITORS:  # lines naming the tool + its '### ' section in report 06
        own = [ln for ln in research.splitlines() if c in ln.lower()]
        own += re.findall(rf"^###[^\n]*{c}[^\n]*\n(.*?)(?=^###|^## |\Z)", research, re.I | re.M | re.S)
        per_tool[c] = "\n".join(own)
    feats, cols = comparison_table()
    probs, notes = [], []
    miss = [c for c in COMPETITORS if c not in cols]
    if miss:
        probs.append(f"comparison lacks {miss}")
    sec = section(9) if 9 in readme_layout()["spans"] else readme_layout()["text"]
    if not re.search(r"not found in the tools we checked", sec, re.I):
        probs.append("comparison does not use the wording 'not found in the tools we checked'")
    if not re.search(r"2026-11-13|13 Nov(ember)? 2026|Nov(ember)? 13,? 2026|Nov(ember)? 2026", sec, re.I):
        probs.append("comparison has no check date (research date 2026-11-13)")
    absolute = re.findall(r"nobody (?:else )?(?:does|has|offers|tests)|no other tool|no one else|the only tool|"
                          r"only tool that|first tool to|unlike any", readme_layout()["text"], re.I)
    if absolute:
        probs.append(f"absolute claims in README: {absolute[:3]}")
    checked = 0
    for feat, cells in feats:
        f = re.sub(r"[*_`]", "", feat).strip()
        rule = next((r for r in COMP_RULES if re.search(r[1], f, re.I)), None)
        neg = bool(re.match(r"^(no|never|without|does not|doesn't|not)\b", f, re.I))
        for comp, cell in cells.items():
            for num in re.findall(r"\d+(?:[.,]\d+)?", re.sub(r"[*_`]", "", cell)):
                if not re.search(rf"(?<![\d.,]){re.escape(num)}(?![\d])", per_tool[comp]):
                    probs.append(f"{comp} / {f!r}: number {num} not in what research 01/06 says about {comp}")
            k = cell_kind(cell)
            if rule is None:
                if k not in ("NO", "UNKNOWN"):
                    probs.append(f"{comp} / {f!r} = {cell!r}: no research fact found for this row")
                continue
            name, _, truth, flip_ok = rule
            if truth == "L":
                if LICENCES[comp].lower() not in cell.lower():
                    probs.append(f"{comp} licence cell {cell!r}, research says {LICENCES[comp]}")
                continue
            t = truth[COMPETITORS.index(comp)]
            kk = k
            if neg and flip_ok:
                kk = {"YES": "NO", "NO": "YES", "DESC": "PARTIAL"}.get(k, k)
            allowed = {"Y": {"YES", "PARTIAL", "DESC"}, "P": {"YES", "PARTIAL", "DESC", "NO", "UNKNOWN"},
                       "N": {"NO", "UNKNOWN"}, "U": {"NO", "UNKNOWN"}}[t]
            checked += 1
            if kk not in allowed:
                say = {"Y": "has it", "N": "lacks it", "U": "does not say (so: not found)"}[t]
                probs.append(f"{comp} / {f!r} = {cell!r} (rule '{name}'{', negated' if neg else ''}): research {say}")
        if rule is None:
            notes.append(f.strip()[:40])
    need(not probs, f"{len(probs)} problem(s): " + " | ".join(probs[:10]))
    extra = f"; rows with no research rule (all competitors 'no'): {notes}" if notes else ""
    return f"{len(feats)} rows, {checked} competitor cells agree with research 01/06, wording ok{extra}"


def _changelog_entries(text: str) -> list:
    """[(heading title, body)] for '## ' entries."""
    hs = [(i, t) for i, lvl, t in md_headings(text) if lvl == 2]
    lines = text.splitlines()
    return [(t, "\n".join(lines[i + 1:(hs[k + 1][0] if k + 1 < len(hs) else len(lines))])) for k, (i, t) in enumerate(hs)]


def d_check8():
    """CHANGELOG.md has 1.3.0 and 1.2.0 matching git history; pyproject keywords + classifiers; pip install . works."""
    probs = []
    text = _read(REPO / "CHANGELOG.md")
    ents = _changelog_entries(text)
    titles = [t for t, _ in ents]
    idx = {v: next((k for k, t in enumerate(titles) if re.search(rf"\[?v?{re.escape(v)}\]?(?![\d.])", t)), None)
           for v in ("1.3.0", "1.2.0")}
    for v, k in idx.items():
        if k is None:
            probs.append(f"no '## [{v}]' entry (headings: {titles[:6]})")
    if not re.search(r"keep a changelog|keepachangelog", text, re.I):
        probs.append("does not reference Keep a Changelog")
    if None not in idx.values():
        a, b = idx["1.3.0"], idx["1.2.0"]
        if not a < b:
            probs.append("1.3.0 must come before 1.2.0 (newest first)")
        earlier = [t for t in titles[b + 1:] if re.search(r"earlier|older|before|history|\b1\.[01]\b|\b0\.\d", t, re.I)]
        if not earlier:
            probs.append(f"no short 'earlier' entry after 1.2.0 (headings after it: {titles[b + 1:]})")
        b13, b12 = ents[a][1], ents[b][1]
        if not re.search(r"^###\s+(Added|Changed|Fixed|Removed|Deprecated|Security)\b", b13, re.M):
            probs.append("1.3.0 has no Keep a Changelog '### Added/Changed/Fixed' groups")
        for rx, what in ((r"signature|guard", "passive guard signatures"), (r"\blog", "logging / scout.log"),
                         (r"schema", "report schema"), (r"--output", "--output -"), (r"exit code", "exit codes"),
                         (r"\bCI\b|github actions|continuous integration", "CI"), (r"pytest|ruff", "pytest/ruff"),
                         (r"robots", "verdict fix when robots forbids the start page")):
            if not re.search(rx, b13, 0 if "CI" in rx else re.I):
                probs.append(f"1.3.0 entry omits {what} (in git history for 1.3.0)")
        for rx, what in ((r"pip|pyproject|packag|install", "packaging / pip install"), (r"licen[cs]e|\bMIT\b", "licence"),
                         (r"honest|identity|user.?agent", "honest identity"), (r"robots", "robots token check")):
            if not re.search(rx, b12, re.I):
                probs.append(f"1.2.0 entry omits {what} (in git history for 1.2.0)")
        for rx, what in ((r"exit code", "exit codes"), (r"--output|schema_version|report\.schema", "machine output"),
                         (r"\bCI\b|github actions", "CI"), (r"\bruff\b|\bpytest\b", "pytest/ruff"),
                         (r"scout\.log|--quiet", "logging")):
            if re.search(rx, b12, 0 if "CI" in rx else re.I):
                probs.append(f"1.2.0 entry claims {what}, which git history puts in 1.3.0")
        dates = {}
        for v, k in idx.items():
            m = re.search(r"(\d{4}-\d{2}-\d{2})", titles[k])
            if m:
                dates[v] = m.group(1)
            elif not re.search(r"unreleased", titles[k], re.I):
                probs.append(f"{v} heading has no ISO date: {titles[k]!r}")
        if has_git():
            rc, out = run(["git", "log", "--format=%ad", "--date=short", "-S", 'version = "1.3.0"', "--", "pyproject.toml"],
                          cwd=REPO, timeout=60)
            bump = min(out.split()) if rc == 0 and out.split() else None
            if bump and dates.get("1.3.0") and dates["1.3.0"] < bump:
                probs.append(f"1.3.0 dated {dates['1.3.0']}, before the commit that set it ({bump})")
        if dates.get("1.3.0") and dates.get("1.2.0") and dates["1.2.0"] > dates["1.3.0"]:
            probs.append(f"1.2.0 dated after 1.3.0 ({dates['1.2.0']} > {dates['1.3.0']})")
    proj = tomllib.loads(_read(REPO / "pyproject.toml")).get("project", {})
    kw = proj.get("keywords") or []
    cls = proj.get("classifiers") or []
    if len(kw) < 3 or not any(re.search(r"scrap", k, re.I) for k in kw):
        probs.append(f"pyproject keywords {kw!r}: want 3+ including a scraping keyword")
    if not cls:
        probs.append("pyproject has no classifiers")
    try:
        import trove_classifiers as tc
        unknown = [c for c in cls if c not in tc.classifiers]
        how = "trove-classifiers"
    except ImportError:
        unknown = [c for c in cls if not re.fullmatch(r"[A-Z][\w ./()+-]*( :: [\w ./()+-]+)+", c)]
        how = "format only (trove-classifiers not installed)"
    if unknown:
        probs.append(f"invalid classifiers ({how}): {unknown[:4]}")
    pyv = [c.rsplit("::", 1)[1].strip() for c in cls if re.match(r"Programming Language :: Python :: 3\.\d+$", c)]
    low = [v for v in pyv if tuple(map(int, v.split("."))) < (3, 10)]
    if low:
        probs.append(f"classifiers claim Python {low}, requires-python is >=3.10")
    lic = [c for c in cls if c.startswith("License ::")]
    if lic and not any("MIT" in c for c in lic):
        probs.append(f"licence classifier {lic} is not MIT")
    need(not probs, " | ".join(probs[:10]))
    if RESULTS.get("p1", (False,))[0]:
        inst = "pip install . ok (phase-1 check 1 passed this run)"
    else:
        inst = "pip install . ok: " + p_check1()
    return f"CHANGELOG 1.3.0/1.2.0/earlier ok; {len(kw)} keywords, {len(cls)} classifiers ({how}); {inst}"


def d_check9():
    """Privacy: check-7 rules, no private terms or AI-tool names in tracked files or commits; ID-noreply author only."""
    info = p_check7()
    probs = []
    rc, out = run(["git", "log", "--format=%an <%ae>%n%cn <%ce>%n%B%n@@END@@"], cwd=REPO, timeout=60)
    need(rc == 0 and out.strip(), f"git log failed (rc {rc})")
    commits = [c.strip() for c in out.split("@@END@@") if c.strip()]
    people = {ln.strip() for c in commits for ln in c.splitlines()[:2]}
    if people != {AUTHOR}:
        probs.append(f"authors/committers other than {AUTHOR}: {sorted(people - {AUTHOR})}")
    msgs = "\n".join("\n".join(c.splitlines()[2:]) for c in commits)
    for rx, what in ((PRIVATE_RE, "private terms"), (AI_TOOL_RE, "AI tool/vendor names"),
                     (r"co-authored-by|generated with", "attribution lines")):
        hits = [ln.strip()[:80] for ln in msgs.splitlines() if re.search(rx, ln, re.I)]
        if hits:
            probs.append(f"commit messages contain {what}: {len(hits)} line(s)")
    ai_rx = "cla" + "ude|anth" + "ropic"
    rc, out = run(["git", "grep", "-nIiE", ai_rx], cwd=REPO, timeout=60)
    if rc != 1:
        probs.append(f"tracked files mention the AI vendor/tool: {[ln[:80] for ln in out.splitlines()[:5]]}")
    rc, out = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, timeout=60)
    for f in [x.strip() for x in out.splitlines() if x.strip()]:
        p = REPO / f
        if p.is_file() and p.stat().st_size < 2_000_000 and re.search(ai_rx, f + p.read_text("utf-8", "ignore"), re.I):
            probs.append(f"new file {f} mentions the AI vendor/tool")
    rc, out = run(["git", "grep", "-IohE", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+"], cwd=REPO, timeout=60)
    ok_mail = {"muhammadadil97113@gmail.com", AUTHOR.split("<")[1].rstrip(">"), "git@github.com"}
    mails = {m for m in out.split() if m not in ok_mail and not re.search(
        r"@(example\.(com|org|net)|[\w.-]+\.(test|invalid|example|localhost))$|@localhost$", m, re.I)}
    if mails:
        probs.append(f"tracked files contain e-mail addresses other than the public contact: {sorted(mails)[:5]}")
    need(not probs, " | ".join(probs[:8]))
    return f"{info}; {len(commits)} commits by {AUTHOR}; no AI names, attribution lines or stray e-mails"


def d_check10():
    """All earlier hidden checks (phase 2: 1-11 incl. pytest 9 and ruff 10; phase 1: p1, p3-p8) still pass."""
    for k in EARLIER:
        if k in RESULTS:
            continue
        t0 = time.time()
        try:
            info = CHECKS[k]()
            RESULTS[k] = (True, info)
        except Fail as e:
            RESULTS[k] = (False, str(e))
        except Exception as e:
            RESULTS[k] = (False, f"crashed {type(e).__name__}: {e}")
        print(f"      (d10 ran check {k}: {'PASS' if RESULTS[k][0] else 'FAIL'}, {time.time() - t0:.0f}s)", flush=True)
    bad = [k for k in EARLIER if not RESULTS[k][0]]
    need(not bad, f"earlier hidden checks failing: {bad}: " + " | ".join(f"{k}: {RESULTS[k][1][:120]}" for k in bad[:4]))
    return (f"all {len(EARLIER)} earlier checks pass (pytest = check 9, ruff = check 10). NOT checked here: CI green on "
            f"origin/main after the push - the lead verifies that")


CHECKS = {"d1": d_check1, "d2": d_check2, "d3": d_check3, "d4": d_check4, "d5": d_check5, "d6": d_check6,
          "d7": d_check7, "d9": d_check9,
          "1": check1, "2": check2, "3": check3, "4": check4, "5": check5, "6": check6, "7": check7,
          "8": check8, "9": check9, "10": check10, "11": check11,
          "p1": p_check1, "p3": p_check3, "p4": p_check4, "p5": p_check5, "p6": p_check6,
          "p7": p_check7, "p8": p_check8,
          "d8": d_check8, "d10": d_check10}


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
    for n in pick:
        t0 = time.time()
        try:
            info = CHECKS[n]()
            RESULTS[n] = (True, info)
            print(f"PASS  check {n:>3} ({time.time() - t0:.0f}s): {info}", flush=True)
        except Fail as e:
            RESULTS[n] = (False, str(e))
            print(f"FAIL  check {n:>3} ({time.time() - t0:.0f}s): {e}", flush=True)
        except Exception as e:
            RESULTS[n] = (False, f"crashed {type(e).__name__}: {e}")
            print(f"FAIL  check {n:>3} ({time.time() - t0:.0f}s): check crashed {type(e).__name__}: {e}", flush=True)
    results = {n: RESULTS[n][0] for n in pick}
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    failed = [n for n, ok in results.items() if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
