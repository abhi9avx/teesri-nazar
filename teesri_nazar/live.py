"""The CLI side of the live dashboard: the user's inbox, the pause switch and runtime secrets.

The dashboard (`tnazar ui`) and the CLI share nothing but files in .tnazar/ and, for secrets,
a localhost call: the agent runs one short `tnazar` process per step, so every step is a
chance to hand it what the user typed in the browser.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from .session import now_iso, state_dir

EXIT_HELD = 3    # a device command was not run: the user sent a message
EXIT_PAUSED = 5  # a device command was not run: the user still has the run paused
PAUSE_WAIT = 60.0  # stays under the agent's 2-minute command timeout
POLL = 0.5
DEVICE_WAIT = 30.0  # the longest turn on the device: a `tap --wait` / `scroll --to` step, or an 8-digit code
PAUSED_HINT = "Run `./tnazar wait-resume` until it prints `resumed`, then continue."


def inbox_file() -> Path:
    return state_dir() / "inbox.jsonl"


def _cursor_file() -> Path:
    return state_dir() / "inbox.cursor"


def pause_file() -> Path:
    return state_dir() / "paused"


def ui_file() -> Path:
    return state_dir() / "ui.json"


def device_lock_file() -> Path:
    return state_dir() / "device.lock"


# ---------------------------------------------------------------- inbox

def post(text: str, **extra) -> dict:
    msg = {"id": secrets.token_hex(4), "t": now_iso(), "text": text, **extra}
    with inbox_file().open("a") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def messages() -> list[dict]:
    f = inbox_file()
    if not f.exists():
        return []
    # A line the dashboard is still writing has no newline yet; it waits for the next read.
    return [json.loads(line) for line in f.read_text().split("\n")[:-1] if line.strip()]


def delivered() -> int:
    f = _cursor_file()
    return int(f.read_text() or 0) if f.exists() else 0


def unread() -> list[dict]:
    return messages()[delivered():]


def mark_all_read() -> None:
    _cursor_file().write_text(str(len(messages())))


def needs_agent(msgs: list[dict]) -> bool:
    """An info message (the dashboard already did it) is shown, but does not stop a step."""
    return any(not m.get("info") for m in msgs)


def deliver(log) -> list[dict]:
    """Print unread messages for the agent, record them in the run, mark them read."""
    # `wait-message` in the background and the agent's own command can deliver at the same moment.
    with (state_dir() / "inbox.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seen = delivered()
        msgs = messages()[seen:]
        for m in msgs:
            print(f">> USER (dashboard): {m['text']}")
            log({"type": "user_msg", "text": m["text"], "id": m["id"], "sent": m["t"],
                 "secret": m.get("secret"), "note": m.get("note")})
        if needs_agent(msgs):
            set_agent_status("got_message", [m for m in msgs if not m.get("info")][-1]["text"])
        if msgs:
            # Only what was printed: a message that lands meanwhile stays unread for the next step.
            _cursor_file().write_text(str(seen + len(msgs)))
    return msgs


def wait_message(limit: float | None, running) -> bool:
    """Block until the user sends a message the agent must act on, while `running()` holds
    (and up to `limit` seconds). An info note is left for the next delivery."""
    deadline = None if limit is None else time.monotonic() + limit
    while not needs_agent(unread()):
        if (deadline is not None and time.monotonic() >= deadline) or not running():
            return False
        time.sleep(POLL)
    return True


class Since:
    """Messages delivered after a point: a plan or flow must stop for one even when the
    background `wait-message` delivered it, not the plan's own step."""

    def __init__(self):
        self.cursor = delivered()

    def take(self) -> list[dict]:
        upto = delivered()
        msgs, self.cursor = messages()[self.cursor:upto], upto
        return msgs


class DeviceBusy(Exception):
    pass


@contextlib.contextmanager
def device_lock(wait: float | None = None):
    """One turn on the device: the dashboard typing a secret, or an agent step that can move
    focus. An flock, so two dashboard requests, the dashboard and the agent, never interleave,
    and a holder that dies releases it at once."""
    deadline = None
    with device_lock_file().open("a") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                deadline = deadline or now + (DEVICE_WAIT if wait is None else wait)
                if now >= deadline:
                    raise DeviceBusy() from None
                time.sleep(POLL / 10)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------- agent status

def _status_file() -> Path:
    return state_dir() / "agent.json"


def _listener_file() -> Path:
    return state_dir() / "listener.json"


def set_agent_status(state: str, text: str = "") -> None:
    """What the agent last did: got_message (a message was delivered) | working (its last `say`)."""
    _status_file().write_text(json.dumps({"state": state, "text": text, "t": now_iso()}) + "\n")


def set_listener(pid: int) -> None:
    _listener_file().write_text(json.dumps({"pid": pid}) + "\n")


def clear_agent_status() -> None:
    """A new run starts without the previous run's status."""
    _status_file().unlink(missing_ok=True)
    _listener_file().unlink(missing_ok=True)


def _read(f: Path) -> dict:
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def agent_status() -> dict:
    """{state: idle|got_message|working, text, t, listening}: listening while a `wait-message`
    process is alive, whatever the agent is doing meanwhile."""
    st = {"state": "idle", "text": "", "t": None, **_read(_status_file())}
    st["listening"] = _alive(_read(_listener_file()).get("pid"))
    return st


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        pass
    return True


# ---------------------------------------------------------------- pause

def paused() -> bool:
    return pause_file().exists()


def set_paused(on: bool) -> None:
    if on:
        pause_file().write_text(now_iso() + "\n")
    else:
        pause_file().unlink(missing_ok=True)


def wait_resume(limit: float = PAUSE_WAIT) -> bool:
    """Block while paused, up to `limit` seconds. True once resumed."""
    deadline = time.monotonic() + limit
    while paused():
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL)
    return True


# ---------------------------------------------------------------- secrets

def _ui() -> dict | None:
    f = ui_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except ValueError:
        return None


def _get(path: str) -> dict | None:
    ui = _ui()
    if not ui:
        return None
    req = urllib.request.Request(f"http://127.0.0.1:{ui['port']}{path}",
                                 headers={"X-Tnazar-Token": ui["token"]})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def secret(name: str) -> str | None:
    """A value the user typed into the dashboard; it lives only in the dashboard's memory."""
    data = _get(f"/api/secret/{name}")
    return data.get("value") if data else None


def secret_values() -> list[str]:
    data = _get("/api/secrets")
    return data.get("values", []) if data else []
