"""Plan files: a whole test written once and run by `tnazar run`, with no model turn per step.

A plan is the tnazar commands a tester would type, without the `./tnazar` prefix:

    app com.android.settings          # the app under test (required)
    name settings-smoke               # run name (default: the file name)

    flow open_network                 # a reusable block, used with `run open_network`
      tap "Network & internet" --wait 5
    end

    case TC-01 "Network page opens"   # a case runs until the next `case`, `flow` or the end
      expect "Internet settings show" # optional: expected results E1, E2… before the steps
      launch --restart
      run open_network
      check "Internet" --for E1       # proves E1; every `expect` needs a `check --for` in its case
      retry 2                         # repeat until every step in it passes
        ...
      end
      if "Search Settings"            # one screen read; never fails the case
        ...
      else
        ...
      end

Lines starting with `#` are comments (a line, never the end of a step). Indentation is for
people; blocks close with `end`. An expected result whose linked check did not run (an `if`
branch not taken) ends its case blocked, naming it; flows cannot link expected results.
`tnazar run` records the selected cases first, so a case that never starts (an error,
`--stop-on-fail`) is still in the report, as not run.
The whole file is checked before anything runs, so a typo never reaches the device.

`wait A --or B` is a step like any other: when B shows up instead of A it fails (exit 4),
which fails the case. To branch on whichever screen shows first, use
`if A --or B --wait <seconds>`. `if` targets are selectors ("Text", ~partial, id=, desc=),
never `#index` or `x,y`: there is no observed screen to index and no element to look for.

`tnazar run` exits 0 when every case passed, 1 when a case failed, 2 on a plan or setup
error (or an unexpected error mid-run, which ends the case as blocked and stops the run),
and 3 when a dashboard message handed the run back (it stays open; continue with --from).

The app map: `tnazar flow <name>` runs one flow against the active run, from any
`plans/*.plan` whose `app` is the run's app. A map file may hold flows and no case.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .commands import NOT_IN_PLANS, PLAN_STEPS as STEPS
from .errors import UserError
from .targets import selector_of


class PlanError(ValueError):
    pass


@dataclass
class Step:
    line: int
    argv: list[str]

    def summary(self) -> str:
        head, *rest = self.argv
        arg = next((a for a in rest if not a.startswith("-")), "")
        return f'{head} "{arg}"' if " " in arg else f"{head} {arg}".rstrip()


@dataclass
class RunFlow:
    line: int
    flow: str

    def summary(self) -> str:
        return f"run {self.flow}"


@dataclass
class Retry:
    line: int
    times: int
    body: list = field(default_factory=list)

    def summary(self) -> str:
        return f"retry {self.times} [{' → '.join(i.summary() for i in self.body)}]"


@dataclass
class If:
    line: int
    target: str
    then: list = field(default_factory=list)
    other: list = field(default_factory=list)
    alts: list = field(default_factory=list)  # --or: screens that may show up instead
    wait: float = 0.0                          # --wait: seconds to wait for target or an --or

    def summary(self) -> str:
        return f'if "{self.target}"'


@dataclass
class Flow:
    line: int
    name: str
    body: list = field(default_factory=list)


@dataclass
class Case:
    line: int
    id: str
    title: str
    body: list = field(default_factory=list)
    expects: list = field(default_factory=list)       # expected results E1, E2… in order
    expect_lines: list = field(default_factory=list)  # the plan line of each, for errors


@dataclass
class Plan:
    app: str
    name: str
    flows: dict[str, Flow]
    cases: list[Case]
    gaps: list = field(default_factory=list)  # draft only: what still keeps it from running


def load(path: str | Path, flows_only: bool = False, draft: bool = False) -> Plan:
    p = Path(path)
    return parse(p.read_text(), str(p), flows_only, draft)


def parse(text: str, source: str = "plan", flows_only: bool = False, draft: bool = False) -> Plan:
    """flows_only: an app map (`tnazar flow`) may hold flows and no case.

    draft: a plan still being written (`plan review`, `cases import`): every completeness gap (no
    steps, no check, an unproven `expect`, a wrong `--for`) is listed in `Plan.gaps` instead of
    stopping at the first. Syntax errors still stop at once.
    """
    app = name = ""
    flows: dict[str, Flow] = {}
    cases: list[Case] = []
    top: Flow | Case | None = None   # the case or flow being filled
    stack: list[list] = []            # step lists of open blocks; [-1] receives the next step
    opened: list[tuple[int, object]] = []  # open retry/if blocks, for `else`, `end` and errors

    def fail(n: int, msg: str):
        raise PlanError(f"{source} line {n}: {msg}")

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            words = shlex.split(line)
        except ValueError as exc:
            fail(n, str(exc))
        head, args = words[0], words[1:]

        if head in ("app", "name"):
            if top is not None or len(args) != 1:
                fail(n, f"`{head} <value>` belongs at the top of the plan")
            app, name = (args[0], name) if head == "app" else (app, args[0])
        elif head in ("case", "flow"):
            if opened:
                fail(opened[-1][0], "this block has no `end`")
            if not args:
                fail(n, f"`{head}` needs a name")
            if isinstance(top, Flow):
                fail(top.line, f"flow `{top.name}` has no `end`")
            if head == "case":
                if any(c.id == args[0] for c in cases):
                    fail(n, f"case {args[0]} is defined twice")
                top = Case(n, args[0], " ".join(args[1:]))
                cases.append(top)
            else:
                if args[0] in flows:
                    fail(n, f"flow {args[0]} is defined twice")
                top = Flow(n, args[0])
                flows[args[0]] = top
            stack = [top.body]
        elif head == "end":
            if opened:
                opened.pop()
                stack.pop()
            elif isinstance(top, Flow):
                top, stack = None, []
            else:
                fail(n, "`end` without an open block")
        elif head == "else":
            if not opened or not isinstance(opened[-1][1], If):
                fail(n, "`else` without an `if`")
            if stack[-1] is opened[-1][1].other:
                fail(n, f"a second `else` for the `if` on line {opened[-1][0]}")
            stack[-1] = opened[-1][1].other
        elif head == "expect":
            if not isinstance(top, Case) or opened or top.body:
                fail(n, "`expect` belongs directly inside a `case`, before its first step")
            if len(args) != 1 or not args[0].strip():
                fail(n, 'use `expect "<one expected result>"`')
            top.expects.append(args[0])
            top.expect_lines.append(n)
        elif top is None:
            fail(n, "steps belong inside a `case` or a `flow`")
        elif head == "retry":
            times = _count(args)
            if times is None:
                fail(n, "use `retry <times>`, e.g. `retry 2`")
            block = Retry(n, times)
            stack[-1].append(block)
            opened.append((n, block))
            stack.append(block.body)
        elif head == "if":
            opts = _if_options(args)
            if opts is None:
                fail(n, 'use `if <target> [--or <target>]... [--wait <seconds>]`, e.g. `if "Get started" --wait 10`')
            for t in [opts.target, *(opts.alt or [])]:
                if not _is_selector(t):
                    fail(n, f"`if` needs a selector (\"Text\", ~partial, id=..., desc=...), not {t!r}")
            block = If(n, opts.target, alts=opts.alt or [], wait=opts.wait)
            stack[-1].append(block)
            opened.append((n, block))
            stack.append(block.then)
        elif head == "run":
            if len(args) != 1:
                fail(n, "use `run <flow>`")
            stack[-1].append(RunFlow(n, args[0]))
        elif head in NOT_IN_PLANS:
            fail(n, f"`{head}` is not allowed in a plan: `tnazar run` manages the run and its cases")
        elif head not in STEPS:
            fail(n, f"unknown command `{head}`")
        else:
            if head == "check" and any(isinstance(b, Retry) for _, b in opened):
                fail(n, "`check` inside `retry` would record the failed attempts: "
                        "use `wait` in the block and put the `check` after its `end`")
            _check_args(words, lambda msg: fail(n, msg))
            stack[-1].append(Step(n, words))

    if opened:
        fail(opened[-1][0], "this block has no `end`")
    if isinstance(top, Flow):
        fail(top.line, f"flow `{top.name}` has no `end`")
    if not app:
        raise PlanError(f"{source}: the plan needs `app <package>` at the top")
    if not cases and not flows_only:
        raise PlanError(f"{source}: the plan has no `case`")
    gaps: list[str] = []

    def gap(n: int, msg: str) -> None:
        if not draft:
            fail(n, msg)
        gaps.append(f"{source} line {n}: {msg}")

    for c in cases:
        if not c.body:
            gap(c.line, f"case {c.id} has no steps")
    for owner in [*flows.values(), *cases]:
        _check_flows(owner.body, flows, [getattr(owner, "name", None)], source)
    for c in cases:
        if c.body and not _has_check(c.body, flows):
            gap(c.line, f"case {c.id} has no `check`: a case that verifies nothing ends as blocked")
    for f in flows.values():
        for step in _checks(f.body):
            if _for_ids(step.argv):
                gap(step.line, f"flow `{f.name}`: a flow's checks are shared by cases; "
                               "link expected results with `check --for` in the case itself")
    for c in cases:
        linked: set[str] = set()
        for step in _checks(c.body):
            for eid in _for_ids(step.argv):
                if int(eid[1:]) > len(c.expects):
                    gap(step.line, f"case {c.id} has no {eid}: it lists {len(c.expects)} `expect` line(s)")
                linked.add(eid)
        for i, (text, line) in enumerate(zip(c.expects, c.expect_lines), 1):
            if f"E{i}" not in linked:
                gap(line, f"case {c.id}: E{i} \"{text}\" has no `check ... --for E{i}`")
    return Plan(app, name or Path(source).stem, flows, cases, gaps)


def review(p: Plan) -> tuple[str, bool]:
    """Every case, each expected result and the checks that prove it, then every gap: (text, ready)."""
    out = []
    for c in p.cases:
        out.append(f"{c.id}  {c.title}".rstrip())
        proof: dict[str, list[str]] = {}
        for step in _checks(c.body):
            for eid in _for_ids(step.argv):
                proof.setdefault(eid, []).append(f"line {step.line}: {step.summary()}")
        for i, text in enumerate(c.expects, 1):
            by = proof.get(f"E{i}")
            out.append(f"  E{i} {text}  <- " + ("; ".join(by) if by else "NOT PROVEN"))
        if not c.expects:
            checks = sum(1 for _ in _checks(c.body))
            out.append(f"  (no expected results listed; {checks} check(s))")
    out.append("")
    if p.gaps:
        out += [f"{len(p.gaps)} gap(s): not ready to run"] + [f"  {g}" for g in p.gaps]
    else:
        n = sum(len(c.expects) for c in p.cases)
        out.append(f"ready to run: {len(p.cases)} case(s), {n} expected result(s), every one proven")
    return "\n".join(out), not p.gaps


def _count(args: list[str]) -> int | None:
    # isdigit() passes "²", which int() rejects; isdecimal() plus the try keep any odd digit a PlanError.
    if len(args) != 1 or not args[0].isdecimal():
        return None
    try:
        times = int(args[0])
    except ValueError:
        return None
    return times if times >= 1 else None


def _is_selector(target: str) -> bool:
    try:
        selector_of(target)
    except UserError:
        return False
    return True


def _if_options(args: list[str]):
    p = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    p.add_argument("target")
    p.add_argument("--or", dest="alt", action="append")
    p.add_argument("--wait", type=float, default=0.0)
    try:
        opts, rest = p.parse_known_args(args)
    except (argparse.ArgumentError, SystemExit, ValueError):
        return None
    return None if rest else opts


def _has_check(body: list, flows: dict[str, Flow], seen: frozenset = frozenset()) -> bool:
    for item in body:
        if isinstance(item, Step) and item.argv[0] == "check":
            return True
        if isinstance(item, RunFlow) and item.flow in flows and item.flow not in seen:
            if _has_check(flows[item.flow].body, flows, seen | {item.flow}):
                return True
        if isinstance(item, If) and (_has_check(item.then, flows, seen) or _has_check(item.other, flows, seen)):
            return True
    return False


def _checks(body: list):
    """The `check` steps written in this body, inside `if`/`else` too (a `retry` holds none)."""
    for item in body:
        if isinstance(item, Step) and item.argv[0] == "check":
            yield item
        elif isinstance(item, If):
            yield from _checks(item.then)
            yield from _checks(item.other)
        elif isinstance(item, Retry):
            yield from _checks(item.body)


def _for_ids(argv: list[str]) -> list[str]:
    """The expected results a `check` step links, read by the CLI's own parser (already validated)."""
    from .cli import build_parser
    return build_parser().parse_args(argv).for_ or []


