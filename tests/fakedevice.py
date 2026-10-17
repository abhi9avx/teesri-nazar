"""A scripted stand-in for one Android device, so the CLI and replay run without adb.

FakeApp describes screens (lists of nodes) and what tapping a node or pressing a key
does. FakeAdb answers the adb calls tnazar makes, serves the current screen as a
uiautomator dump and records every command. `fake_device()` patches it into the CLI
and replay modules, and swaps the clock so waits and timeouts finish instantly.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
from unittest import mock
from xml.sax.saxutils import quoteattr

from teesri_nazar import cli

PACKAGE = "com.example.app"
SERIAL = "fake-1"
PNG = b"\x89PNG\r\n\x1a\nfake"


def node(id: str = "", text: str = "", desc: str = "", bounds=(0, 0, 100, 100), cls: str = "android.widget.TextView",
         clickable: bool = True, enabled: bool = True, checked: bool = False, password: bool = False) -> dict:
    return dict(id=id, text=text, desc=desc, bounds=bounds, cls=cls, clickable=clickable,
                enabled=enabled, checked=checked, password=password)


def screen_xml(nodes: list[dict], size=(1080, 2400), package: str = PACKAGE) -> str:
    def attrs(n: dict) -> str:
        l, t, r, b = n["bounds"]
        rid = f"{package}:id/{n['id']}" if n["id"] else ""
        pairs = {
            "text": n["text"], "resource-id": rid, "class": n["cls"], "package": package,
            "content-desc": n["desc"], "clickable": str(n["clickable"]).lower(),
            "enabled": str(n["enabled"]).lower(), "checked": str(n["checked"]).lower(),
            "password": str(n["password"]).lower(), "bounds": f"[{l},{t}][{r},{b}]",
        }
        return " ".join(f"{k}={quoteattr(v)}" for k, v in pairs.items())

    root = node(cls="android.widget.FrameLayout", clickable=False, bounds=(0, 0, *size))
    children = "".join(f"<node {attrs(n)} />" for n in nodes)
    return (f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            f"<hierarchy rotation=\"0\"><node {attrs(root)}>{children}</node></hierarchy>")


class FakeApp:
    """screens: name -> nodes. on_tap: (screen, node id or text) -> next screen. on_key: (screen, key) -> next."""

    def __init__(self, screens: dict[str, list[dict]], start: str,
                 on_tap: dict | None = None, on_key: dict | None = None, on_swipe: dict | None = None):
        self.screens = screens
        self.start = start
        self.on_tap = on_tap or {}
        self.on_key = on_key or {}
        self.on_swipe = on_swipe or {}


def login_app() -> FakeApp:
    """Login screen -> Home; Back on Home returns to Login."""
    return FakeApp(
        screens={
            "login": [
                node(id="title", text="Welcome back", clickable=False, bounds=(60, 200, 1020, 300)),
                node(id="phone_input", desc="Phone number", cls="android.widget.EditText", bounds=(60, 400, 1020, 520)),
                node(id="mpin_input", cls="android.widget.EditText", password=True, bounds=(60, 560, 1020, 680)),
                node(id="btn_login", text="Login", cls="android.widget.Button", bounds=(60, 800, 1020, 920)),
                node(id="btn_forgot", text="Forgot MPIN", enabled=False, bounds=(60, 930, 1020, 940)),
                node(id="remember", text="Remember me", cls="android.widget.CheckBox", checked=True,
                     bounds=(60, 950, 1020, 1000)),
            ],
            "home": [
                node(id="greeting", text="Hi Asha", clickable=False, bounds=(60, 200, 1020, 300)),
                node(id="balance", text="₹1,000.00", clickable=False, bounds=(60, 320, 1020, 400)),
                node(id="btn_pay", text="Pay now", cls="android.widget.Button", bounds=(60, 800, 1020, 920)),
            ],
        },
        start="login",
        on_tap={("login", "btn_login"): "home"},
        on_key={("home", 4): "login"},
    )


class FakeAdb:
    def __init__(self, app: FakeApp, size=(1080, 2400), installed=(PACKAGE,), version: str = "1.0 (1)"):
        self.app = app
        self.serial = SERIAL
        self.size = size
        self.installed = list(installed)
        self.version = version
        self.current = app.start
        self.settings = {"window_animation_scale": "1.0", "transition_animation_scale": "1.0",
                         "animator_duration_scale": "1.0"}
        self.crash_log = ""
        self.typed: list[str] = []
        self.taps: list[tuple[int, int]] = []
        self.calls: list[list[str]] = []

    # --- helpers for tests
    def nodes(self) -> list[dict]:
        return self.app.screens[self.current]

    def node_at(self, x: int, y: int) -> dict | None:
        hits = [n for n in self.nodes() if n["bounds"][0] <= x < n["bounds"][2] and n["bounds"][1] <= y < n["bounds"][3]]
        return hits[-1] if hits else None

    # --- the Adb interface used by tnazar
    def screen_size(self) -> tuple[int, int]:
        return self.size

    def exec_out(self, command: str, timeout: float = 60) -> bytes:
        self.calls.append(["exec-out", command])
        if "uiautomator dump" in command:
            return screen_xml(self.nodes(), self.size).encode()
        if command.startswith("screencap"):
            return PNG
        raise AssertionError(f"unexpected exec-out: {command}")

    def run(self, args: list[str], timeout: float = 60, check: bool = True, binary: bool = False):
        self.calls.append(list(args))
        out = ""
        if args[:1] == ["logcat"]:
            if "-c" in args:
                self.crash_log = ""
            elif "crash" in args:
                out = self.crash_log
            elif "-t" in args:
                out = "logcat dump\n" + self.crash_log
        elif args[:1] == ["shell"]:
            out = self._shell(args[1:])
        return subprocess.CompletedProcess(args, 0, out, "")

    def shell(self, *args: str, timeout: float = 60, check: bool = True) -> str:
        return self.run(["shell", *args], timeout=timeout, check=check).stdout

    def _shell(self, a: list[str]) -> str:
        cmd = a[:2]
        if cmd == ["input", "tap"]:
            x, y = int(a[2]), int(a[3])
            self.taps.append((x, y))
            n = self.node_at(x, y)
            if n:
                nxt = self.app.on_tap.get((self.current, n["id"])) or self.app.on_tap.get((self.current, n["text"]))
                if nxt:
                    self.current = nxt
            return ""
        if cmd == ["input", "text"]:
            self.typed.append(a[2])
            return ""
        if cmd == ["input", "keyevent"]:
            for code in a[2:]:
                nxt = self.app.on_key.get((self.current, int(code)))
                if nxt:
                    self.current = nxt
            return ""
        if cmd == ["input", "swipe"]:
            nxt = self.app.on_swipe.get(self.current)
            if nxt:
                self.current = nxt
            return ""
        if a[:1] == ["getprop"]:
            return "14\n"
        if a[:2] == ["wm", "size"]:
            return f"Physical size: {self.size[0]}x{self.size[1]}\n"
        if a[:3] == ["pm", "list", "packages"]:
            return "".join(f"package:{p}\n" for p in self.installed)
        if a[:2] == ["pm", "clear"]:
            self.current = self.app.start
            return "Success\n"
        if a[:2] == ["dumpsys", "package"]:
            name, code = self.version.split(" (")
            return f"versionName={name}\nversionCode={code.rstrip(')')}\n"
        if a[:2] == ["dumpsys", "activity"]:
            return f"mResumedActivity: ActivityRecord{{1 u0 {PACKAGE}/.Main t1}}\n"
        if a[:3] == ["settings", "get", "global"]:
            return self.settings.get(a[3], "null") + "\n"
        if a[:3] == ["settings", "put", "global"]:
            self.settings[a[3]] = a[4]
            return ""
        if a[:3] == ["settings", "delete", "global"]:
            self.settings.pop(a[3], None)
            return ""
        if a[:2] == ["cmd", "package"]:
            return f"priority=0\n{PACKAGE}/.Main\n"
        if a[:2] == ["am", "start"]:
            return "Status: ok\n"
        if a[:2] == ["am", "force-stop"]:
            self.current = self.app.start
            return ""
        if a[:1] == ["monkey"]:
            return "Events injected: 1\n"
        raise AssertionError(f"unexpected adb shell: {a}")


class FakeClock:
    """Replaces the `time` module in device/replay so wait loops never really sleep."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.now += secs


@contextlib.contextmanager
def fake_device(adb: FakeAdb):
    """Patch tnazar to use `adb` and a temp TNAZAR_HOME; yields the home directory."""
    from teesri_nazar import device, replay

    clock = FakeClock()
    with tempfile.TemporaryDirectory() as home, contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, {"TNAZAR_HOME": home}))
        for mod in (cli, replay):
            stack.enter_context(mock.patch.object(mod, "Adb", lambda serial: adb))
            stack.enter_context(mock.patch.object(mod, "pick_serial", lambda explicit=None: SERIAL))
        stack.enter_context(mock.patch.object(cli, "list_devices",
                                              lambda: [mock.Mock(serial=SERIAL, state="device", model="FakePhone")]))
        stack.enter_context(mock.patch.object(device, "time", clock))
        stack.enter_context(mock.patch.object(replay, "time", clock))
        yield home


def tnazar(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()
