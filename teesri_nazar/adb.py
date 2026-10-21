"""Thin, synchronous access to one Android device over adb."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass


class AdbError(RuntimeError):
    pass


def adb_binary() -> str:
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env)
        if root:
            candidate = os.path.join(root, "platform-tools", "adb")
            if os.access(candidate, os.X_OK):
                return candidate
    found = shutil.which("adb")
    if not found:
        raise AdbError("adb not found: install platform-tools or set ANDROID_HOME")
    return found


@dataclass
class Device:
    serial: str
    state: str
    model: str = ""


DEVICES_TIMEOUT = 10  # the first call also starts the adb server
VERSION_TIMEOUT = 5


def adb_version(path: str) -> str:
    """The platform-tools version of the adb at `path` ("35.0.2-12147458"); OSError when it cannot run."""
    try:
        out = subprocess.run([path, "version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT).stdout
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb version did not answer within {VERSION_TIMEOUT}s") from exc
    found = re.search(r"^Version (\S+)", out, re.M) or re.search(r"version (\S+)", out)
    return found.group(1) if found else "unknown version"


def parse_devices(out: str) -> list[Device]:
    """`adb devices -l` output -> devices."""
    devices = []
    for line in out.splitlines():
        parts = line.split()
        # Skipped: the header, the server's "* daemon started" and "adb server version ... doesn't match".
        if len(parts) < 2 or line.startswith(("*", "List of devices", "adb server")):
            continue
        # Linux prints "no permissions (missing udev rules? ...); see [...]": a state of two words.
        state = "no permissions" if parts[1:3] == ["no", "permissions"] else parts[1]
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
        devices.append(Device(parts[0], state, model))
    return devices


def list_devices() -> list[Device]:
    try:
        out = subprocess.run([adb_binary(), "devices", "-l"], capture_output=True, text=True,
                             timeout=DEVICES_TIMEOUT).stdout
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb devices did not answer within {DEVICES_TIMEOUT}s") from exc
    return parse_devices(out)


DOCTOR_HINT = "run ./tnazar doctor for a full check"
RESTART_ADB = "restart adb: adb kill-server && adb start-server"
ACCEPT_PROMPT = 'accept the "Allow USB debugging" prompt on the phone; re-plug it if the prompt is gone'

# What to do about each state `adb devices` reports for a device that is not ready.
STATE_FIX = {
    "unauthorized": ACCEPT_PROMPT,
    "authorizing": ACCEPT_PROMPT,
    "offline": f"re-plug the device, or {RESTART_ADB}",
    "connecting": f"wait a few seconds; if it stays, re-plug the device or {RESTART_ADB}",
    "no permissions": "Linux: add a udev rule for the phone (see developer.android.com/studio/run/device), then re-plug",
    "recovery": "boot the device normally (it is in recovery)",
    "sideload": "boot the device normally (it is in sideload mode)",
    "bootloader": "boot the device normally (it is in the bootloader)",
}


def state_fix(state: str) -> str:
    return STATE_FIX.get(state, f"re-plug the device, or {RESTART_ADB}")


def pick_serial(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if os.environ.get("ANDROID_SERIAL"):
        return os.environ["ANDROID_SERIAL"]
    devices = list_devices()
    ready = [d for d in devices if d.state == "device"]
    if len(ready) == 1:
        return ready[0].serial
    if not devices:
        raise AdbError(f"no device connected (adb devices is empty); {DOCTOR_HINT}")
    if not ready:
        why = "; ".join(f"{d.serial} is {d.state} ({state_fix(d.state)})" for d in devices)
        raise AdbError(f"no ready device: {why};\n{DOCTOR_HINT}")
    names = ", ".join(d.serial for d in ready)
    raise AdbError(f"several devices connected ({names}); pass --device <serial>")


class Adb:
    def __init__(self, serial: str):
        self.serial = serial
        self._bin = adb_binary()

    def run(self, args: list[str], timeout: float = 60, check: bool = True,
            binary: bool = False) -> subprocess.CompletedProcess:
        cmd = [self._bin, "-s", self.serial] + args
        try:
            # Logcat carries whatever apps log: a byte that is not UTF-8 must not crash a read.
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  encoding=None if binary else "utf-8", errors=None if binary else "replace")
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb timed out after {timeout}s: {' '.join(args)}") from exc
        if check and proc.returncode != 0:
            err = proc.stderr if not binary else proc.stderr.decode(errors="replace")
            raise AdbError(f"adb {' '.join(args)} failed ({proc.returncode}): {err.strip()}")
        return proc

    def shell(self, *args: str, timeout: float = 60, check: bool = True) -> str:
        return self.run(["shell", *args], timeout=timeout, check=check).stdout

    def exec_out(self, command: str, timeout: float = 60) -> bytes:
        return self.run(["exec-out", command], timeout=timeout, binary=True).stdout

    def screen_size(self, timeout: float = 60) -> tuple[int, int]:
        out = self.shell("wm", "size", timeout=timeout)
        # "Override size" wins over "Physical size" when present.
        size = None
        for line in out.splitlines():
            if ":" in line:
                w, h = line.split(":", 1)[1].strip().split("x")
                size = (int(w), int(h))
        if not size:
            raise AdbError(f"could not read screen size: {out!r}")
        return size
