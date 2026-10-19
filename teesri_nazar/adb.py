"""Thin, synchronous access to one Android device over adb."""

from __future__ import annotations

import os
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


def list_devices() -> list[Device]:
    out = subprocess.run([adb_binary(), "devices", "-l"], capture_output=True, text=True).stdout
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
        devices.append(Device(parts[0], parts[1], model))
    return devices


def pick_serial(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if os.environ.get("ANDROID_SERIAL"):
        return os.environ["ANDROID_SERIAL"]
    ready = [d for d in list_devices() if d.state == "device"]
    if len(ready) == 1:
        return ready[0].serial
    if not ready:
        raise AdbError("no device connected (adb devices is empty)")
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

    def screen_size(self) -> tuple[int, int]:
        out = self.shell("wm", "size")
        # "Override size" wins over "Physical size" when present.
        size = None
        for line in out.splitlines():
            if ":" in line:
                w, h = line.split(":", 1)[1].strip().split("x")
                size = (int(w), int(h))
        if not size:
            raise AdbError(f"could not read screen size: {out!r}")
        return size
