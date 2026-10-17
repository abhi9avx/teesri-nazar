"""Plan files: a whole test written once and run by `tnazar run`, with no model turn per step.

A plan is the tnazar commands a tester would type, without the `./tnazar` prefix:

    app com.android.settings          # the app under test (required)
    name settings-smoke               # run name (default: the file name)

    flow open_network                 # a reusable block, used with `run open_network`
      tap "Network & internet" --wait 5
    end

    case TC-01 "Network page opens"   # a case runs until the next `case`, `flow` or the end
      launch --restart
      run open_network
      check "Internet"
      retry 2                         # repeat until every step in it passes
        ...
      end
      if "Search Settings"            # one screen read; never fails the case
        ...
      else
        ...
      end

Lines starting with `#` are comments. Indentation is for people; blocks close with `end`.
The whole file is checked before anything runs, so a typo never reaches the device.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

STEPS = {"launch", "open-url", "observe", "tap", "type", "key", "scroll", "swipe", "wait", "check",
         "shot", "note", "say", "adb", "wait-online"}
NOT_IN_PLANS = {"start", "stop", "status", "case", "report", "runs", "replay", "ui", "run", "devices",
                "packages", "wait-resume"}


class PlanError(ValueError):
    pass


@dataclass
class Step:
    line: int
    argv: list[str]


@dataclass
class RunFlow:
    line: int
    flow: str


@dataclass
class Retry:
    line: int
    times: int
    body: list = field(default_factory=list)


@dataclass
class If:
    line: int
    target: str
    then: list = field(default_factory=list)
    other: list = field(default_factory=list)
    alts: list = field(default_factory=list)  # --or: screens that may show up instead
    wait: float = 0.0                          # --wait: seconds to wait for target or an --or


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


@dataclass
class Plan:
    app: str
    name: str
    flows: dict[str, Flow]
    cases: list[Case]


def load(path: str | Path) -> Plan:
    p = Path(path)
    return parse(p.read_text(), str(p))


def parse(text: str, source: str = "plan") -> Plan:
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
            stack[-1] = opened[-1][1].other
        elif top is None:
            fail(n, "steps belong inside a `case` or a `flow`")
        elif head == "retry":
            if len(args) != 1 or not args[0].isdigit() or int(args[0]) < 1:
                fail(n, "use `retry <times>`, e.g. `retry 2`")
            block = Retry(n, int(args[0]))
            stack[-1].append(block)
            opened.append((n, block))
            stack.append(block.body)
        elif head == "if":
            opts = _if_options(args)
            if opts is None:
                fail(n, 'use `if <target> [--or <target>]... [--wait <seconds>]`, e.g. `if "Get started" --wait 10`')
            block = If(n, args[0], alts=opts.alt or [], wait=opts.wait)
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
    if not cases:
        raise PlanError(f"{source}: the plan has no `case`")
    for c in cases:
        if not c.body:
            fail(c.line, f"case {c.id} has no steps")
    for owner in [*flows.values(), *cases]:
        _check_flows(owner.body, flows, [getattr(owner, "name", None)], source)
    for c in cases:
        if not _has_check(c.body, flows):
            fail(c.line, f"case {c.id} has no `check`: a case that verifies nothing ends as blocked")
    return Plan(app, name or Path(source).stem, flows, cases)


def _if_options(args: list[str]):
    import argparse
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


def _check_args(words: list[str], fail) -> None:
    """Let the CLI's own parser reject bad flags now, not halfway through a run."""
    import argparse
    import contextlib
    import io

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
