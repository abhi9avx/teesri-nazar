"""`tnazar doctor`: check the setup a run needs, and say how to fix what is wrong.

Read-only: it lists, asks and reads, and never installs, starts or changes anything on the
device or the machine, so it is safe while a run is in progress. Fixes are printed for the
user (or the agent) to run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from . import uia2
from .adb import RESTART_ADB, Adb, AdbError, adb_binary, adb_version, list_devices, state_fix

MIN_PYTHON = (3, 9)
DEVICE_TIMEOUT = 10  # each question to a device; a hung device fails its check, never hangs doctor
# Common reasons a plugged-in phone is missing from `adb devices`; vendors name the USB modes differently.
PLUGGED_IN_NOT_LISTED = ("if a phone is plugged in but not listed, the usual causes: USB debugging is off"
                         " (Settings > Developer options), the cable only charges (use a cable that carries data),"
                         " or the phone's USB mode blocks data (pick File transfer / MTP in its USB notification)")
STATUS_WIDTH = 6  # "OK    ", "WARN  ", "FAIL  ", "SKIP  "


@dataclass
class Check:
    status: str  # ok | warn | fail | skip
    name: str
    detail: str
    fix: list[str] = field(default_factory=list)
    device: str = ""  # the serial a per-device check is about; "" for the machine
    kind: str = ""  # "device": the check that says whether that device is ready


def run_checks(python=None, executable: str | None = None, find_adb: Callable[[], str] = adb_binary,
               adb_version: Callable[[str], str] = adb_version, devices: Callable[[], list] = list_devices,
               adb_for: Callable[[str], Adb] = Adb, only: str | None = None,
               environ=None) -> list[Check]:
    python = tuple(python or sys.version_info[:3])
    environ = os.environ if environ is None else environ
    checks = [_python(python, executable or sys.executable)]
    sdk_without_adb = [f"{name}={environ[name]} has no adb" for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT")
                       if environ.get(name)
                       and not os.access(os.path.join(environ[name], "platform-tools", "adb"), os.X_OK)]
    adb = _adb(find_adb, adb_version, sdk_without_adb)
    checks.append(adb)
    if adb.status == "fail":
        return checks + [Check("skip", "devices", "skipped: adb is needed to list devices")]
    try:
        listed = devices()
    except (AdbError, OSError) as exc:
        return checks + [Check("fail", "devices", str(exc), [RESTART_ADB])]
    if not listed:
        return checks + [Check("fail", "devices", "no device connected (adb devices is empty)",
                               ["plug in a phone or start an emulator", PLUGGED_IN_NOT_LISTED])]
    if only:
        if only not in [d.serial for d in listed]:
            return checks + [Check("fail", f"device {only}", f"{only} is not listed by adb devices",
                                   [PLUGGED_IN_NOT_LISTED,
                                    f"or a typo: adb lists {', '.join(d.serial for d in listed)}"],
                                   device=only)]
        listed = [d for d in listed if d.serial == only]
    for d in listed:
        checks += _device(d, adb_for)
    ready = [d.serial for d in listed if d.state == "device"]
    if len(ready) > 1:
        checks.append(Check("warn", "devices", f"{len(ready)} ready devices: {', '.join(ready)}",
                            ["pass --device <serial> or set ANDROID_SERIAL to choose one"]))
    return checks


def _python(version: tuple, executable: str) -> Check:
    shown = ".".join(str(n) for n in version)
    if version[:2] >= MIN_PYTHON:
        return Check("ok", "python", f"{shown} ({executable})")
    need = ".".join(str(n) for n in MIN_PYTHON)
    return Check("fail", "python", f"{shown} ({executable}) is older than {need}",
                 [f"install Python {need} or newer and run tnazar with it (e.g. python3.11 ./tnazar doctor),"
                  " or put it first on PATH"])


def _adb(find_adb, adb_version, sdk_without_adb: list[str]) -> Check:
    try:
        path = find_adb()
    except AdbError as exc:
        fix = ["install Android platform-tools (developer.android.com/tools/releases/platform-tools)"
               " and put adb on PATH, or set ANDROID_HOME to the SDK folder"]
        if sdk_without_adb:
            fix.append(f"{', '.join(sdk_without_adb)} (no platform-tools/adb inside): point it at the SDK folder")
        return Check("fail", "adb", str(exc), fix)
    try:
        version = adb_version(path)
    except AdbError as exc:
        return Check("fail", "adb", f"{path}: {exc}", [RESTART_ADB])
    except OSError as exc:
        return Check("fail", "adb", f"{path} does not run: {exc.strerror or exc}",
                     ["reinstall Android platform-tools, or point ANDROID_HOME at a working SDK"])
    detail = f"{path} ({version})"
    if sdk_without_adb:
        detail += f"; {', '.join(sdk_without_adb)}, so this one from PATH is used"
    return Check("ok", "adb", detail)


def _device(d, adb_for) -> list[Check]:
    if d.state != "device":
        return [Check("fail", f"device {d.serial}", d.state, [state_fix(d.state)], device=d.serial, kind="device")]
    adb = adb_for(d.serial)
    try:
        booted = adb.shell("getprop", "sys.boot_completed", timeout=DEVICE_TIMEOUT).strip() == "1"
    except (AdbError, OSError) as exc:
        return [Check("fail", f"device {d.serial}", f"listed but not answering: {exc}",
                      [f"re-plug the device, or {RESTART_ADB}"], device=d.serial, kind="device")]
    if not booted:
        # adb says `device` before Android has started: pm and wm would fail, and look like other problems.
        return [Check("fail", f"device {d.serial}", "still booting",
                      ["wait for it to finish booting (the home screen shows), then run ./tnazar doctor again"],
                      device=d.serial, kind="device")]
    checks = [Check("ok", f"device {d.serial}", f"ready ({d.model})" if d.model else "ready", device=d.serial,
                    kind="device")]
    try:
        w, h = adb.screen_size(timeout=DEVICE_TIMEOUT)
        checks.append(Check("ok", f"{d.serial} screen", f"{w}x{h}", device=d.serial))
    except (AdbError, OSError, ValueError) as exc:
        # Percentage taps, scroll and swipe are computed from the size: the device cannot run.
        checks.append(Check("fail", f"{d.serial} screen", f"size unreadable: {exc}",
                            [f"unlock the device and run: adb -s {d.serial} shell wm size;"
                             " if it prints no size, report it with that output"], device=d.serial))
        return checks
    # Installed, not started: starting takes up to 15 s and would take the UI automation
    # connection from a run in progress. `start` reports a server that does not answer.
    try:
        fast = uia2.installed(adb, timeout=DEVICE_TIMEOUT)
    except (AdbError, OSError) as exc:
        checks.append(Check("fail", f"{d.serial} screen reads", f"the device did not answer: {exc}",
                            [f"re-plug the device, or {RESTART_ADB}"], device=d.serial))
        return checks
    if fast:
        checks.append(Check("ok", f"{d.serial} screen reads", "fast (UiAutomator2 server installed)",
                            device=d.serial))
    else:
        cmds = uia2.install_hint(adb)
        fix = ["install the UiAutomator2 server once for ~0.05 s reads:", *cmds] if cmds else \
            [f"{uia2.GET_SERVER_HINT}, then run ./tnazar doctor again for the install commands"]
        checks.append(Check("warn", f"{d.serial} screen reads", "slow, uiautomator dump (~2 s per read)", fix,
                            device=d.serial))
    return checks


def usable(checks: list[Check]) -> list[str]:
    """Devices a run can start on: reported ready, and no blocker against them."""
    ready = [c.device for c in checks if c.kind == "device" and c.status == "ok"]
    blocked = {c.device for c in checks if c.status == "fail"}
    return [s for s in ready if s not in blocked]


def verdict(checks: list[Check]) -> tuple[int, str]:
    """(exit code, last line): 0 when a run can start on some device, 2 when nothing can run."""
    blockers = sum(c.status == "fail" for c in checks)
    warnings = sum(c.status == "warn" for c in checks)
    can = usable(checks)
    machine_ok = not any(c.status == "fail" and not c.device for c in checks)
    counts = ", ".join(f"{n} {word}{'s' if n != 1 else ''}"
                       for n, word in ((blockers, "blocker"), (warnings, "warning")) if n)
    if machine_ok and can:
        where = f"runs can start on {', '.join(can)}" + (" only" if blockers else "")
        return 0, f"{counts}: {where}" if counts else f"all good: {where}"
    if not counts:
        return 2, "no run can start: no device was checked"
    return 2, f"{counts}: no run can start until the blockers above are fixed"


def render(checks: list[Check]) -> str:
    """One line per check, its fix indented under it, and the verdict last. Plain text: the agent reads it."""
    lines = []
    for c in checks:
        lines.append(f"{c.status.upper():<{STATUS_WIDTH}}{c.name}: {c.detail}")
        for i, step in enumerate(c.fix):
            lines.append(f"        fix: {step}" if i == 0 else f"          {step}")
    lines.append(verdict(checks)[1])
    return "\n".join(lines)
