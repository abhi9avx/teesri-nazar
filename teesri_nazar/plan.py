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
      require "Search Settings"       # setup: if it does not hold, the case is blocked, never failed
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

`require <target>` takes `check`'s options: it is the state the setup must reach (a fresh
chat, an empty cart, a logged-in home). When it does not hold, the case stops there as
`blocked · precondition not met`: the app was never tested, so it never fails. It is never
an expected result (no `--for`) and a case needs a real `check` besides it.

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
import hashlib
import io
import json
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
    expect_reqs: list = field(default_factory=list)   # per expected result: the requirement ids it proves


@dataclass
class Requirement:
    line: int
    id: str
    text: str
    source: str = ""        # where in the spec it comes from (--source)
    out_of_scope: str = ""  # why it is not tested (--out-of-scope); never verified


@dataclass
class Plan:
    app: str
    name: str
    flows: dict[str, Flow]
    cases: list[Case]
    gaps: list = field(default_factory=list)  # draft only: what still keeps it from running
    requirements: list = field(default_factory=list)
    source: str = ""    # the spec the requirements come from
    approved: str = ""  # fingerprint written by `tnazar plan approve`


def load(path: str | Path, flows_only: bool = False, draft: bool = False) -> Plan:
    p = Path(path)
    return parse(p.read_text(), str(p), flows_only, draft)


def parse(text: str, source: str = "plan", flows_only: bool = False, draft: bool = False) -> Plan:
    """flows_only: an app map (`tnazar flow`) may hold flows and no case.

    draft: a plan still being written (`plan review`, `cases import`): every completeness gap (no
    steps, no check, an unproven `expect`, a wrong `--for`) is listed in `Plan.gaps` instead of
    stopping at the first. Syntax errors still stop at once.
    """
    app = name = spec = approved = ""
    requirements: list[Requirement] = []
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
        elif head in ("source", "approved"):
            if top is not None or len(args) != 1:
                fail(n, f"`{head} <value>` belongs at the top of the plan")
            spec, approved = (args[0], approved) if head == "source" else (spec, args[0])
        elif head == "requirement":
            if top is not None:
                fail(n, "`requirement` belongs at the top of the plan, before the first `case` or `flow`")
            opts = _options(args[2:], ("--source", "--out-of-scope"))
            if len(args) < 2 or not args[1].strip() or opts is None:
                fail(n, 'use `requirement <id> "<what the feature must do>" [--source "<where in the spec>"] '
                        '[--out-of-scope "<why not tested>"]`')
            if any(r.id == args[0] for r in requirements):
                fail(n, f"requirement {args[0]} is defined twice")
            requirements.append(Requirement(n, args[0], args[1], opts.get("--source", ""),
                                            opts.get("--out-of-scope", "")))
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
            reqs = _reqs(args[1:])
            if not args or not args[0].strip() or reqs is None:
                fail(n, 'use `expect "<one expected result>" [--req <requirement id>]...`')
            for rid in reqs:
                if all(r.id != rid for r in requirements):
                    fail(n, f"no requirement {rid}: define it with `requirement {rid} \"...\"` at the top")
            top.expects.append(args[0])
            top.expect_lines.append(n)
            top.expect_reqs.append(reqs)
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
        elif head == "require":
            if any(isinstance(b, Retry) for _, b in opened):
                fail(n, "`require` inside `retry` would record the failed attempts: "
                        "use `wait` in the block and put the `require` after its `end`")
            opts = _check_args(["check", *args], lambda msg: fail(n, msg))
            if opts.setup or opts.for_ or opts.result:
                fail(n, "`require` is a setup condition on the screen, not an expected result or a manual "
                        "result: prove expected results with `check ... --for`")
            stack[-1].append(Step(n, ["check", *args, "--setup"]))
        elif head in NOT_IN_PLANS:
            fail(n, f"`{head}` is not allowed in a plan: `tnazar run` manages the run and its cases")
        elif head not in STEPS:
            fail(n, f"unknown command `{head}`")
        else:
            if head == "check" and "--setup" in words:
                fail(n, "write a setup condition as `require <target>`, not `check --setup`")
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
    return Plan(app, name or Path(source).stem, flows, cases, gaps, requirements, spec, approved)


def links(p: Plan) -> dict[str, list[tuple[str, int]]]:
    """Per requirement id: the expected results that prove it, as (case id, E number), in plan order."""
    out: dict[str, list[tuple[str, int]]] = {r.id: [] for r in p.requirements}
    for c in p.cases:
        for i, reqs in enumerate(c.expect_reqs, 1):
            for rid in reqs:
                out[rid].append((c.id, i))
    return out


def fingerprint(p: Plan) -> str:
    """What the user approved: the spec, each requirement, its source and scope, and which expected
    results prove it (by case id and text, so reordering cases or editing steps keeps it)."""
    expect = {c.id: c.expects for c in p.cases}
    data = [p.source, [[r.id, r.text, r.source, r.out_of_scope,
                        sorted([cid, expect[cid][n - 1]] for cid, n in links(p)[r.id])] for r in p.requirements]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:12]


def requirement_gaps(p: Plan) -> list[str]:
    """What keeps the requirements from being a claim: a requirement nothing proves, a scope not
    approved or changed since. `plan review` is not ready while any is left; `run` warns."""
    if not p.requirements:
        return []
    proof = links(p)
    out = [f'{r.id} "{r.text}" has no expected result: link one with --req {r.id}, '
           f'or mark it --out-of-scope "<why>"' for r in p.requirements if not r.out_of_scope and not proof[r.id]]
    now = fingerprint(p)
    if not p.approved:
        out.append(f"requirements not approved: show this review to the user, then `tnazar plan approve` ({now})")
    elif p.approved != now:
        out.append(f"requirements changed since approval (approved {p.approved}, now {now}): "
                   "show the review again, then `tnazar plan approve`")
    return out


