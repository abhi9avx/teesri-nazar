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
TYPING_WAIT = 15.0  # an 8-digit code takes ~2 s; a crashed dashboard must not hold the agent forever


def inbox_file() -> Path:
    return state_dir() / "inbox.jsonl"


def _cursor_file() -> Path:
    return state_dir() / "inbox.cursor"


def pause_file() -> Path:
    return state_dir() / "paused"


def ui_file() -> Path:
    return state_dir() / "ui.json"


def typing_file() -> Path:
    return state_dir() / "typing"


def busy_file() -> Path:
    return state_dir() / "busy"


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
            set_agent_status("got_message", msgs[-1]["text"])
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


@contextlib.contextmanager
def dashboard_typing():
    """While the dashboard types a secret, agent commands wait instead of moving focus mid-PIN."""
    typing_file().write_text(now_iso() + "\n")
    try:
        yield
    finally:
        typing_file().unlink(missing_ok=True)


def _fresh(f: Path) -> bool:
    """A marker older than TYPING_WAIT was left by a process that died mid-way: ignore it."""
    try:
        written = f.stat().st_mtime
    except FileNotFoundError:
        return False
    return time.time() - written < TYPING_WAIT


def wait_typing() -> None:
    if not _fresh(typing_file()):
        return
    deadline = time.monotonic() + TYPING_WAIT
    while _fresh(typing_file()) and time.monotonic() < deadline:
        time.sleep(POLL / 5)


@contextlib.contextmanager
def agent_busy():
    """While a device command runs, the dashboard does not start typing into a field it may move."""
    busy_file().write_text(f"{os.getpid()}\n")
    try:
        yield
    finally:
        busy_file().unlink(missing_ok=True)


def wait_agent_idle() -> None:
    deadline = time.monotonic() + TYPING_WAIT
    while _fresh(busy_file()) and _alive(_pid(busy_file())) and time.monotonic() < deadline:
        time.sleep(POLL / 5)


def _pid(f: Path):
    try:
        return int(f.read_text().strip() or 0) or None
    except (FileNotFoundError, ValueError):
        return None


def clear_leftovers() -> None:
    """A new run starts with no typing marker and no status from the previous run."""
    for f in (typing_file(), busy_file(), _status_file()):
        f.unlink(missing_ok=True)


PAUSED_HINT = "Run `./tnazar wait-resume` until it prints `resumed`, then continue."


# ---------------------------------------------------------------- agent status

def _status_file() -> Path:
    return state_dir() / "agent.json"


def set_agent_status(state: str, text: str = "", pid: int | None = None) -> None:
    """listening (wait-message is running) | got_message | working (the last `say`)."""
    _status_file().write_text(json.dumps({"state": state, "text": text, "t": now_iso(), "pid": pid}) + "\n")


def agent_status() -> dict:
    f = _status_file()
    try:
        st = json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        st = {}
    if st.get("state") == "listening" and not _alive(st.get("pid")):
        st["state"] = "away"  # the listener was stopped: messages wait for the agent's next step
    return st or {"state": "away", "text": "", "t": None}


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
