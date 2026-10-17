"""The CLI side of the live dashboard: the user's inbox, the pause switch and runtime secrets.

The dashboard (`tnazar ui`) and the CLI share nothing but files in .tnazar/ and, for secrets,
a localhost call: the agent runs one short `tnazar` process per step, so every step is a
chance to hand it what the user typed in the browser.
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from .session import now_iso, state_dir

EXIT_HELD = 3
PAUSE_WAIT = 60.0  # stays under the agent's 2-minute command timeout
POLL = 0.5


def inbox_file() -> Path:
    return state_dir() / "inbox.jsonl"


def _cursor_file() -> Path:
    return state_dir() / "inbox.cursor"


def pause_file() -> Path:
    return state_dir() / "paused"


def ui_file() -> Path:
    return state_dir() / "ui.json"


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


def deliver(log) -> list[dict]:
    """Print unread messages for the agent, record them in the run, mark them read."""
    msgs = unread()
    for m in msgs:
        print(f">> USER (dashboard): {m['text']}")
        log({"type": "user_msg", "text": m["text"], "id": m["id"], "sent": m["t"]})
    if msgs:
        mark_all_read()
    return msgs


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
