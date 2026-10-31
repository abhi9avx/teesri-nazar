"""Replay a recorded run: re-find every element by selector and re-run every UI check.

The replay is itself a run (`<name>-replay-<stamp>`), so it gets the same report.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from . import commands
from . import coverage
from . import device as dev
from . import uia2
from . import report as rpt
from . import screen as scr
from . import visual
from .adb import Adb, AdbError, adb_binary, pick_serial
from .cli import (EXIT_ERROR, EXIT_FAIL, Ctx, _end_case, check_device_owner, close_run,
                  device_owner_of, evidence_shot, open_case_crashed)
from .errors import UserError
from .session import Run, get_active, now_iso, stamp

SETTLE = 0.3


class StepFailed(Exception):
    pass




def _scale(xy: list[int], recorded: list[int] | None, now: tuple[int, int]) -> tuple[int, int]:
    if not recorded or not recorded[0]:
        return xy[0], xy[1]
    return round(xy[0] * now[0] / recorded[0]), round(xy[1] * now[1] / recorded[1])


def _locate(ctx: Ctx, e: dict, size: tuple[int, int], timeout: float) -> tuple[int, int]:
    if e.get("selector"):
        sel = scr.Selector.from_dict(e["selector"])
        ok, _, el = dev.wait_for(ctx.adb, sel, timeout)
        if not ok or el is None:
            raise StepFailed(f"{e['type']}: could not find {sel} within {timeout}s")
        return el.center
    if e.get("xy"):
        return _scale(e["xy"], e.get("screen"), size)
    raise StepFailed(f"{e['type']}: no selector or coordinates recorded")


def _do(ctx: Ctx, e: dict, size: tuple[int, int], timeout: float) -> None:
    t = e["type"]
    adb = ctx.adb
    if t == "launch":
        if e.get("fresh"):
            dev.force_stop(adb, e["package"])
            dev.clear_data(adb, e["package"])
        elif e.get("restart"):
            dev.force_stop(adb, e["package"])
        dev.launch(adb, e["package"], clean=bool(e.get("fresh") or e.get("restart")))
    elif t == "open_url":
        dev.open_url(adb, e["url"])
    elif t == "tap":
        x, y = _locate(ctx, e, size, timeout)
        (dev.long_press if e.get("long") else dev.tap)(adb, x, y)
    elif t == "type":
        if e.get("text") is not None:
            text = e["text"]
        elif e.get("env") and e["env"] in os.environ:
            text = os.environ[e["env"]]
        else:
            hint = f"set ${e['env']}" if e.get("env") else "record it with --env VAR"
            raise StepFailed(f"type: secret value was not recorded ({hint})")
        if e.get("selector") or e.get("xy"):
            dev.tap(adb, *_locate(ctx, e, size, timeout))
        if e.get("clear"):
            dev.clear_field(adb, dev.CLEAR_KEYS)
        dev.type_text(adb, text)
        if e.get("enter"):
            dev.key(adb, "enter")
    elif t == "key":
        if dev.is_back(e["key"]) and e.get("keyboard") is not None:
            now = dev.keyboard_shown(adb)
            if e["keyboard"] and now is False:
                print("  back skipped: it only closed the keyboard, which is already hidden")
                return
            if not e["keyboard"] and now:
                print("  extra back: closing the keyboard so this back navigates as recorded")
                dev.key(adb, "back")
        dev.key(adb, e["key"])
    elif t == "swipe":
        x1, y1 = _scale(e["from"], e.get("screen"), size)
        x2, y2 = _scale(e["to"], e.get("screen"), size)
        dev.swipe(adb, x1, y1, x2, y2, e.get("ms", dev.SWIPE_MS))
    elif t == "scroll":
        sideways = e["direction"] in ("left", "right")
        vec = dev.scroll_vector(e["direction"], dev.scroll_area(scr.capture(adb) if sideways else None,
                                                                e["direction"], size))
        if e.get("to"):
            sel = scr.Selector.from_dict(e["to"])
            for _ in range(dev.SCROLL_MAX):
                if scr.find(scr.capture(adb), sel):
                    return
                dev.swipe(adb, *vec)
            if e.get("found") and not scr.find(scr.capture(adb), sel):
                raise StepFailed(f"scroll: {sel} not found")
        else:
            for _ in range(e.get("times", 1)):
                dev.swipe(adb, *vec)
    elif t == "wait":
        sel = scr.Selector.from_dict(e["selector"])
        alts = [scr.Selector.from_dict(a) for a in e.get("alternatives", [])]
        stats: dict = {}
        ok, _, _ = dev.wait_for(adb, sel, max(e.get("timeout", 15), timeout), gone=e.get("gone", False),
                                stats=stats, alternatives=alts)
        if "alternative" in stats:
            # Named in the reason, but the case is not failed: the plan may have met it inside a
            # `retry` and passed on a later attempt, and a replay cannot retry.
            raise StepFailed(f"wait: {alts[stats['alternative']]} showed instead of {sel}")
        if not ok and e.get("ok", True):
            raise StepFailed(f"wait: {sel} never became {'gone' if e.get('gone') else 'visible'}")
    elif t == "wait_online":
        if e.get("ok", True) and not dev.wait_online(adb, max(e.get("timeout", 30), timeout)):
            raise StepFailed("wait-online: the device did not come back online")
    elif t == "adb":
        subprocess.run([adb_binary(), "-s", adb.serial, *e["args"]], capture_output=True)


def _recheck(ctx: Ctx, e: dict, timeout: float) -> None:
    kind = e.get("kind", "ui")
    # A manual `check --result` and a plan runner's failed step are ui checks with no selector.
    if kind != "ui" or not e.get("selector"):
        ctx.log({"type": "note", "kind": "observation",
                 "text": f"[{kind}] not re-verified on replay: {e['what']}"})
        return
    sel = scr.Selector.from_dict(e["selector"])
    ok, screen, el = dev.wait_for(ctx.adb, sel, max(timeout, 5), gone=e.get("gone", False), exact=True)
    if ok and el is not None:
        for flag, actual in (("checked", el.checked), ("enabled", el.enabled)):
            if e.get(flag) is not None and e[flag] != actual:
                ok = False
    event = {"type": "check", "kind": "ui", "ok": ok, "what": e["what"], "selector": e["selector"],
             "gone": e.get("gone", False), "recorded_ok": e["ok"], **({"for": e["for"]} if e.get("for") else {}),
             **({"setup": True} if e.get("setup") else {})}
    if not ok and not e.get("gone") and el is None and (screen is None or len(screen.elements) <= 1):
        event["blank_screen"] = True  # as on a live run: nothing was read beyond the window root
    if shot := evidence_shot(ctx, f"check-{'pass' if ok else 'fail'}"):
        event["shot"] = shot
    ctx.log(event)


def run(args) -> int:
    source = Run.load(args.run)
    if get_active():
        print("error: a run is active; `tnazar stop` it before replaying", file=sys.stderr)
        return EXIT_ERROR
    smeta = source.meta()
    adb = Adb(pick_serial(args.device))
    try:
        took_over = check_device_owner(adb, adb.serial, False)  # another home's live run: not ours to drive
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if took_over:
        print(took_over)
    meta = {"replay_of": source.name, "device": adb.serial, "prompt": smeta.get("prompt", ""),
            "package": smeta.get("package", ""), "model": "", "fresh": args.fresh,
            "android": adb.shell("getprop", "ro.build.version.release").strip()}
    if meta["package"]:
        meta["app_version"] = dev.app_version(adb, meta["package"])
        if smeta.get("app_version") and smeta["app_version"] != meta["app_version"]:
            print(f"note: recorded on {smeta['app_version']}, replaying on {meta['app_version']}")
    meta["animations_before"] = None
    target = Run.create(f"{source.name}-replay-{stamp()}", meta)
    owner = device_owner_of(target)
    try:
        dev.claim_device(adb, owner)
    except AdbError as exc:
        print(f"warning: could not mark the device as in use: {exc}", file=sys.stderr)
    ctx = Ctx(adb, target)
    print(f"replaying {source.name} on {adb.serial} -> {target.name}")
    skipping = ""
    pairs: list[tuple[str, str]] = []
    step = 0
    try:
        meta["animations_before"] = dev.animations_off(adb)
        target.update_meta(animations_before=meta["animations_before"])
        print(uia2.start_line(adb))
        if args.fresh and meta["package"]:
            dev.force_stop(adb, meta["package"])
            dev.clear_data(adb, meta["package"])
        size = adb.screen_size()
        for e in source.events():  # the same requested cases, so one that never ran is still a row
            if e["type"] == "case_plan":
                target.log({k: v for k, v in e.items() if k != "t"})
        for e in source.events():
            t = e["type"]
            if t == "case_start":
                if target.current_case():
                    _end_case(ctx, None, None)
                skipping = ""
                dev.logcat_clear(adb)
                ctx.log({"type": "case_start", "case": e["case"], "title": e.get("title", "")})
                print(f"case {e['case']} {e.get('title', '')}".rstrip())
                continue
            if t == "case_end":
                if target.current_case():
                    # The judgment decides: a step that failed blocks the case, unless the app crashed or a
                    # check failed first, which fails it.
                    status = _end_case(ctx, None, f"{skipping}; rest of the case skipped" if skipping else None)
                    print(f"  -> {status.upper()}")
                skipping = ""
                continue
            if skipping or t in commands.NARRATION_EVENTS:
                continue
            if t == "check":
                _recheck(ctx, e, args.step_timeout)
                last = target.events()[-1]
                if last["type"] == "check":
                    print(f"  {'PASS' if last['ok'] else 'FAIL'} {last['what']}")
                    if last.get("setup") and not last["ok"] and target.current_case():
                        skipping = "the setup did not hold"  # the case stops here
                    if args.visual and e.get("shot") and last.get("shot"):
                        pairs.append((str(source.path / e["shot"]), str(target.path / last["shot"])))
                continue
            if t == "shot":
                path = dev.screenshot(adb, target.artifact("shots", e.get("label") or "shot", "png"))
                ctx.log({"type": "shot", "label": e.get("label", ""), "file": target.rel(path)})
                if args.visual:
                    pairs.append((str(source.path / e["file"]), str(path)))
                continue
            if e.get("ok") is False:
                continue  # a step that did not run when it was recorded
            if e.get("by") == "dashboard" and e.get("env") not in os.environ:
                print(f"  skipped: {e['env']} was typed live from the dashboard (set ${e['env']} to replay it)")
                continue
            step += 1
            try:
                _do(ctx, e, size, args.step_timeout)
                time.sleep(SETTLE)
            except StepFailed as exc:
                print(f"  step {step} failed: {exc}")
                ctx.log({"type": "check", "kind": "ui", "ok": False, "what": f"step {step}: {exc}",
                         "step": {"cmd": e["type"], "code": EXIT_FAIL, "error": str(exc)}})
                if target.current_case():
                    skipping = "a step failed"
        if target.current_case():
            _end_case(ctx, None, None)
        if pairs:
            print(f"visual: comparing {len(pairs)} screenshot pair(s) with Claude...")
            for res in visual.compare(pairs):
                ctx.log({"type": "check", "kind": "other", "ok": res["ok"],
                         "what": f"visual match: {os.path.basename(res['golden'])}",
                         "detail": res["reasoning"], "case": None})
    finally:
        close_run(target, "replay stopped before the case finished", "not run in the recorded run",
                  crash=open_case_crashed(target, adb))
        try:
            dev.release_device(adb, owner)
        except AdbError as exc:  # never at the cost of restoring the device or writing the report
            print(f"warning: could not clear the device's in-use mark: {exc}", file=sys.stderr)
        uia2.stop(adb)
        if meta["animations_before"] is not None:
            dev.animations_restore(adb, meta["animations_before"])
        target.update_meta(finished_at=now_iso())
        paths = rpt.write(target)

    summary = rpt.summarize(target)
    print(rpt.summary_line(summary))
    for p in paths:
        print(f"report: {p}")
    failed = (summary["counts"].get("fail", 0) + summary["counts"].get("blocked", 0)
              + summary["counts"].get("not_run", 0) + summary["checks_failed"])
    return 0 if failed == 0 else EXIT_FAIL

