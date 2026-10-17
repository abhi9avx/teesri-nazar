"""Device actions. Pure device I/O; recording happens in the CLI layer."""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import screen as scr
from . import uia2
from .adb import Adb, AdbError

KEYS = {
    "back": 4, "home": 3, "enter": 66, "tab": 61, "del": 67, "backspace": 67,
    "delete": 112, "menu": 82, "search": 84, "escape": 111, "recents": 187,
    "up": 19, "down": 20, "left": 21, "right": 22, "space": 62,
    "volume_up": 24, "volume_down": 25, "power": 26,
}
KEY_MOVE_END = 123

_SAFE = re.compile(r"[A-Za-z0-9_.,:/@+=-]")
ANIMATION_SETTINGS = ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")
# The user's real animation settings live on the device until restored, so a run that never got to
# restore them (device lost, host killed, another TNAZAR_HOME) cannot leave 0 as the next baseline.
ANIMATION_BASELINE = "/data/local/tmp/tnazar-animations"


def escape_input_text(text: str) -> str:
    """Escape text for `input text`, which runs through the device shell and maps %s to space."""
    if not text.isascii():
        raise ValueError("adb `input text` only supports ASCII; non-ASCII text cannot be typed")
    out = []
    for ch in text:
        if ch == " ":
            out.append("%s")
        elif _SAFE.fullmatch(ch):
            out.append(ch)
        else:
            out.append("\\" + ch)
    return "".join(out)


def tap(adb: Adb, x: int, y: int) -> None:
    adb.shell("input", "tap", str(x), str(y))


def long_press(adb: Adb, x: int, y: int, ms: int = 800) -> None:
    adb.shell("input", "swipe", str(x), str(y), str(x), str(y), str(ms))


CODE_MAX = 8  # OTPs and PINs; a 10-digit phone number still goes in one burst
KEY_GAP = 0.2


def type_text(adb: Adb, text: str) -> None:
    if not text:
        return
    if text.isdigit() and len(text) <= CODE_MAX:
        # Segmented PIN/OTP boxes drop digits that arrive in a single `input text` burst.
        for i, digit in enumerate(text):
            if i:
                time.sleep(KEY_GAP)
            adb.shell("input", "text", digit)
        return
    adb.shell("input", "text", escape_input_text(text))


def clear_field(adb: Adb, length: int) -> None:
    codes = [str(KEY_MOVE_END)] + ["67"] * max(length, 1)
    adb.shell("input", "keyevent", *codes)


def key(adb: Adb, name: str) -> int:
    code = int(name) if name.isdigit() else KEYS.get(name.lower())
    if code is None:
        raise ValueError(f"unknown key {name!r}; use one of {', '.join(sorted(KEYS))} or a keycode")
    adb.shell("input", "keyevent", str(code))
    return code


def is_back(name: str) -> bool:
    return name.lower() == "back" or name == str(KEYS["back"])


def swipe(adb: Adb, x1: int, y1: int, x2: int, y2: int, ms: int = 350) -> None:
    adb.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))


