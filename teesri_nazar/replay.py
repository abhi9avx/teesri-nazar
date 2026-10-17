"""Replay a recorded run: re-find every element by selector and re-run every UI check.

The replay is itself a run (`<name>-replay-<stamp>`), so it gets the same report.
"""

from __future__ import annotations

import os
import subprocess
import time

from . import device as dev
from . import report as rpt
from . import screen as scr
from . import visual
from .adb import Adb, adb_binary, pick_serial
from .cli import Ctx, _end_case, evidence_shot
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
        dev.launch(adb, e["package"])
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
            dev.clear_field(adb, 40)
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
        dev.swipe(adb, x1, y1, x2, y2, e.get("ms", 350))
    elif t == "scroll":
        area = (0, int(size[1] * 0.15), size[0], int(size[1] * 0.85))
        vec = dev.scroll_vector(e["direction"], area)
        if e.get("to"):
            sel = scr.Selector.from_dict(e["to"])
            for _ in range(10):
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
        ok, _, _ = dev.wait_for(adb, sel, max(e.get("timeout", 15), timeout), gone=e.get("gone", False))
        if not ok and e.get("ok", True):
            raise StepFailed(f"wait: {sel} never became {'gone' if e.get('gone') else 'visible'}")
    elif t == "adb":
        subprocess.run([adb_binary(), "-s", adb.serial, *e["args"]], capture_output=True)


def _recheck(ctx: Ctx, e: dict, timeout: float) -> None:
    if e.get("kind", "ui") != "ui":
        ctx.log({"type": "note", "kind": "observation",
                 "text": f"[{e['kind']}] not re-verified on replay: {e['what']}"})
        return
    sel = scr.Selector.from_dict(e["selector"])
    ok, _, el = dev.wait_for(ctx.adb, sel, max(timeout, 5), gone=e.get("gone", False))
    if ok and el is not None:
        for flag, actual in (("checked", el.checked), ("enabled", el.enabled)):
            if e.get(flag) is not None and e[flag] != actual:
                ok = False
    event = {"type": "check", "kind": "ui", "ok": ok, "what": e["what"], "selector": e["selector"],
             "gone": e.get("gone", False), "recorded_ok": e["ok"]}
    if shot := evidence_shot(ctx, f"check-{'pass' if ok else 'fail'}"):
        event["shot"] = shot
    ctx.log(event)


def run(args) -> int:
    source = Run.load(args.run)
    if get_active():
        print("error: a run is active; `tnazar stop` it before replaying")
        return 2
    smeta = source.meta()
    adb = Adb(pick_serial(args.device))
    meta = {"replay_of": source.name, "device": adb.serial, "prompt": smeta.get("prompt", ""),
            "package": smeta.get("package", ""), "model": "", "fresh": args.fresh,
            "android": adb.shell("getprop", "ro.build.version.release").strip()}
    if meta["package"]:
        meta["app_version"] = dev.app_version(adb, meta["package"])
        if smeta.get("app_version") and smeta["app_version"] != meta["app_version"]:
            print(f"note: recorded on {smeta['app_version']}, replaying on {meta['app_version']}")
    meta["animations_before"] = dev.animations_off(adb)
    target = Run.create(f"{source.name}-replay-{stamp()}", meta)
    ctx = Ctx(adb, target)
    print(f"replaying {source.name} on {adb.serial} -> {target.name}")

    if args.fresh and meta["package"]:
        dev.force_stop(adb, meta["package"])
        dev.clear_data(adb, meta["package"])

    size = adb.screen_size()
    skipping = False
    pairs: list[tuple[str, str]] = []
    step = 0
    try:
        for e in source.events():
            t = e["type"]
            if t == "case_start":
                if target.current_case():
                    _end_case(ctx, None, None)
                skipping = False
                dev.logcat_clear(adb)
                ctx.log({"type": "case_start", "case": e["case"], "title": e.get("title", "")})
                print(f"case {e['case']} {e.get('title', '')}".rstrip())
                continue
            if t == "case_end":
                if target.current_case():
                    status = _end_case(ctx, "blocked" if skipping else None,
                                       "a step failed; rest of the case skipped" if skipping else None)
                    print(f"  -> {status.upper()}")
                skipping = False
                continue
            if skipping or t in ("note",):
                continue
            if t == "check":
                _recheck(ctx, e, args.step_timeout)
                last = target.events()[-1]
                if last["type"] == "check":
                    print(f"  {'PASS' if last['ok'] else 'FAIL'} {last['what']}")
                    if args.visual and e.get("shot") and last.get("shot"):
                        pairs.append((str(source.path / e["shot"]), str(target.path / last["shot"])))
                continue
            if t == "shot":
                path = dev.screenshot(adb, target.artifact("shots", e.get("label") or "shot", "png"))
                ctx.log({"type": "shot", "label": e.get("label", ""), "file": target.rel(path)})
                if args.visual:
                    pairs.append((str(source.path / e["file"]), str(path)))
                continue
            step += 1
            try:
                _do(ctx, e, size, args.step_timeout)
                time.sleep(SETTLE)
            except StepFailed as exc:
                print(f"  step {step} failed: {exc}")
                ctx.log({"type": "check", "kind": "ui", "ok": False, "what": f"step {step}: {exc}"})
                skipping = bool(target.current_case())
        if target.current_case():
            _end_case(ctx, None, None)
        if pairs:
            print(f"visual: comparing {len(pairs)} screenshot pair(s) with Claude...")
            for res in visual.compare(pairs):
                ctx.log({"type": "check", "kind": "other", "ok": res["ok"],
                         "what": f"visual match: {os.path.basename(res['golden'])}",
                         "detail": res["reasoning"], "case": None})
    finally:
        dev.animations_restore(adb, meta["animations_before"])
        target.update_meta(finished_at=now_iso())
        paths = rpt.write(target)

    summary = rpt.summarize(target)
    print(rpt.summary_line(summary))
    for p in paths:
        print(f"report: {p}")
    failed = summary["counts"].get("fail", 0) + summary["counts"].get("blocked", 0) + summary["checks_failed"]
    return 0 if failed == 0 else 1

