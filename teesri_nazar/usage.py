"""Token cost of a run.

Two numbers, from two places:
- per step: how many tokens a `tnazar` command's output adds to the agent's context
  (logged by the CLI as an `io` event; ~4 characters per token);
- per case: what the model really used, read from Claude Code's session transcript
  (~/.claude/projects/<project>/<session>.jsonl) for the requests made between
  `case start` and `case end`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .session import Run, home


def estimate(text: str) -> int:
    return -(-len(text) // 4)


def fmt(n: int) -> str:
    if n < 1000:
        return "<1k"
    k = n / 1000
    return f"{k:.1f}k".replace(".0k", "k") if k < 10 else f"{round(k)}k"


def transcripts() -> list[Path]:
    """Claude Code session logs for this project folder, newest first."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    folder = root / re.sub(r"[^A-Za-z0-9]", "-", str(home().resolve()))
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def _requests(paths: list[Path], start: str, end: str) -> dict[str, tuple[str, dict]]:
    """Model requests in [start, end]; one entry per request even when it spans several lines."""
    found: dict[str, tuple[str, dict]] = {}
    for p in paths:
        if start and p.stat().st_mtime < _epoch(start):
            continue  # last written before the run began
        with p.open(errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t, u = d.get("timestamp", ""), (d.get("message") or {}).get("usage")
                if d.get("type") == "assistant" and u and start <= t <= end:
                    found.setdefault(d.get("requestId") or d.get("uuid") or t, (t, u))
    return found


def _epoch(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def per_case(run: Run, paths: list[Path] | None = None) -> dict[str, dict]:
    """{case: {turns, new, cached, output}}: new = fresh input + cache writes + output.

    A model request pays for the cases that finish while its tool calls run, i.e. before the
    next request; one command that runs several cases splits the cost between them. A request
    that finishes no case (planning, reading output) is carried to the next case that ends.
    """
    paths = transcripts() if paths is None else paths
    ends = [(e["t"], e["case"]) for e in run.events() if e["type"] == "case_end"]
    if not paths or not ends:
        return {}
    start = run.meta().get("started_at", "")
    reqs = sorted(_requests(paths, "", ends[-1][0]).values(), key=lambda r: r[0])
    # The request whose command started the run comes just before `started_at`: it belongs here.
    first = max((i for i, (t, _) in enumerate(reqs) if t <= start), default=0)
    reqs = reqs[first:]
    out: dict[str, dict] = {c: {"turns": 0, "new": 0, "cached": 0, "output": 0} for _, c in ends}
    carried: list[dict] = []
    for i, (t, u) in enumerate(reqs):
        until = reqs[i + 1][0] if i + 1 < len(reqs) else ends[-1][0]
        cases = list(dict.fromkeys(c for te, c in ends if t < te <= until))
        carried.append(u)
        if not cases:
            continue
        for c in cases:
            total = out[c]
            for paid in carried:
                total["turns"] += 1
                total["output"] += paid.get("output_tokens", 0) // len(cases)
                total["cached"] += paid.get("cache_read_input_tokens", 0) // len(cases)
                total["new"] += (paid.get("input_tokens", 0) + paid.get("cache_creation_input_tokens", 0)
                                 + paid.get("output_tokens", 0)) // len(cases)
        carried = []
    return out


def label(u: dict | None) -> str:
    return f"{fmt(u['new'])} new · {fmt(u['cached'])} cached" if u and u["turns"] else ""
