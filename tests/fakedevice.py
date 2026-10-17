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
import urllib.error
from unittest import mock
from xml.sax.saxutils import quoteattr

from teesri_nazar import cli
from teesri_nazar.adb import AdbError

PACKAGE = "com.example.app"
SERIAL = "fake-1"
PNG = b"\x89PNG\r\n\x1a\nfake"


def node(id: str = "", text: str = "", desc: str = "", bounds=(0, 0, 100, 100), cls: str = "android.widget.TextView",
         clickable: bool = True, enabled: bool = True, checked: bool = False, password: bool = False,
         scrollable: bool = False) -> dict:
    return dict(id=id, text=text, desc=desc, bounds=bounds, cls=cls, clickable=clickable,
                enabled=enabled, checked=checked, password=password, scrollable=scrollable)


def screen_xml(nodes: list[dict], size=(1080, 2400), package: str = PACKAGE) -> str:
    def attrs(n: dict) -> str:
        l, t, r, b = n["bounds"]
        rid = f"{package}:id/{n['id']}" if n["id"] else ""
        pairs = {
            "text": n["text"], "resource-id": rid, "class": n["cls"], "package": package,
            "content-desc": n["desc"], "clickable": str(n["clickable"]).lower(),
            "enabled": str(n["enabled"]).lower(), "checked": str(n["checked"]).lower(),
            "password": str(n["password"]).lower(), "bounds": f"[{l},{t}][{r},{b}]",
            "scrollable": str(n.get("scrollable", False)).lower(),
        }
        return " ".join(f"{k}={quoteattr(v)}" for k, v in pairs.items())

    root = node(cls="android.widget.FrameLayout", clickable=False, bounds=(0, 0, *size))
    children = "".join(f"<node {attrs(n)} />" for n in nodes)
    return (f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            f"<hierarchy rotation=\"0\"><node {attrs(root)}>{children}</node></hierarchy>")


