"""Fast screen reads through the UiAutomator2 server, when the device has it.

`uiautomator dump` starts a fresh process for every read: ~2 s, and unbounded while the app
is busy. The UiAutomator2 server (installed by Appium as `io.appium.uiautomator2.server`)
stays up and answers in ~0.05 s, waiting at most IDLE_MS for the app to settle.

A run starts the server and `stop` shuts it down. Reads fall back to `uiautomator dump`
whenever the server is missing or stops answering. The two cannot run together (the server
holds the device's UI automation connection), so the server is stopped before falling back.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from .adb import Adb
from .session import state_dir

SERVER = "io.appium.uiautomator2.server"
TEST = f"{SERVER}.test"
RUNNER = f"{TEST}/androidx.test.runner.AndroidJUnitRunner"
DEVICE_PORT = 6790
IDLE_MS = 300
READY_TIMEOUT = 15.0


def _state_file():
    return state_dir() / "uia2.json"


def _http(method: str, port: int, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _state(adb: Adb) -> dict | None:
    f = _state_file()
    if not f.exists():
        return None
    try:
        st = json.loads(f.read_text())
    except ValueError:
        return None
    return st if st.get("serial") == adb.serial else None


def active(adb: Adb) -> bool:
    return _state(adb) is not None


def installed(adb: Adb) -> bool:
    return RUNNER in adb.shell("pm", "list", "instrumentation", check=False)


def start(adb: Adb) -> bool:
    """Start the server for this device; False (and nothing left running) when it can't."""
    if not installed(adb):
        return False
    adb.shell("sh", "-c", f"'nohup am instrument -w -e disableAnalytics true {RUNNER} >/dev/null 2>&1 &'",
              check=False)
    port = int(adb.run(["forward", "tcp:0", f"tcp:{DEVICE_PORT}"]).stdout.strip())
    deadline = time.monotonic() + READY_TIMEOUT
    while True:
        try:
            if _http("GET", port, "/status", timeout=2)["value"].get("ready"):
                break
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() >= deadline:
            _shutdown(adb, port)
            return False
        time.sleep(0.25)
    try:
        created = _http("POST", port, "/session", {"capabilities": {"firstMatch": [{}], "alwaysMatch": {}}})
        session = created.get("sessionId") or created["value"]["sessionId"]
        _http("POST", port, f"/session/{session}/appium/settings", {"settings": {"waitForIdleTimeout": IDLE_MS}})
    except (OSError, ValueError, KeyError):
        _shutdown(adb, port)
        return False
    _state_file().write_text(json.dumps({"serial": adb.serial, "port": port, "session": session}) + "\n")
    return True


# Seconds to wait before each retry of a read the server answered with an error (~3 s in all).
RETRY_DELAYS = (0.2, 0.3, 0.5, 0.8, 1.2)


def source(adb: Adb) -> str | None:
    """The screen as XML from the server, or None: then the caller reads it with uiautomator dump."""
    st = _state(adb)
    if not st:
        return None
    for delay in (*RETRY_DELAYS, None):
        try:
            return _http("GET", st["port"], f"/session/{st['session']}/source")["value"]
        except urllib.error.HTTPError as exc:
            # The server answered, so it is alive: on Android 14+ it sometimes fails one read
            # ("Cannot set AccessibilityNodeInfo's field 'mSealed'") while a window changes.
            reason = f"HTTP {exc.code}: {_error_message(exc)}"
            if delay is None:
                break
            time.sleep(delay)
        except (OSError, ValueError, KeyError) as exc:
            reason = str(exc) or type(exc).__name__
            break
    print(f"warning: fast screen reads stopped ({reason}); using uiautomator dump (~2 s per read)",
          file=sys.stderr)
    stop(adb)  # frees the UI automation connection so `uiautomator dump` works again
    return None


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        value = json.loads(exc.read() or b"{}").get("value") or {}
        return f"{value.get('error', '')} {value.get('message', '')}".strip()[:200]
    except (OSError, ValueError, AttributeError):
        return ""


def stop(adb: Adb) -> None:
    st = _state(adb)
    _shutdown(adb, st["port"] if st else None)


def _shutdown(adb: Adb, port: int | None) -> None:
    for package in (TEST, SERVER):
        adb.shell("am", "force-stop", package, check=False)
    if port:
        adb.run(["forward", "--remove", f"tcp:{port}"], check=False)
    _state_file().unlink(missing_ok=True)
