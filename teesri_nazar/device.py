"""Device actions. Pure device I/O; recording happens in the CLI layer."""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import screen as scr
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


def type_text(adb: Adb, text: str) -> None:
    if text:
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


def launcher_activity(adb: Adb, package: str) -> str | None:
    out = adb.shell("cmd", "package", "resolve-activity", "--brief",
                    "-a", "android.intent.action.MAIN",
                    "-c", "android.intent.category.LAUNCHER", package, check=False)
    lines = [ln.strip() for ln in out.splitlines() if "/" in ln]
    return lines[-1] if lines else None


def force_stop(adb: Adb, package: str) -> None:
    adb.shell("am", "force-stop", package)


def clear_data(adb: Adb, package: str) -> None:
    out = adb.shell("pm", "clear", package, check=False)
    if "Success" not in out:
        raise AdbError(f"pm clear {package} failed: {out.strip()}")


def launch(adb: Adb, package: str) -> str:
    component = launcher_activity(adb, package)
    if component:
        out = adb.shell("am", "start", "-W", "-n", component, check=False)
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


def wait_for(adb: Adb, sel: scr.Selector, timeout: float, gone: bool = False,
             interval: float = 0.4) -> tuple[bool, scr.Screen, scr.Element | None]:
    deadline = time.monotonic() + timeout
    screen = el = None
    while True:
        try:
            screen = scr.capture(adb)
        except AdbError:
            # Animating screens fail the dump transiently; only a dump that never works is an error.
            if time.monotonic() >= deadline and screen is None:
                raise
        else:
            el = scr.find(screen, sel)
            if (el is None) == gone:
                return True, screen, el
        if time.monotonic() >= deadline:
            return False, screen, el
        time.sleep(interval)
