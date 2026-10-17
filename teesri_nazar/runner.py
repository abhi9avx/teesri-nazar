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
from dataclasses import dataclass

from . import cli, live
from . import device as dev
from .plan import Case, If, Plan, PlanError, Retry, RunFlow, Step, load
from .session import Run, get_active
from .targets import selector_of

DETAIL_LINES = 6


@dataclass
class Failed:
    detail: str


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
    if not (active and active[0].meta().get("plan") == path):
        code, out = call(["start", p.name, "--package", p.app, "--prompt", f"plan {os.path.basename(path)}"])
        if code:
            why = "; ".join(ln.strip().removeprefix("error: ") for ln in out.splitlines() if ln.strip())
            print(f"setup error: {why or 'tnazar start failed'}", file=sys.stderr)
            return cli.EXIT_ERROR
        get_active()[0].update_meta(plan=path)
    the_run = get_active()[0]
    print(f"plan {p.name}: {len(cases)} case(s) on {p.app}")

    failed = False
    for case in cases:
        began = time.monotonic()
        # "--": a title (or id) starting with "-" is not an option.
        call(["case", "start", "--", case.id] + ([case.title] if case.title else []))
        try:
            result = Executor(p, the_run, case).body(case.body)
        except Exception as exc:  # noqa: BLE001 - whatever broke, the run must not stay open
            call(["case", "end", "blocked", "--reason", f"error: {exc}"])
            print(f"{case.id:8} BLOCKED  error: {exc}")
            _stop()
            return cli.EXIT_ERROR
        if isinstance(result, HandedBack):
            call(["case", "end", "blocked", "--reason", "stopped: a message from the dashboard"])
            print(_tail(result.output))
            print(f"{case.id}: stopped by your message. The run is still open; act on the message, then continue "
                  f"with: ./tnazar run {args.plan} --from {case.id}")
            return live.EXIT_HELD
        call(["case", "end"])
        status = _last_status(the_run, case.id)
        print(f"{case.id:8} {status.upper():7} {time.monotonic() - began:5.1f}s  {case.title}")
        if isinstance(result, Failed):
            print(_indent(result.detail))
        if status != "pass":
            failed = True
            if args.stop_on_fail:
                print("stopping at the first failed case (--stop-on-fail)")
                break
    _stop()
    return cli.EXIT_FAIL if failed else 0


def _stop() -> None:
    _, out = call(["stop"])
    print("\n".join(ln for ln in out.splitlines() if ln.startswith(("report:", " ")) or "case(s)" in ln))


class Executor:
    def __init__(self, plan: Plan, the_run: Run, case: Case):
        self.plan, self.run, self.case = plan, the_run, case

    def body(self, items: list, record: bool = True):
        for item in items:
            result = self.item(item, record)
            if result is not None:
                return result
        return None

    def item(self, item, record: bool):
        if isinstance(item, RunFlow):
            return self.body(self.plan.flows[item.flow].body, record)
        if isinstance(item, If):
            held = self.hold()
            if held:
                return held
            found, reads = self.visible(item.target, item.alts, item.wait)
            self.run.log({"type": "if", "target": item.target, "found": found, "reads": reads, "case": self.case.id})
            return self.body(item.then if found else item.other, record)
        if isinstance(item, Retry):
            for attempt in range(1, item.times + 1):
                result = self.body(item.body, record=False)
                if not isinstance(result, Failed):
                    return result
            return self.fail(item.line, f"retry {item.times}",
                             f"still failing after {item.times} attempts: {result.detail}", record)
        return self.step(item, record)

    def hold(self) -> HandedBack | None:
        """What the CLI does before a device command, for the screen read an `if` makes itself."""
        while live.paused():
            live.wait_resume()
        if live.unread():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                live.deliver(self.run.log)
            return HandedBack(out.getvalue())
        return None

    def step(self, step: Step, record: bool):
        while True:
            code, out = call(step.argv)
            if code == live.EXIT_HELD:
                return HandedBack(out)
            if code != live.EXIT_PAUSED:
                break
            while not live.wait_resume():
                pass  # no model timeout here: wait as long as the user keeps it paused
        if code == 0 or (step.argv[0] == "check" and code == cli.EXIT_FAIL):
            return None  # a failed check is already recorded; the case goes on
        return self.fail(step.line, " ".join(_quote(a) for a in step.argv), _tail(out), record)

    def fail(self, line: int, what: str, detail: str, record: bool) -> Failed:
        first = next((ln for ln in detail.splitlines() if ln.strip()), "failed")
        if record:
            self.run.log({"type": "check", "kind": "ui", "ok": False, "case": self.case.id,
                          "what": f"line {line}: {what}: {first.strip()}"})
        return Failed(f"line {line}: {what}\n{detail}")

    def visible(self, target: str, alts: list[str], wait: float) -> tuple[bool, int]:
        """Is `target` on screen? With --wait, read until it or one of `alts` shows (or time runs out)."""
        adb = cli.Ctx.from_args(None).adb
        sel, *others = [selector_of(t) for t in [target, *alts]]
        stats: dict = {}
        found, _, _ = dev.wait_for(adb, sel, wait, stats=stats, alternatives=others)
        return found, stats["polls"]


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


def _tail(text: str) -> str:
    lines = [ln for ln in text.rstrip().splitlines() if ln.strip()]
    return "\n".join(lines[-DETAIL_LINES:])


def _indent(text: str) -> str:
    return "\n".join("    " + ln for ln in text.splitlines())


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg or not arg else arg
