"""The app map: saved routes the agent runs in one command during a live run.

A route is a plan `flow` (format: plan.py). `tnazar flow <name>` finds it in `plans/*.plan`
(only files whose `app` is the active run's app; a map file may hold flows and no case),
runs it against the active run through the normal CLI, so every step is recorded and shown
in the dashboard timeline, and leaves the run open. `tnazar flows` lists the routes.

Unlike `tnazar run`, a failed step does not fail the current case: the agent reads the exit
code and decides. A `check` in the flow is recorded in the current case as usual.

Exit codes: 0 done; 1 a step or check failed; 4 a `wait --or` saw the other screen;
2 no such flow, no active run, a bad `--plan` file (other broken map files are skipped with a warning); 3 a dashboard message stopped the flow;
5 the user paused the run mid-flow.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import cli, ident, live, navmap, uia2
from . import device as dev
from . import screen as scr
from .errors import UserError
from .plan import Plan, PlanError, load
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

    def long_pause(self) -> Paused:
        return Paused()


def _sources(args, app: str | None) -> list[tuple[Path, Plan]]:
    if args.plan:
        return [(Path(args.plan), load(args.plan, flows_only=True))]
    found, broken = route_files(app)
    for b in broken:
        print(f"warning: skipped {b}", file=sys.stderr)
    return found


def run_flow(args) -> int:
    # From the first moment: a message the background listener delivers while the flow starts still stops it.
    since = live.Since()
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
    # The flow's checks go to whatever case the agent has open.
    ex = FlowExecutor(p, the_run, the_run.current_case() or "", since)
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


# ---------------------------------------------------------------- learned routes: goto and map
#
# `goto <phrase>` reaches a screen in one command. A saved flow of that name wins. Otherwise it
# reads the screen once, finds it in the navigation map (navmap.py) and taps the learned route,
# waiting after each tap until the screen *is* the expected one (identity, not a label: the
# button that opens "Passbook" is often labelled "Passbook" itself). It stops at the first hop
# that goes elsewhere, explains it, and remembers the failure. A miss costs the one read and
# prints the screen like `observe`, so exploring from there costs nothing extra.
#
# Exit codes: 0 arrived (or already there, or --dry-run); 1 a hop failed; 2 not in the map, the
# start screen is unknown, no route can run, or the phrase means two screens; 3 a dashboard
# message; 5 paused.

STABLE_ELSEWHERE = 1.5  # seconds another screen must hold before a hop counts as gone wrong


def _need_app():
    active = get_active()
    if not active:
        raise UserError("no active run: `./tnazar start <name> --package <app>` first")
    app = _app_of(active)
    if not app:
        raise UserError("the run has no app: start it with --package to learn and use routes")
    return active[0], app


def _flow_for(phrase: str, app: str):
    want = navmap.normalise(phrase)
    found, _ = route_files(app)
    hits = [(f, p, n) for f, p in found for n in p.flows if navmap.normalise(n) == want and want]
    return hits[0] if len(hits) == 1 else None


def goto(args) -> int:
    since = live.Since()
    the_run, app = _need_app()
    phrase = " ".join(args.target).strip()
    if not navmap.enabled():
        raise UserError("the navigation map is off (TNAZAR_MAP=off)")
    if flow := _flow_for(phrase, app):
        f, _, name = flow
        print(f"goto {phrase}: saved flow `{name}` ({_rel(f)})")
        if args.dry_run:
            return 0
        # A new request: it ends an open search for an earlier phrase, like any goto.
        the_run.log({"type": "nav", "phrase": phrase, "outcome": "flow", "flow": name})
        code = run_flow(argparse.Namespace(name=name, plan=str(f), device=args.device))
        return cli.EXIT_FAIL if code == cli.EXIT_INSTEAD else code  # goto's codes have no 4

    ctx = cli.Ctx.from_args(args.device)
    version = the_run.meta().get("app_version", "")
    screen = ctx.capture()
    it = ident.identify(screen, app=app, secrets=navmap.secret_values())
    start_sid = ctx.stamp(screen, it)
    m = navmap.current(the_run, app, version)
    for w in m.warnings[:3]:
        print(f"warning: map: {w}", file=sys.stderr)
    here = m.where(it) if it.id else None
    targets = m.resolve(phrase)

    def nav(outcome: str, **extra) -> None:
        ctx.log({"type": "nav", "phrase": phrase, "outcome": outcome, "sid": start_sid, **extra})

    if not targets:
        if not navmap.normalise(phrase):
            raise UserError(f"say which screen: {phrase!r} names none")
        close = navmap.suggest(navmap.normalise(phrase), m.known_names() + _flow_names(app))
        nav("unknown", episode=True)
        print(f"goto {phrase}: not in the map yet." + (f" Did you mean: {', '.join(close)}?" if close else ""))
        print("Explore from here, then `check` the screen: the route and the name are learned. Current screen:")
        cli.print_screen(screen)
        return cli.EXIT_ERROR
    if len(targets) > 1:
        nav("conflict")
        print(f"goto {phrase}: the name means more than one screen: "
              + ", ".join(f"{t} ({m.label(t) or 'unnamed'})" for t in sorted(targets))
              + f". Fix it with `tnazar map name <screen> {phrase}` or `tnazar map forget {phrase}`.")
        return cli.EXIT_ERROR
    if here is None:
        nav("start_unknown")
        kind = it.kind if not it.id else "not in the map yet"
        print(f"goto {phrase}: this screen is {kind}, so no route starts here. Known: "
              f"{', '.join(m.known_names()) or '(no names yet)'}. Current screen:")
        cli.print_screen(screen)
        return cli.EXIT_ERROR
    path = m.route(here, targets, via=m.via(phrase))
    if path == []:
        nav("arrived", hops=0)
        print(f"goto {phrase}: already there")
        cli.print_screen(screen)
        return 0
    if path is None:
        nav("no_route")
        print(f"goto {phrase}: no route the map can run from here (only unsafe, typed, back or "
              f"pixel steps are known, or a step failed earlier in this run). Explore. Current screen:")
        cli.print_screen(screen)
        return cli.EXIT_ERROR
    route_text = " → ".join(_hop_text(h) for h in path)
    if args.dry_run:
        print(f"goto {phrase}: {len(path)} step(s): {route_text}")
        for h in path:
            print(f"  {h.frm} -> {h.to}  {_hop_text(h)}  (worked in {len(h.ok_runs)} run(s))")
        return 0

    began = time.monotonic()
    cli.cmd_say(argparse.Namespace(text=f"Going to {phrase} by a learned route ({len(path)} steps)"))
    cur, cur_it = screen, it
    for i, hop in enumerate(path, 1):
        if i > 1:
            held = cli.hold(the_run)
            if held is None and live.needs_agent(since.take()):
                held = live.EXIT_HELD
            if held is not None:
                nav("stopped", hops=i - 1)
                print(f"goto {phrase}: stopped by you before step {i}; `observe` first: the route stopped part-way.")
                return held
        try:
            with live.device_lock():
                el = _find_for_hop(ctx, cur, hop)
                if el is None:
                    return _hop_failed(ctx, m, nav, phrase, i, hop, cur, "its button is not on the screen")
                x, y = el.center
                dev.tap(ctx.adb, x, y)
                sel = hop.sel or {}
                ctx.log({"type": "tap", "selector": hop.sel, "xy": [x, y],
                         "label": hop.label or sel.get("text") or sel.get("desc") or sel.get("id") or "",
                         "long": False, "screen": list(ctx.adb.screen_size()), "sid": ctx.stamp(cur, cur_it),
                         "via": "goto"})
        except live.DeviceBusy:
            raise UserError("the device is busy with another step; run goto again") from None
        arrived, cur, cur_it, why = _arrive(ctx, m, app, hop, args.timeout)
        if not arrived:
            return _hop_failed(ctx, m, nav, phrase, i, hop, cur, why)
    if sid := ctx.stamp(cur, cur_it):
        ctx.log({"type": "seen", "sid": sid})
    cli.save_xml(cli.state_dir() / "last-screen.xml", cur.xml)
    took = time.monotonic() - began
    nav("arrived", hops=len(path), took_ms=round(took * 1000))
    print(f"goto {phrase}: arrived in {len(path)} step(s), {took:.1f}s ({route_text})")
    cli.print_screen(cur)
    return 0


def _hop_text(h) -> str:
    sel = h.sel or {}
    what = h.label or sel.get("text") or sel.get("desc") or sel.get("id") or "?"
    return f'tap "{what}"' + (f" (scroll {h.scroll['direction']})" if h.scroll else "")


def _flow_names(app: str) -> list[str]:
    return sorted({navmap.normalise(n) for _, p in route_files(app)[0] for n in p.flows} - {""})


def _find_for_hop(ctx, cur, hop):
    """The hop's button on the screen just read; scrolled to the way it was found when learned
    (recorded as a `scroll` event, so a replay of this run scrolls too)."""
    sel = scr.Selector.from_dict(hop.sel)
    el = scr.find(cur, sel, strict=True)
    if el is not None or not hop.scroll:
        return el
    direction = hop.scroll.get("direction") or "down"
    vec = dev.scroll_vector(direction, dev.scroll_area(cur, direction, ctx.adb.screen_size()))
    screen = cur
    for _ in range(dev.SCROLL_MAX):
        dev.swipe(ctx.adb, *vec)
        before, screen = screen.signature(), scr.capture(ctx.adb)
        if (el := scr.find(screen, sel, strict=True)) is not None or screen.signature() == before:
            break
    ctx.log({"type": "scroll", "direction": direction, "to": hop.sel, "within": None, "times": 1,
             "found": el is not None, "via": "goto"})
    return el


def _arrive(ctx, m, app, hop, timeout):
    """Poll until the screen is the hop's target. Another screen holding still means the hop
    went somewhere else; the screen it started from just means the app is still on its way."""
    interval = 0.15 if uia2.active(ctx.adb) else 0.4
    deadline = time.monotonic() + timeout
    other, other_since = None, 0.0
    screen = it = None
    while True:
        try:
            screen = scr.capture(ctx.adb)
        except cli.AdbError:
            screen = None
        if screen is not None:
            it = ident.identify(screen, app=app, secrets=navmap.secret_values())
            sid = m.where(it) if it.id else None
            expected, source = m.screens.get(hop.to), m.screens.get(hop.frm)
            # The page may be held twice in the map (read with and without its title): a read that
            # fits the target and not the screen it left counts as arriving.
            if sid == hop.to or (it.id and expected is not None and sid != hop.frm
                                 and ident.compatible(expected, it)
                                 and not (source is not None and ident.compatible(source, it))):
                return True, screen, it, ""
            if it.id and sid != hop.frm:
                now = time.monotonic()
                if other != it.id:
                    other, other_since = it.id, now
                elif now - other_since >= STABLE_ELSEWHERE:
                    name = m.label(sid) if sid else ""
                    return False, screen, it, f"it went to {('“' + name + '”') if name else 'another screen'}"
            else:
                other = None
        if time.monotonic() >= deadline:
            return False, screen, it, f"the screen did not change to the expected one within {timeout:g}s"
        time.sleep(interval)


def _hop_failed(ctx, m, nav, phrase, i, hop, screen, why) -> int:
    expected = m.label(hop.to) or hop.to
    nav("failed", hops=i - 1, ok=False, expected=expected, why=why,
        edge={"from": hop.frm, "to": hop.to, "kind": hop.kind, "sel": hop.sel})
    print(f"goto {phrase}: FAILED at step {i} ({_hop_text(hop)}): expected “{expected}”, but {why}. "
          f"This step is not used again in this run; explore from here and the new way is learned.")
    if screen is not None:
        cli.save_xml(cli.state_dir() / "last-screen.xml", screen.xml)
        print("Current screen:")
        cli.print_screen(screen)
    return cli.EXIT_FAIL


def _load(app: str, active):
    """The map of `app`, with what the active run learned so far when it tests that app."""
    if active and _app_of(active) == app:
        run = active[0]
        return navmap.current(run, app, run.meta().get("app_version", ""))
    return navmap.NavMap.load(app)


def cmd_map(args) -> int:
    active = get_active()
    app = args.app or _app_of(active)
    if not app:
        raise UserError("which app? start a run with --package, or pass --app <package>")
    if args.action == "list":
        return _map_list(app, active, args.edges)
    if args.action == "name":
        return _map_name(app, active, args)
    if args.action == "forget":
        if len(args.args) != 1:
            raise UserError("usage: tnazar map forget <phrase|screen-id|route-id>")
        m = _load(app, active)
        key = args.args[0]
        what = "screen" if key in m.screens or key in m.alias else \
            "edge" if any(navmap.edge_id(e) == key for e in m.edges.values()) else "name"
        if what == "name" and not m.resolve(key):
            raise UserError(f"nothing called {key!r} in the map")
        navmap.Store(app).append([navmap.forget_line(what, key)], None)
        print(f"forgot {what} {key}")
        return 0
    if args.action == "audit":
        m = navmap.NavMap.load(app)
        steps = [(e.label, e.sel or {}, (e.scroll or {}).get("to") or {}) for e in m.edges.values()]
        strings = sorted({str(v) for label, sel, to in steps for v in (label, *sel.values(), *to.values())
                          if isinstance(v, str) and v} | set(m.known_names()))
        print(f"every readable string in the map of {app} ({navmap.Store(app).path}):")
        for s in strings:
            print(f"  {s}")
        return 0
    n = navmap.compact(app)
    print(f"map of {app} compacted: {n} lines")
    return 0


def _map_list(app, active, with_edges) -> int:
    m = _load(app, active)
    for w in m.warnings[:3]:
        print(f"warning: map: {w}", file=sys.stderr)
    runnable = [e for e in m.edges.values() if m.runnable(e)]
    print(f"map: {app}  {len(m.screens)} screens, {len(runnable)} usable steps, {len(m.known_names())} names"
          f"  ({navmap.Store(app).path})")
    names_of: dict = {}
    for p in m.known_names():
        for sid in m.resolve(p):
            names_of.setdefault(sid, []).append(p)
    for sid in m.screens:
        outs = [e for e in m.edges.values() if e.frm == sid]
        ins = [e for e in m.edges.values() if e.to == sid]
        if not (outs or ins or sid in names_of):
            continue
        flags = " [secure]" if sid in m.secure else ""
        names = f"  names: {', '.join(names_of[sid])}" if sid in names_of else ""
        print(f"  {sid}  {m.label(sid) or '(unnamed)'}{names}{flags}")
    flows = sorted({n for _, p in route_files(app)[0] for n in p.flows})
    if flows:
        print(f"  saved flows: {', '.join(flows)}")
    if with_edges:
        print("steps:")
        for e in m.edges.values():
            state = "" if m.runnable(e) else " [unsafe]" if m.unsafe(e) else " [retired]" if m.retired(e) else \
                " [failed in this run]" if e.key in m.failed else f" [{e.kind}: not replayed]"
            print(f"  {navmap.edge_id(e)}  {e.frm} -> {e.to}  {_hop_text(e)}  "
                  f"(worked in {len(e.ok_runs)} run(s)){state}")
    return 0


def _map_name(app, active, args) -> int:
    if len(args.args) < 2:
        raise UserError("usage: tnazar map name <screen-id|here> <phrase> [--alias other]")
    where, phrases = args.args[0], [" ".join(args.args[1:]), *args.alias]
    for p_ in phrases:
        navmap.check_phrase(p_)
    lines = []
    if where == "here":
        if not active or _app_of(active) != app:
            raise UserError("`here` needs an active run of this app on the device")
        ctx = cli.Ctx.from_args(args.device)
        sid = ctx.stamp(ctx.capture())
        if not sid:
            raise UserError("this screen cannot be named (a loading, web or system screen)")
        rec = navmap.run_screens(active[0]).get(sid)
        if rec:
            lines.append(dict(rec, k="screen"))
        via = navmap.entry_tap(active[0].events(), sid, navmap.run_screens(active[0]))
    else:
        via = None
        m = _load(app, active)
        if where not in m.screens and where not in m.alias:
            raise UserError(f"no screen {where} in the map; `tnazar map` lists them")
        sid = m.canon(where)
    lines += [navmap.name_line(p, sid, explicit=True, via=via) for p in phrases]
    navmap.Store(app).append(lines, None)
    print(f"named {sid}: {', '.join(navmap.normalise(p) for p in phrases)}")
    return 0