def scroll_vector(direction: str, area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Finger path that moves content in `direction` ("down" reveals what is below)."""
    l, t, r, b = area
    cx, cy = (l + r) // 2, (t + b) // 2
    dy, dx = int((b - t) * 0.30), int((r - l) * 0.30)
    vectors = {
        "down": (cx, cy + dy, cx, cy - dy),
        "up": (cx, cy - dy, cx, cy + dy),
        "right": (cx + dx, cy, cx - dx, cy),
        "left": (cx - dx, cy, cx + dx, cy),
    }
    if direction not in vectors:
        raise ValueError("direction must be up, down, left or right")
    return vectors[direction]


def screenshot(adb: Adb, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = adb.exec_out("screencap -p", timeout=30)
    if not data.startswith(b"\x89PNG"):
        raise AdbError("screencap did not return a PNG")
    dest.write_bytes(data)
    return dest


DEBUG_LAUNCHERS = ("leakcanary", "chucker", "flipper")


def launcher_activity(adb: Adb, package: str) -> str | None:
    intent = ["-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package]
    out = adb.shell("cmd", "package", "resolve-activity", "--brief", *intent, check=False)
    lines = [ln.strip() for ln in out.splitlines() if "/" in ln]
    if lines and lines[-1].startswith(f"{package}/"):
        return lines[-1]
    # Several launcher entries (debug builds add LeakCanary & co.) resolve to the system chooser.
    out = adb.shell("cmd", "package", "query-activities", "--brief", *intent, check=False)
    found = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(f"{package}/")]
    found.sort(key=lambda c: any(tool in c.lower() for tool in DEBUG_LAUNCHERS))
    return found[0] if found else None


def force_stop(adb: Adb, package: str) -> None:
    adb.shell("am", "force-stop", package)


def clear_data(adb: Adb, package: str) -> None:
    out = adb.shell("pm", "clear", package, check=False)
    if "Success" not in out:
        raise AdbError(f"pm clear {package} failed: {out.strip()}")


FLAG_NEW_CLEAR_TASK = "0x10008000"


def launch(adb: Adb, package: str, clean: bool = False) -> str:
    """Start the app; `clean` also drops activities left in its task, including other packages' ones."""
    component = launcher_activity(adb, package)
    if component:
        flags = ["-f", FLAG_NEW_CLEAR_TASK] if clean else []
        out = adb.shell("am", "start", "-W", *flags, "-n", component, check=False)
        if "Error" not in out:
            return component
    out = adb.shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    if "No activities found" in out or "Error" in out:
        raise AdbError(f"could not launch {package}: is it installed?")
    return package


def open_url(adb: Adb, url: str) -> None:
    adb.shell("am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", f"'{url}'")


def installed_packages(adb: Adb, keyword: str = "") -> list[str]:
    out = adb.shell("pm", "list", "packages")
    pkgs = sorted(ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:"))
    return [p for p in pkgs if keyword.lower() in p.lower()]


def app_version(adb: Adb, package: str) -> str:
    out = adb.shell("dumpsys", "package", package, check=False)
    m = re.search(r"versionName=(\S+)", out)
    code = re.search(r"versionCode=(\d+)", out)
    if not m:
        return ""
    return m.group(1) + (f" ({code.group(1)})" if code else "")


def keyboard_shown(adb: Adb) -> bool | None:
    """Whether the soft keyboard is up; None when this Android version does not say."""
    m = re.search(r"mInputShown=(true|false)", adb.shell("dumpsys", "input_method", check=False))
    return m.group(1) == "true" if m else None


def foreground_package(adb: Adb) -> str:
    out = adb.shell("dumpsys", "activity", "activities", check=False)
    m = re.search(r"(?:mResumedActivity|topResumedActivity|ResumedActivity).*?\s([\w.]+)/", out)
    return m.group(1) if m else ""


def get_animations(adb: Adb) -> dict[str, str]:
    return {k: adb.shell("settings", "get", "global", k).strip() for k in ANIMATION_SETTINGS}


def set_animations(adb: Adb, values: dict[str, str]) -> None:
    for k, v in values.items():
        if v in ("", "null"):
            adb.shell("settings", "delete", "global", k, check=False)
        else:
            adb.shell("settings", "put", "global", k, v)


def animations_off(adb: Adb) -> dict[str, str]:
    """Turn animations off; returns the user's real settings, preferring one saved by an unrestored run."""
    before = _saved_baseline(adb)
    if before is None:
        before = get_animations(adb)
        adb.shell("echo", ",".join(f"{k}={v}" for k, v in before.items()), ">", ANIMATION_BASELINE)
    set_animations(adb, {k: "0" for k in ANIMATION_SETTINGS})
    return before


def animations_restore(adb: Adb, before: dict[str, str]) -> None:
    set_animations(adb, _saved_baseline(adb) or before)
    adb.shell("rm", "-f", ANIMATION_BASELINE, check=False)


def _saved_baseline(adb: Adb) -> dict[str, str] | None:
    raw = adb.shell("cat", ANIMATION_BASELINE, check=False).strip()
    pairs = dict(p.split("=", 1) for p in raw.split(",") if "=" in p)
    return pairs if set(pairs) == set(ANIMATION_SETTINGS) else None


def logcat_clear(adb: Adb) -> None:
    adb.run(["logcat", "-b", "main,system,crash", "-c"], check=False)


def logcat_dump(adb: Adb, lines: int = 3000) -> str:
    return adb.run(["logcat", "-d", "-v", "threadtime", "-t", str(lines)], check=False).stdout


def crashes(adb: Adb, package: str | None = None) -> str:
    """Crash buffer plus ANR lines since the last clear, optionally limited to one app."""
    out = adb.run(["logcat", "-d", "-b", "crash", "-v", "threadtime"], check=False).stdout
    anr = adb.run(["logcat", "-d", "-b", "system", "-v", "threadtime", "-e", "ANR in"], check=False).stdout
    text = (out + anr).strip()
    if package and package not in text:
        return ""
    return text


def wait_online(adb: Adb, timeout: float, host: str = "8.8.8.8") -> bool:
    """True once the device can reach `host` (after airplane mode, Wi-Fi takes a while to return)."""
    deadline = time.monotonic() + timeout
    while adb.run(["shell", "ping", "-c", "1", "-W", "1", host], check=False).returncode != 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def wait_for(adb: Adb, sel: scr.Selector, timeout: float, gone: bool = False,
             interval: float | None = None, stats: dict | None = None,
             alternatives: list[scr.Selector] | None = None) -> tuple[bool, scr.Screen, scr.Element | None]:
    """Poll until `sel` appears (or is gone). If one of `alternatives` shows up first, stop at once:
    ok is False and stats["alternative"] says which one (e.g. an error instead of the next screen)."""
    if interval is None:
        interval = 0.15 if uia2.active(adb) else 0.4  # a fast read makes frequent polling cheap
    deadline = time.monotonic() + timeout
    screen = el = None
    stats = {} if stats is None else stats
    stats["polls"] = 0
    while True:
        stats["polls"] += 1
        try:
            screen = scr.capture(adb)
        except AdbError:
            # Animating screens fail the dump transiently; only a dump that never works is an error.
            if time.monotonic() >= deadline and screen is None:
                raise
        else:
            el = scr.find(screen, sel, strict=gone)
            if (el is None) == gone:
                return True, screen, el
            for i, alt in enumerate(alternatives or []):
                if (found := scr.find(screen, alt)) is not None:
                    stats["alternative"] = i
                    return False, screen, found
        if time.monotonic() >= deadline:
            return False, screen, el
        time.sleep(interval)
