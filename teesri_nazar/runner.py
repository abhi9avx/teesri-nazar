"""`tnazar run <plan>`: execute every case of a plan file, with no model turn per step.

Each step goes through the normal CLI in this process, so it is recorded, timed, held by a
dashboard pause and stopped by a dashboard message exactly as when an agent types it. Only
its output is captured, and shown when the step fails.

- A failed `check` is recorded and the case goes on.
- A failed action (a tap that finds nothing, a wait that times out) ends its case; the run
  goes on with the next case (or stops, with --stop-on-fail).
- A dashboard message ends the run early and leaves it open, so the agent can act on the
  message and continue with `tnazar run <plan> --from <case>` (exit 3).
- An unexpected error (device gone, a bad target) ends the case as blocked and stops the
  run, so no run is left open (exit 2).
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from dataclasses import dataclass, field

from . import cli, coverage, live, report
from . import plan as plan_mod
from . import device as dev
from .judge import blocked_end
from .plan import Case, If, Plan, PlanError, Retry, RunFlow, Step, load
from .session import Run, claim_active, get_active
from .targets import selector_of

DETAIL_LINES = 6


@dataclass
class Failed:
    detail: str
    code: int = 1  # the failing step's exit code
    flows: list = field(default_factory=list)  # every flow the failing step was inside, innermost first
    step: dict = field(default_factory=dict)  # the step that could not run: {cmd, code, error}, for the verdict


@dataclass
class Paused:
    """The user kept the run paused: a flow gives the turn back (exit 5)."""


@dataclass
class HandedBack:
    output: str


def call(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = cli.main(argv)
    return code, out.getvalue()


def run(args) -> int:
    try:
        p = load(args.plan)
        cases = _select(p.cases, args.only, args.from_)
    except (PlanError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return cli.EXIT_ERROR
    path = os.path.realpath(args.plan)
    active = get_active()
    if active and active[0].meta().get("plan") == path:
        try:
            cli.refuse_if_owned(active[0])
        except cli.UserError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return cli.EXIT_ERROR
        claim_active()
        sel = active[0].meta().get("selection") or {}
        if sel.get("how") and (args.only or args.from_):  # a selection's continuation: name both
            more = f"--only {args.only}" if args.only else f"--from {args.from_}"
            active[0].update_meta(selection={**sel, "how": f"{sel['how']}, then {more}"})
    else:
        device = ["--device", args.device] if getattr(args, "device", None) else []
        code, out = call([*device, "start", p.name, "--package", p.app, "--prompt", f"plan {os.path.basename(path)}"])
        if code:
            why = "; ".join(ln.strip().removeprefix("error: ") for ln in out.splitlines() if ln.strip())
            print(f"setup error: {why or 'tnazar start failed'}", file=sys.stderr)
            return cli.EXIT_ERROR
        how = f"--only {args.only}" if args.only else f"--from {args.from_}" if args.from_ else ""
        get_active()[0].update_meta(plan=path, selection={"plan_cases": len(p.cases), "how": how})
    the_run = get_active()[0]
    for case in cases:  # requested before any runs, so a case that never starts still has a row
        the_run.log(coverage.case_plan(case.id, case.title, case.expects))
    if p.requirements:
        _log_scope(the_run, p, cases)
    print(f"plan {p.name}: {len(cases)} case(s) on {p.app}")
    if p.requirements:
        _warn_scope(p)

    failed = False
    since = live.Since()
    broken: dict[str, list[str]] = {}  # flow -> the cases it failed in, in a row
    unmet: set[str] = set()  # flows whose last failure was a `require`, theirs or a flow's they ran
    for case in cases:
        began = time.monotonic()
        needs = _flows_used(case.body, p.flows)
        if dead := [f for f in needs if len(broken.get(f, [])) >= 2]:
            why = "; ".join(f"flow {f} (failed in {', '.join(broken[f])})" for f in dead)
            _block(the_run, case, f"precondition failed: {why}",
                   "precondition" if all(f in unmet for f in dead) else "step")
            print(f"{case.id:8} BLOCKED  precondition failed: {why}")
            failed = True
            continue
        # "--": a title (or id) starting with "-" is not an option.
        call(["case", "start", "--", case.id] + ([case.title] if case.title else []))
        try:
            executor = Executor(p, the_run, case.id, since)
            result = executor.body(case.body)
        except Exception as exc:  # noqa: BLE001 - whatever broke, the run must not stay open
            call(["case", "end", "blocked", "--reason", f"error: {exc}"])
            print(f"{case.id:8} BLOCKED  error: {exc}")
            the_run.update_meta(stop_reason=f"run stopped at {case.id}: error: {exc}")
            _stop()
            return cli.EXIT_ERROR
        if isinstance(result, HandedBack):
            call(["case", "end", "blocked", "--reason", "stopped: a message from the dashboard"])
            print(tail(result.output))
            print(f"{case.id}: stopped by your message. The run is still open; act on the message, then continue "
                  f"with: ./tnazar run {args.plan} --from {case.id}")
            return live.EXIT_HELD
        call(["case", "end"])
        status = _last_status(the_run, case.id)
        for f in needs:
            if isinstance(result, Failed) and f in result.flows:
                broken.setdefault(f, []).append(case.id)
                if result.step.get("cmd") == "require":
                    unmet.add(f)
                else:
                    unmet.discard(f)
            elif f in executor.completed:  # it worked here: not a broken precondition any more
                broken.pop(f, None)
        print(f"{case.id:8} {status.upper():7} {time.monotonic() - began:5.1f}s  {case.title}")
        if isinstance(result, Failed):
            print(indent(result.detail))
        if status != "pass":
            failed = True
            if args.stop_on_fail:
                print("stopping at the first failed case (--stop-on-fail)")
                the_run.update_meta(stop_reason=f"--stop-on-fail after {case.id}")
                break
    _stop()
    if p.requirements:
        reqs = report.summarize(the_run)["requirements"]
        print(f"requirements: {reqs['line']}")
        failed = failed or not reqs["ok"]  # an incomplete feature never exits 0
    return cli.EXIT_FAIL if failed else 0


def _log_scope(the_run: Run, p, cases: list[Case]) -> None:
    the_run.log(coverage.scope(
        p.source, plan_mod.fingerprint(p), p.approved,
        [{"id": r.id, "text": r.text, "source": r.source, "out_of_scope": r.out_of_scope} for r in p.requirements],
        [{"id": c.id, "expects": [{"text": t, "reqs": q} for t, q in zip(c.expects, c.expect_reqs)]} for c in p.cases],
        [c.id for c in cases]))


def _warn_scope(p) -> None:
    proof = plan_mod.links(p)
    bare = [r.id for r in p.requirements if not r.out_of_scope and not proof[r.id]]
    if bare:
        print(f"{len(bare)} requirement{'s have' if len(bare) > 1 else ' has'} no expected result: "
              f"{', '.join(bare)} (the run cannot verify the feature)")
    for gap in plan_mod.requirement_gaps(p):
        if "has no expected result" not in gap:
            print(gap)


def _stop() -> None:
    _, out = call(["stop"])
    print("\n".join(ln for ln in out.splitlines() if ln.startswith(("report:", " ")) or "case(s)" in ln))


class Executor:
    """Runs plan items through the CLI; `tnazar run` waits out a pause as long as the user keeps it."""

    def __init__(self, plan: Plan, the_run: Run, case_id: str, since: live.Since):
        self.plan, self.run, self.case_id, self.since = plan, the_run, case_id, since
        self.failed_checks = 0
        self.completed: set[str] = set()  # flows that ran to their end in this case

    def long_pause(self) -> Paused | None:
        """The user kept the run paused past PAUSE_WAIT: keep waiting (None) or give the turn back."""
        while not live.wait_resume():
            pass  # no model timeout here: wait as long as the user keeps it paused
        return None

    def body(self, items: list, record: bool = True):
        for item in items:
            result = self.item(item, record)
            if result is not None:
                return result
        return None

    def item(self, item, record: bool):
        if isinstance(item, RunFlow):
            result = self.body(self.plan.flows[item.flow].body, record)
            if isinstance(result, Failed):
                result.flows.append(item.flow)
            elif result is None:
                self.completed.add(item.flow)
            return result
        if isinstance(item, If):
            held = self.hold()
            if held:
                return held
            found, reads = self.visible(item.target, item.alts, item.wait)
            self.run.log({"type": "if", "target": item.target, "found": found, "reads": reads, "case": self.case_id})
            return self.body(item.then if found else item.other, record)
        if isinstance(item, Retry):
            instead = None  # an attempt that met the error screen the plan named: evidence about the app
            for attempt in range(1, item.times + 1):
                result = self.body(item.body, record=False)
                if not isinstance(result, Failed):
                    return result
                if result.code == cli.EXIT_INSTEAD and instead is None:
                    instead = result
            result = instead or result
            return self.fail(item.line, f"retry {item.times}",
                             f"still failing after {item.times} attempts: {result.detail}", record, result.code,
                             result.step)
        return self.step(item, record)

    def hold(self) -> HandedBack | Paused | None:
        """What the CLI does before a device command, for the screen read an `if` makes itself."""
        if live.paused() and not live.wait_resume() and not live.needs_agent(live.unread()):
            paused = self.long_pause()
            if paused:
                return paused
        if live.needs_agent(live.unread()):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                live.deliver(self.run.log)
            self.since.take()
            return HandedBack(out.getvalue())
        return self.already_told()

    def already_told(self) -> HandedBack | None:
        """A message the background `wait-message` delivered: the agent is acting on it, so stop."""
        told = self.since.take()
        if not live.needs_agent(told):
            return None
        return HandedBack("".join(f">> USER (dashboard): {m['text']}\n" for m in told))

    def step(self, step: Step, record: bool):
        told = self.already_told()
        if told:
            return told
        while True:
            code, out = call(step.argv)
            if code == live.EXIT_HELD:
                self.since.take()
                return HandedBack(out)
            if code != live.EXIT_PAUSED:
                break
            paused = self.long_pause()
            if paused:
                return paused
        if step.argv[0] == "check" and code == cli.EXIT_FAIL:
            if "--setup" in step.argv:  # recorded by `check`; the setup did not hold, so the case stops
                return Failed(f"line {step.line}: require {' '.join(_quote(a) for a in step.argv[1:-1])}\n{tail(out)}",
                              code, step={"cmd": "require", "code": code, "error": error_line(tail(out))})
            self.failed_checks += 1
            return None  # a failed check is already recorded; the case goes on
        if code == 0:
            return None
        detail = tail(out)
        return self.fail(step.line, " ".join(_quote(a) for a in step.argv), detail, record, code,
                         {"cmd": step.argv[0], "code": code, "error": error_line(detail)})

    def fail(self, line: int, what: str, detail: str, record: bool, code: int = cli.EXIT_FAIL,
             step: dict | None = None) -> Failed:
        step = step or {"cmd": what.split()[0], "code": code, "error": error_line(detail)}
        if record:
            self.run.log({"type": "check", "kind": "ui", "ok": False, "case": self.case_id,
                          "what": f"line {line}: {what}: {step['error']}", "step": step})
        return Failed(f"line {line}: {what}\n{detail}", code, step=step)

    def visible(self, target: str, alts: list[str], wait: float) -> tuple[bool, int]:
        """Is `target` on screen? With --wait, read until it or one of `alts` shows (or time runs out)."""
        adb = cli.Ctx.from_args(None).adb
        sel, *others = [selector_of(t) for t in [target, *alts]]
        stats: dict = {}
        found, _, _ = dev.wait_for(adb, sel, wait, stats=stats, alternatives=others)
        return found, stats["polls"]


def _flows_used(body: list, flows: dict, seen: frozenset = frozenset()) -> list[str]:
    """The flows a case always runs, and those they run. A flow in an `if` branch may not be
    needed (the screen decides), so it never blocks the case."""
    out: list[str] = []
    for item in body:
        if isinstance(item, RunFlow) and item.flow not in seen:
            out += [item.flow] + _flows_used(flows[item.flow].body, flows, seen | {item.flow})
        elif isinstance(item, Retry):
            out += _flows_used(item.body, flows, seen)
    return list(dict.fromkeys(out))


def _block(the_run: Run, case: Case, reason: str, cause: str = "step") -> None:
    """End a case unrun: no device call, so a dead precondition costs nothing per case."""
    events = the_run.events()
    the_run.log({"type": "case_start", "case": case.id, "title": case.title})
    the_run.log(blocked_end(events, case.id, reason, [], cause))


def _select(cases: list[Case], only: str | None, start: str | None) -> list[Case]:
    ids = [c.id for c in cases]
    if only:
        wanted = [x.strip() for x in only.split(",") if x.strip()]
        unknown = [x for x in wanted if x not in ids]
        if unknown:
            raise PlanError(f"no case {', '.join(unknown)} in the plan")
        return [c for c in cases if c.id in wanted]
    if start:
        if start not in ids:
            raise PlanError(f"no case {start} in the plan")
        return cases[ids.index(start):]
    return cases


def _last_status(the_run: Run, case_id: str) -> str:
    ends = [e for e in the_run.events() if e["type"] == "case_end" and e["case"] == case_id]
    return ends[-1]["status"] if ends else "blocked"


def error_line(detail: str) -> str:
    """Why a step failed, in its own words: a SLOW note or a dashboard line is never the reason."""
    lines = [ln.strip() for ln in detail.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("SLOW:", ">> USER"))]
    return next((ln.removeprefix("error: ") for ln in lines if ln.startswith("error: ")), lines[0] if lines else "failed")


def tail(text: str) -> str:
    lines = [ln for ln in text.rstrip().splitlines() if ln.strip()]
    return "\n".join(lines[-DETAIL_LINES:])


def indent(text: str) -> str:
    return "\n".join("    " + ln for ln in text.splitlines())


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg or not arg else arg
