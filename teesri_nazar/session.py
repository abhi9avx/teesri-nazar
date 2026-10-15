"""Run state on disk: one directory per run, an append-only event log, an active pointer."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def home() -> Path:
    return Path(os.environ.get("TNAZAR_HOME") or os.getcwd())


def runs_dir() -> Path:
    return home() / "runs"


def state_dir() -> Path:
    d = home() / ".tnazar"
    d.mkdir(exist_ok=True)
    return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class Run:
    """A test run: runs/<name>/{run.json, events.jsonl, shots/, dumps/, logs/}."""

    def __init__(self, path: Path):
        self.path = path
        self.meta_path = path / "run.json"
        self.events_path = path / "events.jsonl"

    @property
    def name(self) -> str:
        return self.path.name

    @classmethod
    def create(cls, name: str, meta: dict) -> "Run":
        if not NAME_RE.match(name):
            raise ValueError("run name must be letters, digits, '-' or '_' (max 64)")
        path = runs_dir() / name
        if path.exists():
            path = runs_dir() / f"{name}-{stamp()}"
        for sub in ("shots", "dumps", "logs"):
            (path / sub).mkdir(parents=True, exist_ok=True)
        run = cls(path)
        run.events_path.touch()
        run.save_meta({"name": path.name, "started_at": now_iso(), **meta})
        return run

    @classmethod
    def load(cls, name_or_path: str) -> "Run":
        p = Path(name_or_path)
        if not p.is_dir():
            p = runs_dir() / name_or_path
        if not (p / "run.json").exists():
            raise FileNotFoundError(f"no run found at {p}")
        return cls(p)

    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text())

    def save_meta(self, meta: dict) -> None:
        self.meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    def update_meta(self, **changes) -> dict:
        meta = self.meta()
        meta.update(changes)
        self.save_meta(meta)
        return meta

    def log(self, event: dict) -> dict:
        event = {"t": now_iso(), **event}
        with self.events_path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self) -> list[dict]:
        out = []
        for line in self.events_path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def artifact(self, kind: str, label: str, ext: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:40] or kind
        base = self.path / kind / f"{time.strftime('%H%M%S')}-{safe}"
        path, n = base.with_suffix(f".{ext}"), 2
        while path.exists():
            path, n = base.parent / f"{base.name}-{n}.{ext}", n + 1
        return path

    def rel(self, p: Path) -> str:
        return os.path.relpath(p, self.path)

    def current_case(self) -> str | None:
        case = None
        for e in self.events():
            if e["type"] == "case_start":
                case = e["case"]
            elif e["type"] == "case_end":
                case = None
        return case


def _active_file() -> Path:
    return state_dir() / "active.json"


def set_active(run: Run, serial: str) -> None:
    _active_file().write_text(json.dumps({"run": str(run.path), "device": serial}) + "\n")


def get_active() -> tuple[Run, str] | None:
    f = _active_file()
    if not f.exists():
        return None
    data = json.loads(f.read_text())
    path = Path(data["run"])
    if not path.exists():
        f.unlink()
        return None
    return Run(path), data["device"]


def clear_active() -> None:
    _active_file().unlink(missing_ok=True)
