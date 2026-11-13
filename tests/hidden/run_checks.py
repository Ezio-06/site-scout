r"""Hidden acceptance checks for spec phase1-packaging (checks 1-10; 11 is manual).

    python tests/hidden/run_checks.py          all checks
    python tests/hidden/run_checks.py 3 5      only checks 3 and 5

Run from anywhere with the project venv active (checks 2-6 and 9 use this Python
to run the repo's code). Only local 127.0.0.1 fake sites are scouted. Checks 1 and 8
need PyPI access for pip build dependencies.

STRING / FORMAT ASSUMPTIONS (case-insensitive substring matching unless noted)
  UA (honest)    : User-Agent starts with "SiteScout/1.2.0" and contains
                   "https://github.com/Ezio-06/site-scout"
  UA (browser)   : contains "Chrome/" and not "SiteScout"
  UA exemption   : requests whose UA starts with "python-requests" (the deliberate
                   library-default UA filter test) are ignored by the identity checks
  log identity   : every requests.log.json entry has key "identity"; value containing
                   "honest" or "sitescout" = honest, "browser" or "chrome" = browser
  comparison     : log entry whose "purpose" contains "comparison" (exactly one in check 5)
  card, check 5  : contains "blocks honest bots" AND matches regex r"identity\W{0,6}browser"
  claims row     : a "|"-table row in scout_card.md with "accepts an honest bot identity"
                   and bold evidence **broken** (check 5) / **verified** (check 3);
                   a row "blocks honest bots" with **verified** is accepted for check 5 too
  robots, chk 4  : a card line containing both "robots" and "disallow" (not "not disallow")
  not tested, 1  : a card line with "not tested" and ("browser" or "playwright"), and an
                   install hint matching r"pip install[^\n]*(\[[^\]]*browser|playwright)"
  version        : `--version` output stripped == "site-scout 1.2.0"
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

from hidden_sites import FakeSite  # noqa: E402

VERSION = "1.2.0"
REPO_URL = "https://github.com/Ezio-06/site-scout"
AUTHOR = "Ezio-06 <Ezio-06@users.noreply.github.com>"
# private terms live in an untracked local file (never committed); see check 7
_PT = REPO / "private_terms.txt"
_PL = _PT.read_text(encoding="utf-8").splitlines() if _PT.exists() else []
_FW = next((x[4:] for x in _PL if x.startswith("dir:")), "private-folder-not-configured")
EXCLUDED = [_FW + "/", "holdout/", "scouts/", "old_versions/", "tests/out/", "research/", "specs/", ".venv/"]
# built from pieces so this file never contains the private words itself
_W = [x[5:] for x in _PL if x.startswith("word:")] or ["(?!)"]
PRIVATE_RE = "|".join(_W)

ENV = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    ENV.pop(k, None)

TMP_ROOT = Path(tempfile.mkdtemp(prefix="scout_hidden_"))
_CACHE: dict = {}


class Fail(Exception):
    pass


def run(cmd, cwd=None, timeout=300):
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=ENV,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"")
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return -999, f"TIMEOUT after {timeout}s\n{out}"
    except FileNotFoundError as e:
        return -998, f"not found: {e}"


def tail(s: str, n: int = 15) -> str:
    lines = s.strip().splitlines()
    return "\n      | " + "\n      | ".join(lines[-n:]) if lines else " (no output)"


def need(cond, msg):
    if not cond:
        raise Fail(msg)


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


def scout_run(site: FakeSite, name: str, extra=()):
    out = TMP_ROOT / f"out_{name}"
    cmd = [sys.executable, REPO / "run_scout.py", site.start_url, "--out", out,
           "--no-pace", "--browser", "never", "--min-delay", "0", *extra]
    t0 = time.time()
    rc, text = run(cmd, cwd=REPO, timeout=400)
    secs = time.time() - t0
    card_p, log_p = out / "scout_card.md", out / "requests.log.json"
    need(rc == 0, f"scout exited {rc} after {secs:.0f}s:{tail(text)}")
    need(card_p.exists(), f"no scout_card.md in {out}:{tail(text)}")
    need(log_p.exists(), f"no requests.log.json in {out}")
    log = json.loads(log_p.read_text(encoding="utf-8"))
    need(isinstance(log, list), "requests.log.json is not a list")
    return card_p.read_text(encoding="utf-8"), log, site.snapshot(), text


def same_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


# ------------------------------------------------------------------ checks
def check1():
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
                   "print([m for m in ('playwright','curl_cffi') if u.find_spec(m)])"])
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
    return f"venv install ok, --version ok, {len(lines)} checks listed, card says not tested + hint"


def check2():
    """python run_scout.py --version, single version source."""
    rc, out = run([sys.executable, REPO / "run_scout.py", "--version"], cwd=REPO, timeout=60)
    need(rc == 0 and out.strip() == f"site-scout {VERSION}",
         f"run_scout.py --version gave rc {rc}, output {out.strip()!r}")
    rc, out = run([sys.executable, "-c", "import scout;print(scout.__version__)"], cwd=REPO, timeout=60)
    need(out.strip() == VERSION, f"scout.__version__ is {out.strip()!r}, want {VERSION!r}")
    pp = REPO / "pyproject.toml"
    need(pp.exists(), "no pyproject.toml")
    data = tomllib.loads(pp.read_text(encoding="utf-8"))
    proj = data.get("project", {})
    need("version" not in proj, f"pyproject [project] hard-codes version {proj.get('version')!r} (two sources)")
    need("version" in proj.get("dynamic", []), "pyproject does not mark version as dynamic")
    attr = str(data.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version", ""))
    need("scout.__version__" in attr, f"dynamic version not read from scout.__version__ ({attr!r})")
    return f"prints 'site-scout {VERSION}', version only in scout.__version__"


def check3():
    """Default run sends the honest SiteScout UA."""
    with FakeSite("honest") as site:
        card, log, hits, _ = scout_run(site, "honest")
    need(hits, "fake site saw no requests")
    start = [ua for p, ua in hits if p.split("?")[0] == "/list"]
    robots = [ua for p, ua in hits if p.split("?")[0] == "/robots.txt"]
    need(start, "start page /list never requested")
    need(robots, "robots.txt never requested")
    for label, uas in (("start page", start), ("robots.txt", robots)):
        bad = [ua for ua in uas if not is_exempt(ua) and
               not (ua.startswith(f"SiteScout/{VERSION}") and REPO_URL in ua)]
        need(not bad, f"{label} got non-honest UA(s): {sorted(set(bad))[:3]}")
    first = start[0] if not is_exempt(start[0]) else next((u for u in start if not is_exempt(u)), "")
    need(first.startswith(f"SiteScout/{VERSION}") and REPO_URL in first, f"first start-page UA {first!r}")
    chrome = [(p, ua) for p, ua in hits if is_chrome(ua)]
    need(not chrome, f"site accepts honest bots but Chrome UA was used anyway: {chrome[:3]}")
    missing = [e.get("n") for e in log if "identity" not in e]
    need(not missing, f"requests.log.json entries without 'identity': n={missing[:10]}")
    start_logs = [e for e in log if same_url(str(e.get("url", "")), site.start_url)]
    need(start_logs and ident_kind(start_logs[0]) == "honest",
         f"start-page log entry identity is {start_logs[0].get('identity') if start_logs else None!r}")
    rows = claim_rows(card)
    ok = any(("accepts an honest bot identity" in r.lower() and ev == "verified") or
             ("blocks honest bots" in r.lower() and ev == "broken") for r, ev in rows)
    need(ok, f"card lacks 'Site accepts an honest bot identity' = verified; rows: {rows}")
    return f"UA {first!r}; {len(hits)} requests, none Chrome; claim verified"


def check4():
    """robots.txt disallows SiteScout (allows *): stricter answer wins."""
    with FakeSite("robots") as site:
        card, log, hits, _ = scout_run(site, "robots")
    need(any(p.startswith("/robots.txt") for p, _ in hits), "robots.txt was never fetched")
    lines = [ln for ln in card.lower().splitlines()
             if "robots" in ln and "disallow" in ln and "not disallow" not in ln]
    need(lines, "card does not say robots.txt disallows the page")
    bad_log = [e.get("url") for e in log if "/private/" in str(e.get("url", ""))]
    need(not bad_log, f"disallowed URL(s) in requests.log.json: {bad_log[:5]}")
    bad_hits = [p for p, _ in hits if p.startswith("/private/")]
    need(not bad_hits, f"fake site received disallowed request(s): {bad_hits[:5]}")
    return f"card: {lines[0].strip()[:90]!r}; 0 disallowed requests"


def check5():
    """Honest UA gets 403, Chrome gets the page: one labelled comparison, then Chrome."""
    with FakeSite("blockhonest") as site:
        card, log, hits, _ = scout_run(site, "blockhonest")
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
    # server side: what really went over the wire
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
    ok = any(("blocks honest bots" in r.lower() and ev == "verified") or
             ("accepts an honest bot identity" in r.lower() and ev == "broken") for r, ev in rows)
    need(ok, f"claims table lacks honest-identity claim with the blocked result; rows: {rows}")
    return f"1 comparison (n={log[ci].get('n')}), {len(later)} later requests none honest, {n_chrome} Chrome hits"


def check6():
    """--identity browser: SiteScout UA never sent."""
    with FakeSite("blockhonest") as site:
        card, log, hits, _ = scout_run(site, "browser_identity", ["--identity", "browser"])
    need(hits, "fake site saw no requests")
    honest = [p for p, ua in hits if "sitescout" in ua.lower()]
    need(not honest, f"SiteScout UA sent with --identity browser: {honest[:5]}")
    honest_log = [e.get("n") for e in log if ident_kind(e) == "honest"]
    need(not honest_log, f"log says honest identity for entries {honest_log[:5]}")
    got = [e for e in log if same_url(str(e.get("url", "")), site.start_url) and e.get("status") == 200]
    need(got, "start page never answered 200 - run did not get in with the browser identity")
    return f"{len(hits)} requests, 0 SiteScout UAs, start page 200"


def check7():
    """git: allowlisted folders untracked, no private words."""
    need(has_git(), f"no .git in {REPO} - repo not initialised yet")
    rc, out = run(["git", "ls-files"], cwd=REPO, timeout=60)
    need(rc == 0, f"git ls-files failed:{tail(out)}")
    files = [f.strip() for f in out.splitlines() if f.strip()]
    need(files, "git ls-files lists nothing - nothing tracked")
    bad = [f for f in files if any(f.startswith(x) for x in EXCLUDED)]
    need(not bad, f"{len(bad)} tracked file(s) under excluded folders: {bad[:8]}")
    named = [f for f in files if re.search(PRIVATE_RE, f, re.I)]
    need(not named, f"tracked file names contain private words: {named[:5]}")
    rc, out = run(["git", "grep", "-niE", PRIVATE_RE], cwd=REPO, timeout=60)
    need(rc == 1, f"git grep found private words (rc {rc}):{tail(out, 12)}")
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
    return f"{len(files)} tracked files, none excluded, no private words, folders kept on disk"


def check8():
    """Wheel ships data files; MIT license; extras declared; wafw00f credited."""
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
        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta = z.read(meta_name).decode("utf-8", "replace")
        ep_name = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        eps = z.read(ep_name).decode() if ep_name else ""
        lic_files = [n for n in names if ".dist-info/" in n and "licen" in n.lower().rsplit("/", 1)[-1]]
        lic_text = " ".join(z.read(n).decode("utf-8", "replace") for n in lic_files)
    need(re.search(rf"^Version: {re.escape(VERSION)}\s*$", meta, re.M), "wheel METADATA Version is not 1.2.0")
    need(re.search(r"^site-scout\s*=", eps, re.M), "no 'site-scout' console script in wheel")
    for extra, pkg in (("browser", "playwright"), ("tls", "curl_cffi")):
        need(re.search(rf"^Provides-Extra: {extra}\s*$", meta, re.M), f"extra [{extra}] not declared")
        reqs = [ln for ln in meta.splitlines() if ln.lower().startswith("requires-dist:")
                and re.search(rf"\b{pkg.replace('_', '[-_]')}\b", ln, re.I)]
        need(reqs, f"{pkg} not listed for extra [{extra}]")
        need(all(f'extra == "{extra}"' in ln or f"extra == '{extra}'" in ln for ln in reqs),
             f"{pkg} is a core dependency, must be only in [{extra}]: {reqs}")
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
    need("wafw00f" in credit.lower() and "bsd" in credit.lower(),
         "README/NOTICE does not credit wafw00f (BSD-3)")
    return f"{wheels[0].name}: data files present, MIT, extras browser/tls, wafw00f credited"


def check9():
    """Existing landmine tests pass."""
    for script, to in (("tests/test_paging.py", 120), ("tests/run_tests.py", 1500)):
        rc, out = run([sys.executable, REPO / script], cwd=REPO, timeout=to)
        need(rc == 0 and "all passed" in out.lower(), f"{script} rc {rc}:{tail(out, 20)}")
    return "test_paging.py and run_tests.py: all passed"


def check10():
    """Commits authored by Ezio-06 only, no Claude / co-author lines."""
    need(has_git(), f"no .git in {REPO} - repo not initialised yet")
    rc, out = run(["git", "log", "--format=%an <%ae>%n%B%n@@END@@"], cwd=REPO, timeout=60)
    need(rc == 0 and out.strip(), f"git log failed or no commits (rc {rc}):{tail(out)}")
    commits = [c.strip() for c in out.split("@@END@@") if c.strip()]
    authors = {c.splitlines()[0].strip() for c in commits}
    need(authors == {AUTHOR}, f"unexpected authors: {sorted(authors - {AUTHOR})}")
    bad = [ln.strip() for ln in out.splitlines() if re.search(r"co-authored-by|claude", ln, re.I)]
    need(not bad, f"forbidden lines in commit messages: {bad[:5]}")
    rc, cm = run(["git", "log", "--format=%cn <%ce>"], cwd=REPO, timeout=60)
    others = sorted({x.strip() for x in cm.splitlines() if x.strip()} - {AUTHOR})
    note = f" (NOTE: committer differs: {others})" if others else ""
    return f"{len(commits)} commit(s), author {AUTHOR} only{note}"


CHECKS = {1: check1, 2: check2, 3: check3, 4: check4, 5: check5,
          6: check6, 7: check7, 8: check8, 9: check9, 10: check10}


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pick = [int(a) for a in argv if a.isdigit()] or list(CHECKS)
    unknown = [n for n in pick if n not in CHECKS]
    if unknown:
        print(f"unknown check number(s): {unknown}; valid 1-10")
        return 2
    results = {}
    for n in pick:
        t0 = time.time()
        try:
            info = CHECKS[n]()
            results[n] = True
            print(f"PASS  check {n:>2} ({time.time() - t0:.0f}s): {info}", flush=True)
        except Fail as e:
            results[n] = False
            print(f"FAIL  check {n:>2} ({time.time() - t0:.0f}s): {e}", flush=True)
        except Exception as e:
            results[n] = False
            print(f"FAIL  check {n:>2} ({time.time() - t0:.0f}s): check crashed {type(e).__name__}: {e}", flush=True)
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    failed = [n for n, ok in results.items() if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" +
          (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
