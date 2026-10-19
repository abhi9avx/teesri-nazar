#!/usr/bin/env python3
"""Benchmark the learned navigation map with the real agent: model turns, time and cost per
navigation, with the map off, on and empty (first visit), and on and learned (repeat visit).

    python3 scripts/bench_navmap.py --device emulator-5554 [--tasks plans/bench-extra.json] [--only settings]

Each task is a user's request ("open internet settings") for one app. For every task the agent
(Claude Code, headless, with this repo's tester prompt) gets a fresh tnazar run with the app on
its home screen and is asked to go there; the script then checks the screen itself with the
task's ground-truth selector, so a wrong screen counts as a failure whatever the agent said.

Modes, in this order per task: `off` (TNAZAR_MAP=off: today's behaviour), `cold` (map on, empty
for this task: the agent explores and the map learns), `warm` (map on, learned: the agent should
use goto). Everything runs in a throwaway TNAZAR_HOME and TNAZAR_MAP_DIR, so your runs, plans
and maps are never touched, and no saved flow helps. A dashboard on --port shows each run live.

Tasks are JSON: [{"app": "settings", "package": "...", "home": "<selector the home screen
shows>", "reset": [["tap", "..."]], "login": {...}, "phrase": "...", "expect": ["check", "args"]}].
Built-in tasks use only Android's own Settings and Clock. Results (JSON and a markdown table)
go to <home>/bench/; exit 0 when no mode failed a task that `off` passed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TNAZAR = str(ROOT / "tnazar")
MODES = ("off", "cold", "warm")
SETTINGS, CLOCK = "com.android.settings", "com.google.android.deskclock"
BUILTIN = [
    {"app": "settings", "package": SETTINGS, "home": "Search Settings", "phrase": "open internet settings",
     "expect": ["desc=Internet"]},
    {"app": "settings", "package": SETTINGS, "home": "Search Settings", "phrase": "open display settings",
     "expect": ["desc=Display & touch"]},
    {"app": "settings", "package": SETTINGS, "home": "Search Settings", "phrase": "open apps settings",
     "expect": ["desc=Apps"]},
    {"app": "settings", "package": SETTINGS, "home": "Search Settings", "phrase": "open connected devices",
     "expect": ["desc=Connected devices"]},
    {"app": "clock", "package": CLOCK, "home": "id=action_bar_title", "reset": [["tap", "desc=World Clock"]],
     "phrase": "open timers", "expect": ["Timers", "--id", "action_bar_title"]},
    {"app": "clock", "package": CLOCK, "home": "id=action_bar_title", "reset": [["tap", "desc=World Clock"]],
     "phrase": "open the stopwatch", "expect": ["Stopwatch", "--id", "action_bar_title"]},
]
PROMPT = ("A tnazar run is already active on the device, with the app {package} open on its home screen. "
          "The user asks: \"{phrase}\". Take the app to that screen with ./tnazar commands, then stop and "
          "reply DONE. Do not start, stop or replay runs, do not run plans or cases, do not type into any "
          "field, and do not open the dashboard.")


class Bench:
    def __init__(self, args):
        self.args = args
        self.home = tempfile.mkdtemp(prefix="tnazar-bench-")
        self.env = {**os.environ, "TNAZAR_HOME": self.home, "TNAZAR_MAP_DIR": os.path.join(self.home, "maps")}
        self.out = Path(self.home) / "bench"
        self.out.mkdir()
        self.results: list[dict] = []
        self.ui = None

    def tn(self, *argv, env=None, timeout=180) -> tuple[int, str]:
        # Every call names the device: the project's SessionEnd hook stops the run when the agent's
        # session ends, so the final check runs without an active run to take the device from.
        dev = ["-s", self.args.device] if self.args.device else []
        try:
            p = subprocess.run([TNAZAR, *dev, *argv], env=env or self.env, capture_output=True, text=True,
                               timeout=timeout, cwd=ROOT)
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s: tnazar {' '.join(argv)}"
        return p.returncode, p.stdout + p.stderr

    def dashboard(self) -> None:
        self.ui = subprocess.Popen([TNAZAR, "ui", "--port", str(self.args.port), "--no-open"], env=self.env,
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"dashboard: http://127.0.0.1:{self.args.port}  (home {self.home})", flush=True)

    def prepare(self, task: dict, run: str, env: dict) -> None:
        code, out = self.tn("start", run, "--package", task["package"], env=env)
        if code:
            sys.exit(f"could not start {run}:\n{out}")
        self.tn("launch", "--restart", env=env)
        login = task.get("login")
        if login:
            self.tn("wait", login["screen"], "--or", task["home"], "--timeout", "25", env=env)
            for _ in range(2):  # a known app bug drops the first PIN after a cold start
                if self.tn("check", login["screen"], "--timeout", "2", env=env)[0]:
                    break
                self.tn("type", "--env", login["pin_env"], "--into", login["field"], env=env)
                if self.tn("wait", task["home"], "--timeout", "15", env=env)[0] == 0:
                    break
        self.tn("wait", task["home"], "--timeout", "25", env=env)
        for step in task.get("reset", []):
            self.tn(*step, env=env)

    def agent(self, task: dict, env: dict) -> dict:
        began = time.monotonic()
        try:
            p = subprocess.run(
            ["claude", "-p", PROMPT.format(**task), "--output-format", "json", "--no-session-persistence",
             "--strict-mcp-config", "--setting-sources", "project,local", "--max-turns", str(self.args.max_turns),
             "--allowedTools", "Bash(./tnazar *)", "--disallowedTools", "Edit", "Write", "NotebookEdit"],
            env=env, cwd=ROOT, capture_output=True, text=True, timeout=self.args.agent_timeout,
            stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"error": f"agent timed out after {self.args.agent_timeout}s", "seconds": self.args.agent_timeout}
        took = time.monotonic() - began
        try:
            d = json.loads(p.stdout)
        except ValueError:
            return {"error": (p.stdout + p.stderr)[-300:], "seconds": round(took, 1)}
        u = d.get("usage") or {}
        return {"turns": d.get("num_turns"), "seconds": round(took, 1), "cost_usd": round(d.get("total_cost_usd") or 0, 3),
                "output_tokens": u.get("output_tokens"), "agent_error": d.get("is_error"),
                "said": str(d.get("result", ""))[-120:]}

    def one(self, task: dict, mode: str, n: int) -> dict:
        # Each task learns into its own map: "cold" is empty, "warm" has only what "cold" taught.
        env = dict(self.env, TNAZAR_MAP_DIR=os.path.join(self.home, "maps", f"task-{n:02d}"))
        if mode == "off":
            env["TNAZAR_MAP"] = "off"
        run = f"bench-{n:02d}-{mode}"
        self.prepare(task, run, env)
        r = self.agent(task, env)
        ok = self.tn("check", *task["expect"], "--timeout", "5", env=env)[0] == 0
        log = Path(self.home) / "runs" / run / "events.jsonl"
        events = [json.loads(x) for x in log.read_text().splitlines() if x.strip()] if log.exists() else []
        navs = [e for e in events if e.get("type") == "nav"]
        self.tn("stop", env=env)
        r.update({"task": task["phrase"], "app": task["app"], "mode": mode, "on_target": ok,
                  "gotos": len(navs), "goto_arrived": sum(1 for e in navs if e.get("outcome") == "arrived"),
                  "device_steps": sum(1 for e in events if e.get("type") in ("tap", "key", "scroll", "type"))})
        print(f"  {mode:5} {'OK  ' if ok else 'MISS'} turns={r.get('turns')} {r.get('seconds')}s "
              f"${r.get('cost_usd')} goto={r['goto_arrived']}/{r['gotos']}", flush=True)
        return r

    def run(self) -> int:
        tasks = list(BUILTIN)
        if self.args.tasks:
            tasks += json.loads(Path(self.args.tasks).read_text())
        if self.args.only:
            tasks = [t for t in tasks if t["app"] in self.args.only.split(",")]
            if not tasks:
                sys.exit(f"no task for --only {self.args.only}")
        self.dashboard()
        try:
            for n, task in enumerate(tasks, 1):
                print(f"[{n}/{len(tasks)}] {task['app']}: {task['phrase']}", flush=True)
                for mode in MODES:
                    self.results.append(self.one(task, mode, n))
                    self.save()
        finally:
            if self.ui:
                self.ui.terminate()
        return self.summary()

    def save(self) -> None:
        (self.out / "results.json").write_text(json.dumps(self.results, indent=1))

    def summary(self) -> int:
        lines = ["| Task | App | Mode | On target | Turns | Seconds | Cost $ | goto arrived |",
                 "|---|---|---|---|---|---|---|---|"]
        for r in self.results:
            lines.append(f"| {r['task']} | {r['app']} | {r['mode']} | {'yes' if r['on_target'] else 'NO'} | "
                         f"{r.get('turns')} | {r.get('seconds')} | {r.get('cost_usd')} | "
                         f"{r['goto_arrived']}/{r['gotos']} |")
        lines += ["", "| Mode | On target | Avg turns | Avg seconds | Total cost $ |", "|---|---|---|---|---|"]
        worse = 0
        for mode in MODES:
            rs = [r for r in self.results if r["mode"] == mode]
            if not rs:
                continue
            def avg(k):
                vals = [r[k] for r in rs if r.get(k) is not None]  # a run that errored has no turns
                return round(sum(vals) / len(vals), 1) if vals else None
            lines.append(f"| {mode} | {sum(r['on_target'] for r in rs)}/{len(rs)} | {avg('turns')} | "
                         f"{avg('seconds')} | {round(sum(r.get('cost_usd') or 0 for r in rs), 2)} |")
        for t in {r["task"] for r in self.results}:
            by = {r["mode"]: r for r in self.results if r["task"] == t}
            if by.get("off", {}).get("on_target") and not all(by.get(m, {}).get("on_target", True) for m in MODES):
                worse += 1
        lines += ["", f"Tasks the map made worse (off reached the screen, a map mode did not): {worse}"]
        (self.out / "results.md").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        print(f"\nresults: {self.out}")
        return 1 if worse else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", "-s")
    ap.add_argument("--tasks", help="extra tasks (JSON), e.g. for an app under test; keep it out of git")
    ap.add_argument("--only", help="comma-separated app names to run")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--agent-timeout", type=int, default=600)
    return Bench(ap.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