def approve(path: str | Path) -> str:
    """Write (or replace) the `approved <fingerprint>` line at the top of the plan; returns the fingerprint."""
    f = Path(path)
    text = f.read_text()
    p = parse(text, str(f), draft=True)
    if not p.requirements:
        raise PlanError(f"{f}: no `requirement` lines, so there is no scope to approve")
    fp = fingerprint(p)
    lines = [ln for ln in text.splitlines(keepends=True) if _head(ln) != "approved"]
    last = max(i for i, ln in enumerate(lines) if _head(ln) == "requirement")
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(last + 1, f"approved {fp}\n")
    f.write_text("".join(lines))
    return fp


def _head(line: str) -> str:
    return line.strip().split(" ", 1)[0]


def review(p: Plan) -> tuple[str, bool]:
    """Every requirement and what proves it, every case, each expected result and the checks that
    prove it, then every gap: (text, ready)."""
    out = []
    if p.requirements:
        proof = links(p)
        out.append(f"requirements (source: {p.source or 'not named'})")
        for r in p.requirements:
            src = f" [{r.source}]" if r.source else ""
            by = ("OUT OF SCOPE: " + r.out_of_scope if r.out_of_scope else
                  "<- " + "; ".join(f"{cid} E{n}" for cid, n in proof[r.id]) if proof[r.id] else "<- NOT COVERED")
            out.append(f"  {r.id} {r.text}{src}  {by}")
        out += [f"  fingerprint {fingerprint(p)}" + (" (approved)" if p.approved == fingerprint(p) else ""), ""]
    for c in p.cases:
        out.append(f"{c.id}  {c.title}".rstrip())
        proof: dict[str, list[str]] = {}
        for step in _checks(c.body):
            for eid in _for_ids(step.argv):
                proof.setdefault(eid, []).append(f"line {step.line}: {step.summary()}")
        for i, text in enumerate(c.expects, 1):
            by = proof.get(f"E{i}")
            reqs = c.expect_reqs[i - 1] if i <= len(c.expect_reqs) else []
            tag = f" [{', '.join(reqs)}]" if reqs else ""
            out.append(f"  E{i} {text}{tag}  <- " + ("; ".join(by) if by else "NOT PROVEN"))
        if not c.expects:
            checks = sum(1 for _ in _checks(c.body))
            out.append(f"  (no expected results listed; {checks} check(s))")
    out.append("")
    gaps = p.gaps + requirement_gaps(p)
    if gaps:
        out += [f"{len(gaps)} gap(s): not ready to run"] + [f"  {g}" for g in gaps]
    else:
        n = sum(len(c.expects) for c in p.cases)
        out.append(f"ready to run: {len(p.cases)} case(s), {n} expected result(s), every one proven")
        if p.requirements:
            excluded = sum(1 for r in p.requirements if r.out_of_scope)
            out.append(f"{len(p.requirements)} requirement(s): {len(p.requirements) - excluded} covered"
                       + (f", {excluded} out of scope" if excluded else "") + ", approved")
    return "\n".join(out), not gaps


def _options(args: list[str], names: tuple) -> dict | None:
    """`--name value` pairs, each name at most once; None when anything else is there."""
    out: dict[str, str] = {}
    if len(args) % 2:
        return None
    for k, v in zip(args[::2], args[1::2]):
        if k not in names or k in out:
            return None
        out[k] = v
    return out


def _reqs(args: list[str]) -> list[str] | None:
    """`--req R1 --req R2` after an expected result; None when anything else is there."""
    if len(args) % 2 or any(k != "--req" or not v or v.startswith("-") for k, v in zip(args[::2], args[1::2])):
        return None
    return list(dict.fromkeys(args[1::2]))


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


def is_check(step: Step) -> bool:
    """A `check` the plan wrote, not a `require` (a setup condition, never an expected result)."""
    return step.argv[0] == "check" and "--setup" not in step.argv


def _has_check(body: list, flows: dict[str, Flow], seen: frozenset = frozenset(), setup: bool = False) -> bool:
    """`setup`: a `require` counts too (anything that records a check, as inside a `retry`)."""
    for item in body:
        if isinstance(item, Step) and (is_check(item) or setup and item.argv[0] == "check"):
            return True
        if isinstance(item, RunFlow) and item.flow in flows and item.flow not in seen:
            if _has_check(flows[item.flow].body, flows, seen | {item.flow}, setup):
                return True
        if isinstance(item, If) and (_has_check(item.then, flows, seen, setup)
                                     or _has_check(item.other, flows, seen, setup)):
            return True
    return False


def _checks(body: list):
    """The `check` steps written in this body, inside `if`/`else` and `retry` too."""
    for item in body:
        if isinstance(item, Step) and is_check(item):
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


def _check_args(words: list[str], fail) -> argparse.Namespace | None:
    """Let the CLI's own parser reject bad flags now, not halfway through a run."""
    from .cli import build_parser
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            return build_parser().parse_args(words)
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
            if in_retry and _has_check(flows[item.flow].body, flows, setup=True):
                raise PlanError(f"{source} line {item.line}: flow `{item.flow}` has a `check` or `require`, so it cannot run "
                                f"inside `retry` (failed attempts would be recorded): use `wait` in the flow")
            _check_flows(flows[item.flow].body, flows, path + [item.flow], source, in_retry)
        elif isinstance(item, Retry):
            _check_flows(item.body, flows, path, source, in_retry=True)
        elif isinstance(item, If):
            _check_flows(item.then, flows, path, source, in_retry)
            _check_flows(item.other, flows, path, source, in_retry)
