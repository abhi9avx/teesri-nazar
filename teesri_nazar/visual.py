"""Screenshot comparison delegated to Claude Code in headless mode."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

PROMPT = """You compare Android screenshots for a regression replay.
For each numbered pair, open both images with the Read tool and decide whether the
replay shows the same screen and the same meaningful content as the original.
Ignore status bar (clock, battery, signal), notifications, cursor blink, and
timestamps or values that naturally change between runs (dates, OTPs, balances
that the test does not control). Fail on: different screen, missing or extra UI
elements, wrong text, error dialogs, broken layout.

{pairs}

Reply with only JSON, no prose:
{{"results": [{{"pair": 1, "ok": true, "reasoning": "one sentence"}}]}}"""


def compare(pairs: list[tuple[str, str]], timeout: float = 600) -> list[dict]:
    if not shutil.which("claude"):
        return [_fail(g, a, "claude CLI not found; visual comparison skipped") for g, a in pairs]
    listing = "\n".join(f"Pair {i}:\n  original: {g}\n  replay:   {a}" for i, (g, a) in enumerate(pairs, 1))
    try:
        proc = subprocess.run(
            ["claude", "-p", PROMPT.format(pairs=listing), "--allowedTools", "Read",
             "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [_fail(g, a, "visual comparison timed out") for g, a in pairs]
    verdicts = _parse(proc.stdout)
    out = []
    for i, (g, a) in enumerate(pairs, 1):
        v = verdicts.get(i)
        out.append({"golden": g, "actual": a, "ok": bool(v and v.get("ok")),
                    "reasoning": (v or {}).get("reasoning", "no verdict returned")})
    return out


def _parse(stdout: str) -> dict[int, dict]:
    text = stdout.strip()
    try:
        envelope = json.loads(text)
        text = envelope.get("result", text) if isinstance(envelope, dict) else text
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {int(r.get("pair", 0)): r for r in data.get("results", []) if isinstance(r, dict)}


def _fail(golden: str, actual: str, why: str) -> dict:
    return {"golden": golden, "actual": actual, "ok": False, "reasoning": why}