def _check_args(words: list[str], fail) -> None:
    """Let the CLI's own parser reject bad flags now, not halfway through a run."""
    from .cli import build_parser
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            build_parser().parse_args(words)
    except SystemExit:
        fail(err.getvalue().strip().splitlines()[-1].replace("tnazar: error: ", ""))
    except argparse.ArgumentError as exc:
        fail(str(exc))


def _check_flows(body: list, flows: dict[str, Flow], path: list, source: str, in_retry: bool = False) -> None:
    for item in body:
        if isinstance(item, RunFlow):
            if item.flow not in flows:
                raise PlanError(f"{source} line {item.line}: no flow named `{item.flow}`")
            if item.flow in path:
                raise PlanError(f"{source} line {item.line}: flows call each other in a loop "
                                f"({' -> '.join(p for p in path if p)} -> {item.flow})")
            if in_retry and _has_check(flows[item.flow].body, flows):
                raise PlanError(f"{source} line {item.line}: flow `{item.flow}` has a `check`, so it cannot run "
                                f"inside `retry` (failed attempts would be recorded): use `wait` in the flow")
            _check_flows(flows[item.flow].body, flows, path + [item.flow], source, in_retry)
        elif isinstance(item, Retry):
            _check_flows(item.body, flows, path, source, in_retry=True)
        elif isinstance(item, If):
            _check_flows(item.then, flows, path, source, in_retry)
            _check_flows(item.other, flows, path, source, in_retry)
