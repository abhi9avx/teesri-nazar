"""`tnazar ui`: a localhost dashboard for watching and steering a run.

Reads what the CLI already writes (runs/<name>/events.jsonl, evidence files), polls the
device for a live screenshot, and writes the user's messages and pause switch to .tnazar/
for the next `tnazar` command to pick up (see live.py). Secrets typed here stay in this
process's memory and reach the CLI over a token-protected localhost call.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets as _secrets
import signal
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import device as dev
from . import live
from . import report as rpt
from . import usage
from .adb import Adb, AdbError, pick_serial
from .cli import EXIT_ERROR
from .session import NAME_RE, Run, get_active, runs_dir

PAGE = Path(__file__).with_name("ui.html")
SCREEN_TTL = 1.0
MAX_BODY = 64 * 1024
SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")


def secure_window(dumpsys: str) -> str | None:
    """Name of an on-screen window with FLAG_SECURE (screencap shows it black), else None."""
    for block in re.split(r"\n(?=  Window #\d+ )", dumpsys):
        if not re.search(r"\bfl=[^\n]*\b(?:FLAG_)?SECURE\b", block):
            continue
        shown = re.search(r"isOnScreen=(true|false)", block) or re.search(r"isVisible=(true|false)", block)
        if (shown.group(1) == "true") if shown else "mViewVisibility=0x0" in block:
            m = re.search(r"Window\{\S+ \S+ ([^}]+)\}", block)
            return m.group(1) if m else "a secure window"
    return None


class Dashboard:
    def __init__(self, port: int):
        self.port = port
        self.token = _secrets.token_urlsafe(24)
        self._secrets: dict[str, str] = {}
        self._secrets_run: str | None = None
        self._screen_lock = threading.Lock()
        self._screen: tuple[float, dict] | None = None

    @property
    def secrets(self) -> dict[str, str]:
        """Secrets belong to the run they were sent for; a new run starts with none."""
        active = get_active()
        name = active[0].name if active else None
        if name != self._secrets_run:
            self._secrets.clear()
            self._secrets_run = name
        return self._secrets

    # ------------------------------------------------------------ device screen
    def screen(self) -> dict:
        """{png, hidden, error}; one capture at a time, reused for SCREEN_TTL seconds."""
        with self._screen_lock:
            if self._screen and time.monotonic() - self._screen[0] < SCREEN_TTL:
                return self._screen[1]
            active = get_active()
            shot: dict = {"png": None, "hidden": None, "error": None}
            try:
                adb = Adb(active[1] if active else pick_serial())
                shot["hidden"] = secure_window(adb.shell("dumpsys", "window", "windows", timeout=10, check=False))
                data = adb.exec_out("screencap -p", timeout=15)
                if data.startswith(b"\x89PNG"):
                    shot["png"] = data
                elif not shot["hidden"]:
                    shot["hidden"] = "the app blocked the capture"
            except AdbError as exc:
                shot["error"] = str(exc)
            self._screen = (time.monotonic(), shot)
            return shot

    # ------------------------------------------------------------ views
    def state(self) -> dict:
        active = get_active()
        out: dict = {"active": None, "paused": live.paused(), "secrets": sorted(self.secrets),
                     "slow_ms": rpt.SLOW_MS, "agent": live.agent_status()}
        if active:
            run, serial = active
            meta = run.meta()
            s = rpt.summarize(run)
            out["active"] = {"name": run.name, "device": serial, "package": meta.get("package", ""),
                             "case": run.current_case(), "started_at": meta.get("started_at"),
                             "summary": rpt.summary_line(s)}
            started = meta.get("started_at", "")
            read = live.delivered()
            out["inbox"] = [{"text": m["text"], "t": m["t"], "delivered": i < read,
                             "secret": m.get("secret"), "note": m.get("note"), "info": bool(m.get("info"))}
                            for i, m in enumerate(live.messages()) if m["t"] >= started][-30:]
        return out

    def runs(self) -> list[dict]:
        d = runs_dir()
        if not d.exists():
            return []
        # Newest start first: a folder's mtime moves when an old run gets a new file (a report
        # written again), and the page shows the first run when nothing is running. A new run's
        # folder is always among the recently modified, so only those are read.
        recent = sorted((p for p in d.iterdir() if (p / "run.json").exists()),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:200]
        dated = []
        for run in map(Run, recent):
            try:
                dated.append((run, run.meta()))
            except (ValueError, OSError):
                continue
        dated.sort(key=lambda x: str(x[1].get("started_at") or ""), reverse=True)
        out = []
        for run, meta in dated[:40]:
            p = run.path
            try:
                line = rpt.summary_line(rpt.summarize(run))
            except (ValueError, KeyError, OSError):
                continue
            out.append({"name": run.name, "package": meta.get("package", ""), "started_at": meta.get("started_at"),
                        "finished": bool(meta.get("finished_at")), "summary": line,
                        "report": (p / "report.html").exists()})
        return out

    def events(self, name: str, offset: int) -> dict:
        if not NAME_RE.match(name):
            raise ValueError("bad run name")
        run = Run.load(name)
        with run.events_path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1  # leave a half-written line for the next poll
        lines = chunk[:end].decode().splitlines()
        meta = run.meta()
        return {"events": [json.loads(ln) for ln in lines if ln.strip()], "next": offset + end,
                "meta": {k: meta.get(k) for k in ("name", "package", "device", "model", "android",
                                                  "app_version", "prompt", "started_at", "finished_at")},
                "report": (run.path / "report.html").exists()}

    # ------------------------------------------------------------ actions
    def message(self, body: dict) -> dict:
        text = str(body.get("text", "")).strip()
        if not get_active():
            raise ValueError("no active run: start a test first, then send instructions")
        if not text or len(text) > 2000:
            raise ValueError("message must be 1-2000 characters")
        live.post(text)
        return {"ok": True}

    def secret(self, body: dict) -> dict:
        active = get_active()
        if not active:
            raise ValueError("no active run: start a test first")
        name = re.sub(r"[^A-Z0-9_]", "_", str(body.get("name", "")).strip().upper())
        name = name if name.startswith("UI_") else f"UI_{name}"
        value = str(body.get("value", ""))
        if not SECRET_NAME.match(name) or not value or len(value) > 256:
            raise ValueError("give a name (letters, digits, _) and a value up to 256 characters")
        self.secrets[name] = value
        note = str(body.get("note", "")).strip()
        how = f"`./tnazar type --env {name} --into <field>`. Never print or note the value."

        def tell(text: str, **extra) -> None:
            live.post((f"{note} — " if note else "") + text, secret=name, note=note, **extra)

        if not body.get("type_now"):
            tell(f"Secret value provided as {name}: type it with {how}")
            return {"ok": True, "name": name, "typed": False}
        # PIN and OTP screens expire: typing waits only for the agent step in progress, and the
        # agent hears about it afterwards.
        run, serial = active
        try:
            with live.device_lock():
                try:
                    dev.type_text(Adb(serial), value)
                except (AdbError, ValueError) as exc:
                    reason = typing_error(exc)
                    run.log({"type": "type", "text": None, "secret": True, "env": name, "by": "dashboard",
                             "ok": False, "error": reason})
                    tell(f"Secret value provided as {name}; typing it from the dashboard failed ({reason}): "
                         f"type it with {how}")
                    raise ValueError(f"{name} saved for the agent, but typing it failed: {reason}") from exc
        except live.DeviceBusy:
            tell(f"Secret value provided as {name}; the device was busy, so it was not typed: type it with {how}")
            raise ValueError(f"{name} saved, but the agent is in the middle of a step: try again in a moment") from None
        run.log({"type": "type", "text": None, "secret": True, "env": name, "by": "dashboard"})
        tell(f"{name} was already typed into the focused field by the dashboard: check the screen; "
             f"re-type only if it did not land, with {how}", info=True)
        return {"ok": True, "name": name, "typed": True}


def typing_error(exc: Exception) -> str:
    """Why typing failed, in words: adb's own message quotes the command (and so the value),
    and its stderr may echo pieces of it, so none of that text is kept."""
    text = str(exc).lower()
    if isinstance(exc, ValueError):
        return "only plain ASCII can be typed"
    if "timed out" in text:
        return "adb timed out"
    for words, reason in ((("offline", "not found", "no devices"), "the device is offline"),
                          (("unauthorized",), "the device is not authorized for adb")):
        if any(w in text for w in words):
            return reason
    return "the device did not accept the input"


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tnazar-ui"

        def log_message(self, fmt, *args):  # keep the terminal for the URL and errors
            pass

        def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _error(self, status: int, msg: str) -> None:
            self._json({"error": msg}, status)

        def _host_ok(self) -> bool:
            # Rejects DNS-rebinding: a page on another origin that resolves to 127.0.0.1.
            if self.headers.get("Host") not in (f"127.0.0.1:{dash.port}", f"localhost:{dash.port}"):
                self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
                return False
            return True

        def _token_ok(self) -> bool:
            if not _secrets.compare_digest(self.headers.get("X-Tnazar-Token", ""), dash.token):
                self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
                return False
            return True

        def do_GET(self):
            if not self._host_ok():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            path = url.path
            try:
                if path == "/":
                    page = PAGE.read_text().replace("__TNAZAR_TOKEN__", dash.token)
                    self._send(200, page.encode(), "text/html; charset=utf-8")
                elif path == "/api/state":
                    self._json(dash.state())
                elif path == "/api/runs":
                    self._json(dash.runs())
                elif path == "/api/events":
                    self._json(dash.events(q.get("run", [""])[0], max(0, int(q.get("from", ["0"])[0]))))
                elif path == "/api/usage":
                    name = q.get("run", [""])[0]
                    if not NAME_RE.match(name):
                        raise ValueError("bad run name")
                    self._json(usage.per_case(Run.load(name)))
                elif path == "/api/screen":
                    shot = dash.screen()
                    headers = {"X-Screen-Hidden": shot["hidden"] or "", "X-Screen-Error": (shot["error"] or "")[:200]}
                    if shot["png"]:
                        self._send(200, shot["png"], "image/png", headers)
                    else:
                        self._send(204, b"", "image/png", headers)
                elif path.startswith("/api/secret/") or path == "/api/secrets":
                    if not self._token_ok():
                        return
                    if path == "/api/secrets":
                        self._json({"values": list(dash.secrets.values())})
                    elif (name := path.rsplit("/", 1)[1]) in dash.secrets:
                        self._json({"value": dash.secrets[name]})
                    else:
                        self._error(404, "no such secret")
                elif path.startswith("/runs/"):
                    self._file(unquote(path[len("/runs/"):]))
                else:
                    self._error(404, "not found")
            except (ValueError, FileNotFoundError) as exc:
                self._error(400, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _file(self, rel: str) -> None:
            root = runs_dir().resolve()
            target = (root / rel).resolve()
            if root not in target.parents or not target.is_file():
                self._error(404, "not found")
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            if not self._host_ok() or not self._token_ok():
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._error(413, "body too large")
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected a JSON object")
                if self.path == "/api/message":
                    self._json(dash.message(body))
                elif self.path == "/api/secret":
                    self._json(dash.secret(body))
                elif self.path in ("/api/pause", "/api/resume"):
                    live.set_paused(self.path == "/api/pause")
                    self._json({"ok": True, "paused": live.paused()})
                else:
                    self._error(404, "not found")
            except ValueError as exc:
                self._error(400, str(exc))

    return Handler


def start(port: int) -> tuple[ThreadingHTTPServer, Dashboard]:
    """Bind (port 0 picks a free one) and tell the CLI where to find the dashboard."""
    dash = Dashboard(port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(dash))
    httpd.daemon_threads = True
    dash.port = httpd.server_address[1]
    info = live.ui_file()
    # Created private: it holds the session token, so it must never be readable by others, even briefly.
    fd = os.open(info, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # a stale ui.json keeps its old mode through O_CREAT
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"port": dash.port, "token": dash.token, "pid": os.getpid()}) + "\n")
    return httpd, dash


def serve(port: int, open_browser: bool = True) -> int:
    try:
        httpd, dash = start(port)
    except OSError as exc:
        print(f"error: cannot listen on 127.0.0.1:{port} ({exc.strerror}); "
              f"is `tnazar ui` already running? try --port", file=sys.stderr)
        return EXIT_ERROR
    info = live.ui_file()
    url = f"http://127.0.0.1:{dash.port}/"
    print(f"dashboard: {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)

    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)  # `kill` cleans up ui.json like Ctrl-C does
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        info.unlink(missing_ok=True)
    return 0
