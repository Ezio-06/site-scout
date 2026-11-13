"""Runs every discovered check in order. One check failing never stops the run."""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field

from .checks import SkipCheck, discover
from .config import ScoutConfig
from .context import ScoutContext
from .logsetup import run_log_file

log = logging.getLogger("scout")


@dataclass
class CheckResult:
    name: str
    title: str
    status: str                  # ok | skipped | error
    findings: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    reason: str = ""
    seconds: float = 0.0


def run_scout(cfg: ScoutConfig, echo=None) -> ScoutContext:
    """Run the checks. Progress goes to the `scout` logger; `echo`, if given, also receives each
    progress line (kept so older callers keep working)."""
    ctx = ScoutContext(cfg)
    with run_log_file(ctx.out_dir):
        return _run(ctx, cfg, echo)


def _run(ctx: ScoutContext, cfg: ScoutConfig, echo) -> ScoutContext:
    def say(msg, level=logging.INFO):
        log.log(level, msg)
        if echo:
            echo(msg)

    checks = discover()
    if cfg.only:
        checks = [c for c in checks if c.name in cfg.only]
    if cfg.skip:
        checks = [c for c in checks if c.name not in cfg.skip]

    say(f"Scouting {cfg.url}")
    say(f"  output: {ctx.out_dir}")
    try:
        for idx, cls in enumerate(checks):
            if "interrupted" in ctx.facts:
                break
            chk = cls()
            t0 = time.perf_counter()
            res = CheckResult(name=chk.name, title=chk.title, status="ok")
            try:
                reason = chk.should_run(ctx)
                if reason:
                    raise SkipCheck(reason)
                say(f"  - {chk.title} ...")
                res.findings = chk.run(ctx) or {}
            except SkipCheck as e:
                res.status, res.reason = "skipped", str(e)
                say(f"  - {chk.title}: skipped ({e})")
            except KeyboardInterrupt:   # Ctrl+C: stop politely, still write the card
                res.status, res.reason = "skipped", "interrupted (Ctrl+C)"
                ctx.facts["interrupted"] = f"stopped by Ctrl+C during '{chk.title}'"
                say("  ! interrupted - writing what was found so far", logging.WARNING)
            except Exception as e:  # a broken check must not kill the run
                res.status, res.reason = "error", f"{type(e).__name__}: {e}"
                log.debug("check '%s' crashed: %s", chk.name, traceback.format_exc())
                say(f"  - {chk.title}: ERROR {res.reason}", logging.WARNING)
            res.notes, res.warnings = chk.notes, chk.warnings
            if res.status == "error":   # say it on the card, then carry on
                res.warnings = res.warnings + [f"This check crashed ({res.reason}); its findings may be "
                                               "incomplete. Re-run with -v to see where."]
            res.seconds = round(time.perf_counter() - t0, 2)
            ctx.results[chk.name] = res
    finally:
        ctx.close()
    return ctx
