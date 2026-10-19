"""tnazar: command line for driving an Android device as a feature tester."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from . import commands
from . import device as dev
from . import live
from . import uia2
from . import report as rpt
from . import screen as scr
from .adb import Adb, AdbError, adb_binary, list_devices, pick_serial
from .env import describe, load_dotenv, mask_xml, redact, secret_values
from .errors import NotFound, UserError
from .judge import _end_case, evidence_shot
from .session import NAME_RE, Run, active_owner, clear_active, get_active, now_iso, runs_dir, set_active, state_dir
from .targets import _not_found_msg, parse_target, resolve, selector_of

EXIT_FAIL = 1
EXIT_ERROR = 2
EXIT_INSTEAD = 4  # `wait A --or B`: B showed up, so A is not coming
# tap / type --into look for their target for this long: right after Back or a tap, the next
# screen is often not there yet. Costs nothing when the target is already on screen.
TARGET_WAIT = 3.0
TIMED = commands.TIMED_EVENTS
HELD = commands.DEVICE_COMMANDS


# ---------------------------------------------------------------- context

class Ctx:
    def __init__(self, adb: Adb, run: Run | None):
        self.adb = adb
        self.run = run
        self._mark = time.monotonic()

    @classmethod
    def from_args(cls, device_arg: str | None) -> "Ctx":
        active = get_active()
        serial = device_arg or (active[1] if active else None)
        return cls(Adb(pick_serial(serial)), active[0] if active else None)

    def log(self, event: dict) -> None:
        if self.run:
            if event["type"] in ("check", "note", "shot") and "case" not in event:
                event["case"] = self.run.current_case()
            if event["type"] in TIMED:
                now = time.monotonic()
                event["dur_ms"] = round((now - self._mark) * 1000)
                self._mark = now
                if event["dur_ms"] > rpt.SLOW_MS:
                    why = f" ({event['polls']} screen reads)" if event.get("polls") else ""
                    print(f"SLOW: {event['type']} took {event['dur_ms'] / 1000:.1f}s{why}", file=sys.stderr)
            self.run.log(event)

    def capture(self) -> scr.Screen:
        screen = scr.capture(self.adb)
        save_xml(state_dir() / "last-screen.xml", screen.xml)
        return screen

    def last_screen(self) -> scr.Screen:
        f = state_dir() / "last-screen.xml"
        if not f.exists():
            raise UserError("no previous `observe`; #index targets need one")
        return scr.parse(f.read_text())

    def artifact(self, kind: str, label: str, ext: str) -> Path:
        if self.run:
            return self.run.artifact(kind, label, ext)
        return state_dir() / kind / f"{label}.{ext}"

    def rel(self, p: Path) -> str:
        return self.run.rel(p) if self.run else str(p)


def save_xml(path: Path, xml: str) -> None:
    # A secret typed into a plain field is plain text in the tree.
    path.write_text(mask_xml(xml))


def _screen_size(ctx: Ctx) -> list[int]:
    # The dump's extent is the top window, which is smaller than the display for dialogs.
    return list(ctx.adb.screen_size())


# ---------------------------------------------------------------- commands

def cmd_devices(args) -> int:
    devices = list_devices()
    if not devices:
        print("no devices")
        return EXIT_FAIL
    for d in devices:
        print(f"{d.serial}\t{d.state}\t{d.model}")
    return 0


def cmd_packages(args) -> int:
    ctx = Ctx.from_args(args.device)
    for p in dev.installed_packages(ctx.adb, args.keyword or ""):
        print(p)
    return 0


def cmd_start(args) -> int:
    if not NAME_RE.match(args.name):
        raise UserError("run name must be letters, digits, '-' or '_' (max 64)")
    if active := get_active():
        refuse_if_owned(active[0])
        finish_run(quiet=True)
    serial = pick_serial(args.device)
    adb = Adb(serial)
    model = next((d.model for d in list_devices() if d.serial == serial), "")
    meta = {
        "device": serial,
        "model": model,
        "android": adb.shell("getprop", "ro.build.version.release").strip(),
        "prompt": args.prompt or "",
        "package": args.package or "",
        "fresh": bool(args.fresh),
    }
    if args.package:
        if args.package not in dev.installed_packages(adb, args.package):
            raise UserError(f"{args.package} is not installed on {serial}")
        meta["app_version"] = dev.app_version(adb, args.package)
    if not args.keep_animations:
        meta["animations_before"] = dev.animations_off(adb)
    run = Run.create(args.name, meta)
    set_active(run, serial)
    live.mark_all_read()  # messages typed before this run are not instructions for it
    live.set_paused(False)
    dev.logcat_clear(adb)
    print(f"run: {run.name}  device: {serial} {model}".rstrip())
    print(f"dir: {run.path}")
    if args.package:
        print(f"app: {args.package} {meta.get('app_version', '')}".rstrip())
        if args.fresh:
            dev.force_stop(adb, args.package)
            dev.clear_data(adb, args.package)
            print("app data cleared")
    if not args.keep_animations:
        print("animations: off (restored on stop)")
    print("screen reads: " + ("fast (UiAutomator2 server)" if uia2.start(adb) else "uiautomator dump"))
    return 0


def refuse_if_owned(run: Run) -> None:
    """Two runs on one device steal the screen from each other: never take over a live one."""
    if (pid := active_owner()) is not None:
        raise UserError(f"a run is in progress on this device: {run.name} (pid {pid}); stop it or wait")


def finish_run(quiet: bool = False) -> Run | None:
    active = get_active()
    if not active:
        return None
    run, serial = active
    meta = run.meta()
    try:
        adb = Adb(serial)
        uia2.stop(adb)
        if meta.get("animations_before"):
            dev.animations_restore(adb, meta["animations_before"])
        if run.current_case():
            run.log({"type": "case_end", "case": run.current_case(), "status": "blocked",
                     "reason": "run stopped before the case finished"})
    except AdbError as exc:
        print(f"warning: device cleanup skipped: {exc}", file=sys.stderr)
    run.update_meta(finished_at=now_iso())
    clear_active()
    live.set_paused(False)
    paths = rpt.write(run)
    if not quiet:
        summary = rpt.summarize(run)
        print(f"run finished: {run.name}")
        print(rpt.summary_line(summary))
        for p in paths:
            print(f"report: {p}")
    return run


def cmd_stop(args) -> int:
    run = finish_run(quiet=args.quiet)
    if run is None and not args.if_active:
        raise UserError("no active run")
    return 0


def cmd_status(args) -> int:
    active = get_active()
    if not active:
        print("no active run")
        return 0
    run, serial = active
    s = rpt.summarize(run)
    print(f"run: {run.name}  device: {serial}  case: {run.current_case() or '-'}")
    print(rpt.summary_line(s))
    return 0


def cmd_launch(args) -> int:
    ctx = Ctx.from_args(args.device)
    package = args.package or (ctx.run.meta().get("package") if ctx.run else "")
    if not package:
        raise UserError("which app? pass a package or start the run with --package")
    if args.fresh:
        dev.force_stop(ctx.adb, package)
        dev.clear_data(ctx.adb, package)
    elif args.restart:
        dev.force_stop(ctx.adb, package)
    component = dev.launch(ctx.adb, package, clean=args.fresh or args.restart)
    ctx.log({"type": "launch", "package": package, "fresh": args.fresh, "restart": args.restart})
    print(f"launched {component}")
    return 0


def cmd_open_url(args) -> int:
    ctx = Ctx.from_args(args.device)
    dev.open_url(ctx.adb, args.url)
    ctx.log({"type": "open_url", "url": args.url})
    print(f"opened {args.url}")
    return 0


def cmd_observe(args) -> int:
    ctx = Ctx.from_args(args.device)
    screen = ctx.capture()
    shown = screen.elements if args.all else screen.interesting()
    fg = screen.package or "?"
    print(f"screen: {fg}  "
          f"{len(screen.elements)} elements ({len(shown)} shown)")
    values = secret_values()
    for e in shown:
        print(describe(e, values))
    if ctx.run:
        save_xml(ctx.artifact("dumps", args.label or "observe", "xml"), screen.xml)
    if args.shot:
        path = dev.screenshot(ctx.adb, ctx.artifact("shots", args.label or "observe", "png"))
        print(f"shot: {path}")
    return 0


def cmd_tap(args) -> int:
    ctx = Ctx.from_args(args.device)
    t = resolve(ctx, args.target, args, timeout=args.wait)
    x, y = t["xy"]
    if args.long:
        dev.long_press(ctx.adb, x, y)
    else:
        dev.tap(ctx.adb, x, y)
    ctx.log({"type": "tap", "selector": t["selector"], "xy": [x, y], "label": t["label"],
             "long": args.long, "screen": _screen_size(ctx)})
    print(f"{'long-pressed' if args.long else 'tapped'} {t['label']!r} at {x},{y}")
    return 0


def cmd_type(args) -> int:
    ctx = Ctx.from_args(args.device)
    if args.env:
        text = os.environ.get(args.env)
        if text is None:
            text = live.secret(args.env)
        if text is None:
            raise UserError(f"{args.env} is not set in the environment, .env or the dashboard")
    elif args.text is not None:
        text = args.text
    else:
        raise UserError("give text to type, or --env VAR")
    target, secret = None, args.secret or bool(args.env)
    if args.into:
        opts = argparse.Namespace(id=args.id, desc=args.desc, text=None,
                                  contains=args.contains, nth=args.nth)
        target = resolve(ctx, args.into, opts, timeout=args.wait)
        el = target["element"]
        secret = secret or bool(el and el.password)
        dev.tap(ctx.adb, *target["xy"])
        if args.clear:
            dev.clear_field(ctx.adb, len(el.text) if el and el.text and not el.password else dev.CLEAR_KEYS)
    elif args.clear:
        dev.clear_field(ctx.adb, dev.CLEAR_KEYS)
    dev.type_text(ctx.adb, text)
    if args.enter:
        dev.key(ctx.adb, "enter")
    ctx.log({
        "type": "type",
        "text": None if secret else text,
        "secret": secret,
        "env": args.env,
        "selector": target["selector"] if target else None,
        "xy": list(target["xy"]) if target else None,
        "screen": _screen_size(ctx) if target else None,
        "clear": args.clear,
        "enter": args.enter,
    })
    shown = "•" * len(text) if secret else repr(text)
    where = f" into {target['label']!r}" if target else ""
    print(f"typed {shown}{where}{' + enter' if args.enter else ''}")
    return 0


def cmd_key(args) -> int:
    ctx = Ctx.from_args(args.device)
    for name in args.keys:
        event = {"type": "key", "key": name}
        if dev.is_back(name):
            # Replay needs this: back over an open keyboard only closes the keyboard.
            event["keyboard"] = dev.keyboard_shown(ctx.adb)
        dev.key(ctx.adb, name)
        ctx.log(event)
    print(f"pressed {' '.join(args.keys)}")
    return 0


def cmd_scroll(args) -> int:
    within = selector_of(args.within) if args.within else None
    goal = selector_of(args.to, contains=args.contains) if args.to else None
    ctx = Ctx.from_args(args.device)
    # Only read the screen when there is something to look for or a container to find.
    sideways = args.direction in ("left", "right")
    screen = ctx.capture() if args.to or args.within or sideways else None
    area = dev.scroll_area(screen, args.direction, ctx.adb.screen_size())
    if within:
        container = scr.find(screen, within)
        if container is None:
            raise NotFound(_not_found_msg(within, screen))
        area = container.bounds
    vec = dev.scroll_vector(args.direction, area)
    found = None
    for i in range(args.max if goal else args.times):
        if goal and (found := scr.find(screen, goal)):
            break
        dev.swipe(ctx.adb, *vec)
        if not goal:
            continue
        before = screen.signature()
        screen = ctx.capture()
        if screen.signature() == before:
            break  # reached the end of the list
    if goal and not found:
        found = scr.find(screen, goal)
    ctx.log({"type": "scroll", "direction": args.direction, "to": goal.to_dict() if goal else None,
             "within": args.within, "times": args.times, "found": bool(found) if goal else None})
    if goal:
        if not found:
            print(f"scrolled {args.direction}; {goal} not found")
            return EXIT_FAIL
        print(f"scrolled {args.direction}; found {describe(found)}")
    else:
        print(f"scrolled {args.direction} x{args.times}")
    return 0


def cmd_swipe(args) -> int:
    ctx = Ctx.from_args(args.device)
    dev.swipe(ctx.adb, args.x1, args.y1, args.x2, args.y2, args.ms)
    ctx.log({"type": "swipe", "from": [args.x1, args.y1], "to": [args.x2, args.y2], "ms": args.ms,
             "screen": list(ctx.adb.screen_size())})
    print(f"swiped {args.x1},{args.y1} -> {args.x2},{args.y2}")
    return 0


def cmd_wait(args) -> int:
    ctx = Ctx.from_args(args.device)
    _, sel = parse_target(args.target, args)
    alts = [selector_of(a) for a in args.alt or []]
    stats: dict = {}
    ok, screen, el = dev.wait_for(ctx.adb, sel, args.timeout, gone=args.gone, stats=stats, alternatives=alts)
    instead = alts[stats["alternative"]] if "alternative" in stats else None
    ctx.log({"type": "wait", "selector": sel.to_dict(), "gone": args.gone, "timeout": args.timeout, "ok": ok,
             "polls": stats.get("polls"), "found_instead": instead.to_dict() if instead else None})
    state = "gone" if args.gone else "visible"
    if instead:
        print(f"found instead: {instead} {describe(el)}")
        return EXIT_INSTEAD
    if ok:
        print(f"ok: {sel} is {state}" + (f" {describe(el)}" if el else ""))
        return 0
    print(f"timeout after {args.timeout}s: {sel} not {state}")
    if not args.gone:
        print(_not_found_msg(sel, screen).split("\n", 1)[1])
    return EXIT_FAIL


def cmd_wait_online(args) -> int:
    ctx = Ctx.from_args(args.device)
    ok = dev.wait_online(ctx.adb, args.timeout, args.host)
    ctx.log({"type": "wait_online", "ok": ok, "timeout": args.timeout})
    print("online" if ok else f"still offline after {args.timeout}s")
    return 0 if ok else EXIT_FAIL


def cmd_check(args) -> int:
    ctx = Ctx.from_args(args.device)
    if args.result:
        if not args.what:
            raise UserError("--what is required: say what was verified")
        ok = args.result == "pass"
        event = {"type": "check", "kind": args.kind, "ok": ok, "what": args.what,
                 "detail": args.detail or ""}
        if args.shot and (shot := evidence_shot(ctx, f"check-{args.kind}")):
            event["shot"] = shot
        ctx.log(event)
        print(f"{'PASS' if ok else 'FAIL'} [{args.kind}] {args.what}")
        return 0 if ok else EXIT_FAIL

    _, sel = parse_target(args.target, args)
    stats: dict = {}
    # Exact: "successful" must not pass on "Payment unsuccessful". `~text` / --contains stay partial.
    ok, screen, el = dev.wait_for(ctx.adb, sel, args.timeout, gone=args.gone, stats=stats, exact=True)
    if ok and el is not None:
        for flag, actual in (("checked", el.checked), ("enabled", el.enabled)):
            want = getattr(args, flag)
            if want is not None and want != actual:
                ok = False
    what = args.what or _describe_check(sel, args)
    label = "pass" if ok else "fail"
    event = {"type": "check", "kind": "ui", "ok": ok, "what": what, "selector": sel.to_dict(),
             "gone": args.gone, "checked": args.checked, "enabled": args.enabled, "polls": stats.get("polls")}
    if screen is not None:
        # Same screen, same fingerprint: the report shows one screenshot for all its checks.
        event["screen_sig"] = hashlib.sha1(screen.signature().encode()).hexdigest()[:12]
    if shot := evidence_shot(ctx, f"check-{label}"):
        event["shot"] = shot
    if ctx.run and not ok:
        dump = ctx.artifact("dumps", "check-fail", "xml")
        save_xml(dump, screen.xml)
        event["dump"] = ctx.rel(dump)
    ctx.log(event)
    print(f"{'PASS' if ok else 'FAIL'} [ui] {what}")
    if not ok and not args.gone and el is None:
        print(_not_found_msg(sel, screen).split("\n", 1)[1])
    return 0 if ok else EXIT_FAIL


def _describe_check(sel: scr.Selector, args) -> str:
    s = f"{sel} is {'gone' if args.gone else 'visible'}"
    if args.checked is not None:
        s += f", {'checked' if args.checked else 'unchecked'}"
    if args.enabled is not None:
        s += f", {'enabled' if args.enabled else 'disabled'}"
    return s


def cmd_shot(args) -> int:
    ctx = Ctx.from_args(args.device)
    path = dev.screenshot(ctx.adb, ctx.artifact("shots", args.label or "shot", "png"))
    ctx.log({"type": "shot", "label": args.label or "", "file": ctx.rel(path)})
    print(f"shot: {path}")
    return 0


def cmd_case(args) -> int:
    ctx = Ctx.from_args(args.device)
    if not ctx.run:
        raise UserError("cases need an active run: tnazar start <name>")
    if args.action == "start":
        if ctx.run.current_case():
            _end_case(ctx, None, "next case started without an explicit result")
        dev.logcat_clear(ctx.adb)
        ctx.log({"type": "case_start", "case": args.id, "title": args.title or ""})
        print(f"case {args.id} started")
        return 0
    case = ctx.run.current_case()
    if not case:
        raise UserError("no case in progress")
    status = _end_case(ctx, args.status, args.reason)
    print(f"case {case}: {status.upper()}")
    return 0


def cmd_note(args) -> int:
    ctx = Ctx.from_args(args.device)
    ctx.log({"type": "note", "kind": args.kind, "text": args.text})
    print(f"noted [{args.kind}] {args.text}")
    return 0


def cmd_adb(args) -> int:
    ctx = Ctx.from_args(args.device)
    if not args.args:
        raise UserError("usage: tnazar adb <adb args...>")
    proc = subprocess.run([adb_binary(), "-s", ctx.adb.serial, *args.args])
    ctx.log({"type": "adb", "args": redact(args.args), "exit": proc.returncode})
    # adb's own code could collide with tnazar's (3 held, 5 paused) and make a plan runner retry forever.
    return EXIT_FAIL if proc.returncode else 0


def cmd_say(args) -> int:
    ctx_run = get_active()
    if ctx_run:
        ctx_run[0].log({"type": "say", "text": args.text})
    print(f"» {args.text}")
    return 0


def cmd_wait_resume(args) -> int:
    if live.wait_resume():
        print("resumed")
        return 0
    print("still paused by the user; run `./tnazar wait-resume` again before the next step")
    return live.EXIT_PAUSED


def cmd_wait_message(args) -> int:
    active = get_active()
    if not active:
        print("no active run")
        return 0
    run = active[0]
    def same_run() -> bool:
        active = get_active()
        return active is not None and active[0].path == run.path

    if not live.wait_message(args.timeout, same_run):
        print(f"no message in {args.timeout:g}s" if same_run() else "no active run")
        return 0
    live.deliver(run.log)
    print("Read the user's message above and act on it now.")
    return live.EXIT_HELD


def cmd_ui(args) -> int:
    from . import ui
    return ui.serve(args.port, open_browser=not args.no_open)


def cmd_report(args) -> int:
    run = Run.load(args.run) if args.run else (get_active() or (None,))[0]
    if run is None:
        raise UserError("which run? pass a run name")
    for p in rpt.write(run):
        print(f"report: {p}")
    print(rpt.summary_line(rpt.summarize(run)))
    return 0


def cmd_runs(args) -> int:
    d = runs_dir()
    runs = sorted((p for p in d.iterdir() if (p / "run.json").exists()), key=lambda p: p.stat().st_mtime) \
        if d.exists() else []
    for p in runs[-args.last:]:
        run = Run(p)
        print(f"{run.name}\t{rpt.summary_line(rpt.summarize(run))}")
    return 0


def cmd_run(args) -> int:
    from . import runner
    return runner.run(args)


def cmd_replay(args) -> int:
    from . import replay
    return replay.run(args)


# ---------------------------------------------------------------- parser

def _add_selector_flags(p: argparse.ArgumentParser, target_help: str) -> None:
    p.add_argument("target", nargs="?", help=target_help)
    p.add_argument("--id", help="resource-id (short form ok: btn_login)")
    p.add_argument("--desc", help="content-desc / accessibility label")
    p.add_argument("--text", help="visible text")
    p.add_argument("--contains", action="store_true", help="partial, case-insensitive text match")
    p.add_argument("--nth", type=int, default=0, help="pick the n-th match (0-based)")


TARGET_HELP = '"Text" | ~partial | id=btn_x | desc=Label | #index (from observe) | x%%,y%% (same spot on any screen) | x,y'


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tnazar", description="Teesri Nazar: feature-testing agent toolkit for Android.")
    ap.add_argument("--device", "-s", help="device serial (defaults to the active run's device)")
    ap.add_argument("--version", action="version", version=f"tnazar {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_, description=help_)
        p.set_defaults(fn=fn)
        return p

    add("devices", cmd_devices, "list connected devices")
    p = add("packages", cmd_packages, "list installed packages, optionally filtered")
    p.add_argument("keyword", nargs="?")

    p = add("start", cmd_start, "start a test run (recording, evidence, report)")
    p.add_argument("name")
    p.add_argument("--package", "-p", help="app under test")
    p.add_argument("--fresh", action="store_true", help="force-stop and clear app data first")
    p.add_argument("--prompt", help="the original request, kept in the report")
    p.add_argument("--keep-animations", action="store_true", help="do not turn off system animations")

    p = add("stop", cmd_stop, "finish the active run and write the report")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--if-active", action="store_true", help="no error when nothing is running")
    add("status", cmd_status, "show the active run")

    p = add("launch", cmd_launch, "launch the app (defaults to the run's package)")
    p.add_argument("package", nargs="?")
    p.add_argument("--fresh", action="store_true", help="clear app data before launch")
    p.add_argument("--restart", action="store_true", help="force-stop before launch, keep data")
    p = add("open-url", cmd_open_url, "open a deep link or URL")
    p.add_argument("url")

    p = add("observe", cmd_observe, "list what is on screen (elements, ids, centers)")
    p.add_argument("--all", action="store_true", help="include unlabelled layout nodes")
    p.add_argument("--shot", action="store_true", help="also save a screenshot")
    p.add_argument("--label", help="name for saved artifacts")

    p = add("tap", cmd_tap, "tap an element")
    _add_selector_flags(p, TARGET_HELP)
    p.add_argument("--long", action="store_true", help="long-press")
    p.add_argument("--wait", type=float, default=TARGET_WAIT, metavar="SEC",
                   help=f"wait up to SEC for the element (default {TARGET_WAIT:g}: a screen may still be changing)")

    p = add("type", cmd_type, "type text, optionally into a field")
    p.add_argument("text", nargs="?")
    p.add_argument("--into", metavar="TARGET", help=f"field to tap first: {TARGET_HELP}")
    p.add_argument("--id", help=argparse.SUPPRESS)
    p.add_argument("--desc", help=argparse.SUPPRESS)
    p.add_argument("--contains", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--nth", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--clear", action="store_true", help="clear the field first")
    p.add_argument("--enter", action="store_true", help="press enter afterwards")
    p.add_argument("--secret", action="store_true", help="do not record the value")
    p.add_argument("--env", metavar="VAR", help="read the value from an environment variable")
    p.add_argument("--wait", type=float, default=TARGET_WAIT, metavar="SEC")

    p = add("key", cmd_key, "press keys: back, home, enter, tab, del, ... or a keycode")
    p.add_argument("keys", nargs="+")

    p = add("scroll", cmd_scroll, "scroll content (down reveals what is below)")
    p.add_argument("direction", choices=["up", "down", "left", "right"])
    p.add_argument("--to", metavar="TARGET", help="keep scrolling until this is visible")
    p.add_argument("--contains", action="store_true")
    p.add_argument("--within", metavar="TARGET", help="scroll inside this container")
    p.add_argument("--max", type=int, default=dev.SCROLL_MAX, help="max swipes when using --to")
    p.add_argument("--times", type=int, default=1, help="swipes when not using --to")

    p = add("swipe", cmd_swipe, "raw swipe between two points")
    for n in ("x1", "y1", "x2", "y2"):
        p.add_argument(n, type=int)
    p.add_argument("--ms", type=int, default=dev.SWIPE_MS)

    p = add("wait", cmd_wait, "wait until an element appears (or is gone)")
    _add_selector_flags(p, TARGET_HELP)
    p.add_argument("--or", dest="alt", action="append", metavar="TARGET",
                   help="stop at once if this appears instead (exit 4), e.g. an error message")
    p.add_argument("--gone", action="store_true")
    p.add_argument("--timeout", type=float, default=15)

    p = add("wait-online", cmd_wait_online, "wait until the device can reach the internet")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--host", default="8.8.8.8")

    p = add("check", cmd_check, "record a verification (UI by selector, or a manual DB/Figma/API result)")
    _add_selector_flags(p, TARGET_HELP)
    p.add_argument("--gone", action="store_true", help="expect the element to be absent")
    p.add_argument("--checked", type=_bool, default=None, metavar="true|false")
    p.add_argument("--enabled", type=_bool, default=None, metavar="true|false")
    p.add_argument("--timeout", type=float, default=5)
    p.add_argument("--result", choices=["pass", "fail"], help="record a check you verified yourself")
    p.add_argument("--kind", default="ui", choices=["ui", "db", "figma", "api", "other"])
    p.add_argument("--what", help="the expectation, in words")
    p.add_argument("--detail", help="evidence: query + result, mismatch details, ...")
    p.add_argument("--shot", action="store_true", help="attach a screenshot to a manual check")

    p = add("shot", cmd_shot, "save a screenshot as evidence")
    p.add_argument("label", nargs="?")

    p = add("case", cmd_case, "mark test case boundaries")
    csub = p.add_subparsers(dest="action", required=True)
    cs = csub.add_parser("start")
    cs.add_argument("id")
    cs.add_argument("title", nargs="?")
    ce = csub.add_parser("end")
    ce.add_argument("status", nargs="?", choices=["pass", "fail", "blocked", "skipped"],
                    help="omit to derive from the case's checks")
    ce.add_argument("--reason")

    p = add("note", cmd_note, "record an observation or bug")
    p.add_argument("text")
    p.add_argument("--kind", default="observation", choices=["observation", "bug", "question", "risk"])

    p = add("adb", cmd_adb, "run a raw adb command on the device (recorded)")
    p.add_argument("args", nargs=argparse.REMAINDER)

    p = add("say", cmd_say, "narrate what you are about to do (shown in the dashboard timeline)")
    p.add_argument("text")
    p = add("wait-message", cmd_wait_message,
            "block until the user sends a dashboard message (run it in the background while idle)")
    p.add_argument("--timeout", type=float, default=live.MESSAGE_WAIT)
    add("wait-resume", cmd_wait_resume, f"wait up to {int(live.PAUSE_WAIT)}s for the user to resume a paused run")
    p = add("ui", cmd_ui, "live dashboard on localhost: screen, timeline, messages to the agent")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not open the browser")

    p = add("report", cmd_report, "(re)write the report for a run")
    p.add_argument("run", nargs="?")
    p = add("runs", cmd_runs, "list recent runs")
    p.add_argument("--last", type=int, default=10)

    p = add("run", cmd_run, "run every case of a plan file (see teesri_nazar/plan.py for the format)")
    p.add_argument("plan")
    p.add_argument("--only", metavar="IDS", help="run only these cases, e.g. TC-02,TC-05")
    p.add_argument("--from", dest="from_", metavar="ID", help="start at this case")
    p.add_argument("--stop-on-fail", action="store_true", help="stop at the first case that does not pass")
    # Also accepted after `run`, where people type it; SUPPRESS keeps a value given before the command.
    p.add_argument("--device", "-s", default=argparse.SUPPRESS, help="device serial to run the plan on")

    p = add("replay", cmd_replay, "re-run a recorded run on a device and re-check every verification")
    p.add_argument("run")
    p.add_argument("--fresh", action="store_true", help="clear app data before replaying")
    p.add_argument("--visual", action="store_true", help="also compare screenshots with Claude")
    p.add_argument("--step-timeout", type=float, default=10, help="seconds to wait for each element")
    return ap


def _bool(v: str) -> bool:
    if v.lower() in ("true", "yes", "1"):
        return True
    if v.lower() in ("false", "no", "0"):
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    try:
        active = get_active() if args.cmd in HELD else None
        if active and (held := _hold(active[0])) is not None:
            return held
        try:
            return args.fn(args) or 0
        finally:
            # Shown, not consumed: the next device command is still held until the agent re-plans.
            if get_active():
                for m in live.unread():
                    print(f">> USER (dashboard, pending): {m['text']}")
    except NotFound as exc:
        print(str(exc))
        return EXIT_FAIL
    except (UserError, AdbError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


def _hold(run: Run) -> int | None:
    """Stop a device command when the user paused the run or left a message it has not seen."""
    if live.paused():
        print(f"|| paused from the dashboard; waiting up to {int(live.PAUSE_WAIT)}s for resume")
        began = time.monotonic()
        resumed = live.wait_resume()
        run.log({"type": "hold", "reason": "paused", "resumed": resumed,
                 "waited_ms": round((time.monotonic() - began) * 1000)})
        if not resumed:
            told = live.deliver(run.log)
            print("PAUSED: the user paused this run. Command NOT run. "
                  "Run `./tnazar wait-resume` until it prints `resumed`, then continue.")
            return live.EXIT_HELD if told else live.EXIT_PAUSED
        print("resumed")
    live.wait_typing()
    if live.needs_agent(live.deliver(run.log)):
        print("Command NOT run: read the user's message above and decide the next step "
              "(re-run the command if it still applies).")
        return live.EXIT_HELD
    return None


if __name__ == "__main__":
    sys.exit(main())