def server_xml(nodes: list[dict], size=(1080, 2400), package: str = PACKAGE) -> str:
    """The same screen as the UiAutomator2 server serializes it: class names as tags, no empty content-desc."""
    def el(n: dict, inner: str = "") -> str:
        l, t, r, b = n["bounds"]
        rid = f"{package}:id/{n['id']}" if n["id"] else ""
        pairs = {"index": "0", "package": package, "class": n["cls"], "text": n["text"], "resource-id": rid,
                 "clickable": str(n["clickable"]).lower(), "enabled": str(n["enabled"]).lower(),
                 "checked": str(n["checked"]).lower(), "password": str(n["password"]).lower(),
                 "bounds": f"[{l},{t}][{r},{b}]", "displayed": "true"}
        if n["desc"]:
            pairs["content-desc"] = n["desc"]
        return f"<{n['cls']} " + " ".join(f"{k}={quoteattr(v)}" for k, v in pairs.items()) + f">{inner}</{n['cls']}>"

    root = node(cls="android.widget.FrameLayout", clickable=False, bounds=(0, 0, *size))
    hidden = dict(node(text="Off-screen", cls="android.widget.TextView"), bounds=(0, 0, 10, 10))
    hidden_xml = el(hidden).replace('displayed="true"', 'displayed="false"')
    return (f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy index=\"0\" rotation=\"0\">"
            f"{el(root, ''.join(el(n) for n in nodes) + hidden_xml)}</hierarchy>")


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
        self.secure = False  # FLAG_SECURE screen: screencap yields no PNG
        self.dump_failures = 0  # next N dumps fail like an animating screen
        self.dump_hangs = 0  # next N dumps hang until adb's timeout (app never goes idle)
        self.uia2_installed = False  # the UiAutomator2 server APKs are on the device
        self.uia2_up = False  # its instrumentation is running
        self.uia2_broken = False  # it stops answering (crashed, killed by the OS)
        self.uia2_errors = 0  # next N reads answer HTTP 500 (screen changing mid-read)
        self.uia2_reads = 0
        self.offline = False  # airplane mode: ping fails
        self.drop_taps = 0  # next N taps are lost (a screen that was not ready yet)
        self.becomes: tuple[int, str] | None = None  # (n, screen): after n more reads, the app shows `screen`
        self.files: dict[str, str] = {}  # device files tnazar writes via the shell
        self.keyboard = False  # soft keyboard showing
        self.foreign_top: str | None = None  # another package's screen on top of the app's task
        self.keyboard_on_focus = True  # False: focusing a field leaves it hidden (hardware keyboard, timing)
        self.launchers = [".Main"]  # launcher activities; more than one makes resolve-activity return the chooser
        self.typed: list[str] = []
        self.taps: list[tuple[int, int]] = []
        self.calls: list[list[str]] = []

    # --- the UiAutomator2 server's HTTP API, as tnazar's uia2 module calls it
    def uia2_http(self, method: str, port: int, path: str, body: dict | None = None, timeout: float = 10) -> dict:
        if not self.uia2_up or self.uia2_broken:
            raise OSError("connection refused")
        if path == "/status":
            return {"value": {"ready": True}}
        if path == "/session":
            return {"value": {"sessionId": "s1"}}
        if path.endswith("/appium/settings"):
            return {"value": None}
        if path.endswith("/source"):
            self._tick()
            if self.uia2_errors:
                self.uia2_errors -= 1
                raise urllib.error.HTTPError(path, 500, "Internal Server Error", None, None)
            self.uia2_reads += 1
            return {"value": server_xml(self.nodes(), self.size)}
        raise AssertionError(f"unexpected uia2 call: {method} {path}")

    def _tick(self) -> None:
        """A screen change still in flight (e.g. after Back) lands after a few reads."""
        if self.becomes:
            n, screen = self.becomes
            self.becomes = (n - 1, screen) if n > 1 else None
            if n <= 1:
                self.current = screen

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
            self._tick()
            if self.dump_hangs:
                self.dump_hangs -= 1
                raise AdbError(f"adb timed out after {timeout}s: exec-out {command}")
            if self.dump_failures:
                self.dump_failures -= 1
                return b"ERROR: could not get idle state."
            return screen_xml(self.nodes(), self.size).encode()
        if command.startswith("screencap"):
            return b"" if self.secure else PNG
        raise AssertionError(f"unexpected exec-out: {command}")

    def run(self, args: list[str], timeout: float = 60, check: bool = True, binary: bool = False):
        self.calls.append(list(args))
        out = ""
        if args[:2] == ["shell", "ping"]:
            return subprocess.CompletedProcess(args, 1 if self.offline else 0, "", "")
        if args[:1] == ["forward"]:
            out = "8299\n" if args[1] == "tcp:0" else ""
        elif args[:1] == ["logcat"]:
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
            if self.drop_taps:
                self.drop_taps -= 1
                return ""
            n = self.node_at(x, y)
            if n:
                if "EditText" in n["cls"]:
                    self.keyboard = self.keyboard_on_focus
                nxt = self.app.on_tap.get((self.current, n["id"])) or self.app.on_tap.get((self.current, n["text"]))
                if nxt:
                    self.current, self.keyboard = nxt, False
            return ""
        if cmd == ["input", "text"]:
            self.typed.append(a[2])
            return ""
        if cmd == ["input", "keyevent"]:
            for code in a[2:]:
                if int(code) == 4 and self.keyboard:
                    self.keyboard = False  # back only dismisses the keyboard
                    continue
                nxt = self.app.on_key.get((self.current, int(code)))
                if nxt:
                    self.current, self.keyboard = nxt, False
            return ""
        if cmd == ["input", "swipe"]:
            nxt = self.app.on_swipe.get(self.current)
            if nxt:
                self.current = nxt
            return ""
        if a[:1] == ["echo"] and ">" in a:
            self.files[a[-1]] = " ".join(a[1:a.index(">")]) + "\n"
            return ""
        if a[:1] == ["cat"]:
            return self.files.get(a[1], "")
        if a[:2] == ["rm", "-f"]:
            self.files.pop(a[2], None)
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
        if a[:2] == ["dumpsys", "input_method"]:
            return f"  mInputShown={str(self.keyboard).lower()}\n"
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
            if a[2] == "resolve-activity" and len(self.launchers) > 1:
                return "priority=0\nandroid/com.android.internal.app.ResolverActivity\n"
            return "".join(f"priority=0\n{PACKAGE}/{c}\n" for c in self.launchers)
        if a[:2] == ["am", "start"]:
            if "-n" in a:  # launching the app's own activity resumes its task
                if "0x10008000" in a:  # NEW_TASK | CLEAR_TASK
                    self.foreign_top = None
                if self.foreign_top:
                    self.current = self.foreign_top
            return "Status: ok\n"
        if a[:2] == ["am", "force-stop"]:
            if a[2].startswith("io.appium.uiautomator2"):
                self.uia2_up = False
            else:
                self.current = self.app.start
            return ""
        if a[:3] == ["pm", "list", "instrumentation"]:
            return ("instrumentation:io.appium.uiautomator2.server.test/androidx.test.runner.AndroidJUnitRunner "
                    "(target=io.appium.uiautomator2.server)\n") if self.uia2_installed else ""
        if a[:2] == ["sh", "-c"] and "am instrument" in a[2]:
            self.uia2_up = self.uia2_installed
            return ""
        if a[:2] == ["pkill", "-x"]:
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
    from teesri_nazar import device, replay, screen, uia2

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
        stack.enter_context(mock.patch.object(screen, "time", clock))
        stack.enter_context(mock.patch.object(uia2, "time", clock))
        stack.enter_context(mock.patch.object(uia2, "_http", adb.uia2_http))
        yield home


def tnazar(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()
