"""The app map: saved routes the agent runs in one command during a live run.

A route is a plan `flow` (format: plan.py). `tnazar flow <name>` finds it in `plans/*.plan`
(only files whose `app` is the active run's app; a map file may hold flows and no case),
runs it against the active run through the normal CLI, so every step is recorded and shown
in the dashboard timeline, and leaves the run open. `tnazar flows` lists the routes.

Unlike `tnazar run`, a failed step does not fail the current case: the agent reads the exit
code and decides. A `check` in the flow is recorded in the current case as usual.

Exit codes: 0 done; 1 a step or check failed; 4 a `wait --or` saw the other screen;
2 no such flow, no active run, a bad map file; 3 a dashboard message stopped the flow;
5 the user paused the run mid-flow.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from . import cli, live
from .errors import UserError
from .plan import Case, Plan, PlanError, load
from .runner import Executor, Failed, HandedBack, Paused, call, indent, tail
from .session import get_active, home

SUMMARY_CHARS = 90


def route_files(app: str | None) -> tuple[list[tuple[Path, Plan]], list[str]]:
    """Every plans/*.plan for `app` (any app when None), and the errors of files that did not parse."""
    found, broken = [], []
    for f in sorted((home() / "plans").glob("*.plan")):
        try:
            p = load(f, flows_only=True)
        except (PlanError, OSError, UnicodeDecodeError) as exc:
            broken.append(str(exc))
            continue
        if app is None or p.app == app:
            found.append((f, p))
    return found, broken


def _rel(f: Path) -> str:
    return os.path.relpath(f, home())


def _app_of(active) -> str | None:
    return (active[0].meta().get("package") or None) if active else None


def _label(f: Path, args) -> str:
    return args.plan or _rel(f)


class FlowExecutor(Executor):
    """A flow gives a long pause back to the agent, like any device command (exit 5)."""

    def long_pause(self, out: str) -> Paused:
        return Paused(out)


def _sources(args, app: str | None) -> list[tuple[Path, Plan]]:
    if args.plan:
        return [(Path(args.plan), load(args.plan, flows_only=True))]
    found, broken = route_files(app)
    for b in broken:
        print(f"warning: skipped {b}", file=sys.stderr)
    return found


def run_flow(args) -> int:
    active = get_active()
    if not active:
        raise UserError("no active run: `./tnazar start <name> --package <app>` first")
    the_run = active[0]
    app = _app_of(active)
    sources = _sources(args, app)
    hits = [(f, p) for f, p in sources if args.name in p.flows]
    if not hits:
        names = sorted({n for _, p in sources for n in p.flows})
        where = args.plan or f"plans/*.plan{f' for {app}' if app else ''}"
        raise UserError(f"no flow `{args.name}` in {where}"
                        + (f"; there are: {', '.join(names)}" if names else "")
                        + ". Find the route by hand, then save it as a flow")
    if len(hits) > 1:
        raise UserError(f"flow `{args.name}` is in {', '.join(_rel(f) for f, _ in hits)}: pick one with --plan")
    f, p = hits[0]
    source = _label(f, args)

    began = time.monotonic()
    call(["say", f"Going to {args.name} (saved route in {source})"])
    # Not a plan case: the flow's checks go to whatever case the agent has open.
    open_case = Case(line=0, id=the_run.current_case() or "", title="")
    ex = FlowExecutor(p, the_run, open_case, live.Since())
    result = ex.body(p.flows[args.name].body, record=False)
    took = time.monotonic() - began
    if isinstance(result, HandedBack):
        print(tail(result.output))
        print(f"flow {args.name}: stopped by your message before it finished. "
              "Act on the message; run the flow again only if it still applies.")
        return live.EXIT_HELD
    if isinstance(result, Paused):
        print(f"PAUSED: the user paused this run in the middle of flow {args.name} (it did not finish). "
              f"{live.PAUSED_HINT} `observe` first: the flow stopped part-way.")
        return live.EXIT_PAUSED
    if isinstance(result, Failed):
        print(f"flow {args.name}: FAILED after {took:.1f}s ({source})")
        print(indent(result.detail))
        return result.code
    if ex.failed_checks:
        print(f"flow {args.name}: done in {took:.1f}s, {ex.failed_checks} check(s) failed")
        return cli.EXIT_FAIL
    print(f"flow {args.name}: done in {took:.1f}s")
    return 0


def list_flows(args) -> int:
    app = _app_of(get_active())
    found = _sources(args, app)
    if not any(p.flows for _, p in found):
        print(f"no saved routes{f' for {app}' if app else ''} yet. Save each route you find by hand as a "
              f"`flow` in plans/<app>.plan (format: teesri_nazar/plan.py); `./tnazar flow <name>` runs it.")
        return 0
    for f, p in found:
        if not p.flows:
            continue
        print(f"{_label(f, args)}  ({p.app})")
        for name, fl in p.flows.items():
            print(f"  {name:24} {summary(fl.body)}")
    return 0


def summary(body: list) -> str:
    """The route at a glance: `tap Login → wait "Pay now"`."""
    text = " → ".join(item.summary() for item in body)
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS - 1] + "…"
