"""Ending a case: its result from its checks and crashes, with evidence for anything not passing."""

from __future__ import annotations

import sys

from . import coverage
from . import device as dev
from . import report as rpt
from .adb import AdbError


def evidence_shot(ctx, label: str) -> str | None:
    """Screenshot for evidence; a screen that blocks capture (FLAG_SECURE) must not lose the result."""
    try:
        return ctx.rel(dev.screenshot(ctx.adb, ctx.artifact("shots", label, "png")))
    except AdbError as exc:
        print(f"warning: no screenshot ({exc}); the app may block capture on this screen", file=sys.stderr)
        return None


def _end_case(ctx, status: str | None, reason: str | None) -> str:
    run = ctx.run
    case = run.current_case()
    events = run.events()
    checks = [e for e in rpt.case_events(events, case) if e["type"] == "check"]
    failed = [c for c in checks if not c["ok"]]
    states = coverage.expectation_states(coverage.expects_of(events, case), checks)
    unverified = "; ".join(f"{s['id']} {s['text']}" for s in states if s["state"] == "unverified")
    package = run.meta().get("package") or None
    crash = dev.crashes(ctx.adb, package)
    if status is None:
        status = "fail" if failed or crash else ("blocked" if unverified else "pass" if checks else "blocked")
        if not reason:
            if failed:
                reason = "failed: " + "; ".join(c["what"] for c in failed)
            elif crash:
                reason = "app crashed or ANR"
            elif not checks:
                reason = "no checks were recorded"
        if unverified and status != "fail":
            reason = "; ".join(r for r in (reason, f"not verified: {unverified}") if r)
    elif status == "pass" and (failed or crash):
        status = "fail"
        why = f"{len(failed)} failed check(s)" if failed else "app crashed"
        reason = f"{why}; marked pass by the tester" + (f": {reason}" if reason else "")
    elif status == "pass" and unverified:
        status = "blocked"
        reason = "marked pass by the tester" + (f": {reason}" if reason else "") + f"; not verified: {unverified}"
    event = {"type": "case_end", "case": case, "status": status, "reason": reason or ""}
    if states:
        event["expects"] = states
    if crash:
        p = ctx.artifact("logs", f"{case}-crash", "txt")
        p.write_text(crash)
        event["crash"] = ctx.rel(p)
    if status in ("fail", "blocked") or crash:
        shot = evidence_shot(ctx, f"{case}-end")
        log = ctx.artifact("logs", f"{case}-logcat", "txt")
        log.write_text(dev.logcat_dump(ctx.adb))
        event["evidence"] = ([shot] if shot else []) + [ctx.rel(log)]
    ctx.log(event)
    return status
