"""Ending a case: its result from its checks and crashes, with evidence for anything not passing."""

from __future__ import annotations

import re
import sys

from . import coverage
from . import device as dev
from . import report as rpt
from .adb import AdbError

EXIT_ERROR = 2  # cli.EXIT_ERROR: adb or the command itself failed
EXIT_INSTEAD = 4  # cli.EXIT_INSTEAD: `wait A --or B` saw B, the screen the plan named as failure


def evidence_shot(ctx, label: str) -> str | None:
    """Screenshot for evidence; a screen that blocks capture (FLAG_SECURE) must not lose the result."""
    try:
        return ctx.rel(dev.screenshot(ctx.adb, ctx.artifact("shots", label, "png")))
    except AdbError as exc:
        print(f"warning: no screenshot ({exc}); the app may block capture on this screen", file=sys.stderr)
        return None


def blocked_end(events: list[dict], case: str, reason: str, checks: list[dict], cause: str | None = None) -> dict:
    """The case_end of a case that could not finish, with what its checks so far proved. A check
    that already failed on the app still fails it: closing a case early never hides one."""
    states = coverage.expectation_states(coverage.expects_of(events, case), checks)
    status, reason, judged = verdict(checks, states, False, "blocked", reason)
    cause = judged or cause
    end = {"type": "case_end", "case": case, "status": status, "reason": reason, **({"cause": cause} if cause else {})}
    if states:
        end["expects"] = states
    return end


def close_run(run, open_reason: str, not_run_reason: str) -> None:
    """End the case still open and give every requested case that never started its row.

    Touches no device, so callers run it before any device cleanup that might fail.
    """
    events = run.events()
    if case := run.current_case():
        checks = [e for e in rpt.case_events(events, case) if e["type"] == "check"]
        run.log(blocked_end(events, case, open_reason, checks))
    for c in coverage.not_started(events):
        run.log({"type": "not_run", "case": c["id"], "title": c["title"], "reason": not_run_reason})


def _end_case(ctx, status: str | None, reason: str | None) -> str:
    run = ctx.run
    case = run.current_case()
    events = run.events()
    checks = [e for e in rpt.case_events(events, case) if e["type"] == "check"]
    states = coverage.expectation_states(coverage.expects_of(events, case), checks)
    package = run.meta().get("package") or None
    crash = dev.crashes(ctx.adb, package)
    status, reason, cause = verdict(checks, states, bool(crash), status, reason)
    event = {"type": "case_end", "case": case, "status": status, "reason": reason, **({"cause": cause} if cause else {})}
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


# app: evidence about the app. step: a plan step could not run (a target not on screen, a wait
# that ran out): the app or the test may be at fault, so it is named, not blamed. device: adb lost
# the device. unverified: nothing proved an expected result (no checks, or a check never ran).
# precondition: a plan's `require` did not hold, so the case never reached the state it tests.
# Only a lost device: a frozen app also makes adb time out, and that is evidence about the app's side.
DEVICE_ERROR = re.compile(r"\bdevice\b[^:]*\b(?:not found|offline|unauthori[sz]ed)\b|\bdevice offline\b"
                          r"|\bno devices?\b|\bunauthori[sz]ed\b", re.I)
# Runs from before steps were recorded: a plan step that failed was logged as "line N: …", a
# replayed one as "step N: …".
STEP_LINE = re.compile(r"^(?:line|step) \d+: ")


def verdict(checks: list[dict], states: list[dict], crash: bool, status: str | None = None,
            reason: str | None = None) -> tuple[str, str, str | None]:
    """A case's (status, reason, cause) from its checks, its expected results and a crash, with no
    device call: the judgment `tests/test_judgment.py` holds to its labelled cases.

    Only evidence about the app fails a case: a failed check, an error screen a plan named with
    `--or`, a crash. A step that could not run (a target not on screen, a wait that ran out, the
    device gone) or a screen that could not be read says nothing about the app: the case is
    blocked, naming what happened (step or device). The reason always names the evidence first;
    a reason handed in is added, never put in its place."""
    failed = [c for c in checks if not c["ok"]]
    app = [c for c in failed if _app_evidence(c)]
    unrun = [c for c in failed if not _app_evidence(c)]
    unverified = "; ".join(f"{s['id']} {s['text']}" for s in states if s["state"] == "unverified")
    if (crash or app) and status not in (None, "pass", "fail"):
        # Nobody can hand in a status over a crash or a failed check: the app is the one that failed.
        return "fail", _join("failed: " + "; ".join(c["what"] for c in app) if app else "",
                             "app crashed or ANR" if crash else "",
                             f"marked {status}" + (f": {reason}" if reason else "")), "app"
    if status is not None and status != "pass":
        return status, reason or "", "app" if status == "fail" else None
    told = f"marked pass by the tester{f': {reason}' if reason else ''}" if status == "pass" else ""
    given = "" if told else reason or ""
    if app or crash:
        if told:
            why = f"{len(app)} failed check(s)" if app else "app crashed"
            return "fail", f"{why}; {told}", "app"
        return "fail", _join("failed: " + "; ".join(c["what"] for c in app) if app else "",
                             "app crashed or ANR" if crash else "", given), "app"
    setup = [c for c in unrun if c.get("setup")]
    if setup:
        return "blocked", _join(*(f"precondition not met: {c['what']}" for c in setup), given, told), "precondition"
    if unrun:
        device = any(_device_lost(c) for c in unrun)
        lead = "the device was not reachable" if device else "a step could not run"
        return "blocked", _join(f"{lead}: " + "; ".join(_step_error(c) for c in unrun), given, told,
                                f"not verified: {unverified}" if unverified else ""), "device" if device else "step"
    if unverified:
        return "blocked", _join(told, given, f"not verified: {unverified}"), "unverified"
    if not any(not c.get("setup") for c in checks):  # a `require` proves nothing about the app
        return "blocked", told or reason or "no checks were recorded", "unverified"
    return "pass", reason or "", None


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _app_evidence(check: dict) -> bool:
    if check.get("setup"):
        return False
    step = check.get("step")
    if step:
        return step.get("code") == EXIT_INSTEAD
    if check.get("blank_screen"):
        return False
    # A check written by `check` has a selector, or a `detail` when the tester gave the result.
    return not ("selector" not in check and "detail" not in check and check.get("kind", "ui") == "ui"
                and STEP_LINE.match(check["what"]))


def _device_lost(check: dict) -> bool:
    # Only an error from adb itself (exit 2) blames the device: "not found: \"Go offline\"" is the step's.
    step = check.get("step") or {}
    return step.get("code") == EXIT_ERROR and bool(DEVICE_ERROR.search(step.get("error", "")))


def _step_error(check: dict) -> str:
    if check.get("blank_screen") and not check.get("step"):
        return f"the screen could not be read for {check['what']}"
    return (check.get("step") or {}).get("error") or check["what"]
