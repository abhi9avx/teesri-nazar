#!/usr/bin/env python3
"""Live check of the learned navigation map on a real device or emulator. Run it before calling
a map change done.

    python3 scripts/smoke_navmap.py [--device emulator-5554]

It drives the system Settings app in a throwaway TNAZAR_HOME and TNAZAR_MAP_DIR, so it never
touches your runs or your maps. It explores to Settings > Network & internet > Internet once
with the map off and once with it on (same commands, same output), then shows that a second
run reaches the screen with `goto` in one command, from two different start screens, without
duplicates, and that a misspelt phrase only suggests. Exit 0 when every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TNAZAR = str(ROOT / "tnazar")
APP = "com.android.settings"
NETWORK, INTERNET = "Network & internet", "Internet"
EXPLORE = [("tap", NETWORK, "--wait", "10"), ("tap", INTERNET, "--wait", "10"), ("check", INTERNET, "--timeout", "10")]


class Smoke:
    def __init__(self, device: str | None):
        self.home = tempfile.mkdtemp(prefix="tnazar-smoke-")
        self.maps = os.path.join(self.home, "maps")
        self.env = {**os.environ, "TNAZAR_HOME": self.home, "TNAZAR_MAP_DIR": self.maps}
        self.device = device
        self.results: list[tuple[bool, str]] = []
        self.runs = 0

    def tn(self, *argv: str, env: dict | None = None) -> tuple[int, str, float]:
        began = time.monotonic()
        dev = ["-s", self.device] if self.device and argv[0] == "start" else []
        p = subprocess.run([TNAZAR, *dev, *argv], env=env or self.env, capture_output=True, text=True, timeout=180)
        return p.returncode, p.stdout + p.stderr, time.monotonic() - began

    def check(self, ok: bool, what: str) -> bool:
        self.results.append((bool(ok), what))
        print(f"{'PASS' if ok else 'FAIL'}  {what}", flush=True)
        return bool(ok)

    def start(self, env=None) -> str:
        self.runs += 1
        name = f"smoke{self.runs}"
        code, out, _ = self.tn("start", name, "--package", APP, env=env)
        if code:
            sys.exit(f"could not start a run:\n{out}")
        self.tn("launch", "--restart", env=env)
        self.tn("wait", "Search Settings", "--timeout", "15", env=env)
        return out

    def explore(self, env=None) -> tuple[list[str], float]:
        outs, took = [], 0.0
        for argv in EXPLORE:
            code, out, t = self.tn(*argv, env=env)
            took += t
            outs.append(re.sub(r"\d+\.\d+s", "", out))
            if code:
                self.check(False, f"explore step {' '.join(argv)} -> {code}: {out[-300:]}")
        return outs, took

    def back_home(self, env=None) -> None:
        self.tn("launch", "--restart", env=env)
        self.tn("wait", "Search Settings", "--timeout", "15", env=env)

    # ---------------------------------------------------------------- the checks
    def run(self) -> int:
        off = {**self.env, "TNAZAR_MAP": "off"}
        out = self.start(env=off)
        self.check("map: off" in out, "map off: start says so")
        observe_off = self.tn("observe", env=off)[1]
        explore_off, t_off = self.explore(env=off)
        self.tn("stop", env=off)
        self.check(not os.path.exists(self.maps), "map off: nothing learned")

        out = self.start()
        self.check(f"map: learning routes into {self.maps}" in out, "map on: start says where it learns")
        observe_on = self.tn("observe")[1]
        self.check(observe_on == observe_off, "observe prints exactly the same with the map on and off")
        code, out, t_miss = self.tn("goto", "internet")
        self.check(code == 2 and "not in the map yet" in out and "screen: " in out,
                   f"first goto misses (exit {code}) and prints the screen ({t_miss:.1f}s)")
        explore_on, t_on = self.explore()
        self.check(explore_on == explore_off, "exploring prints exactly the same with the map on and off")
        self.back_home()
        code, out, t = self.tn("goto", "internet")
        self.check(code == 0 and "arrived in 2 step(s)" in out, f"same run: goto internet arrives ({t:.1f}s)")
        code, out, _ = self.tn("stop")
        self.check("map: learned" in out, "stop reports what was learned")

        self.start()
        code, out, t_goto = self.tn("goto", "open my internet")
        self.check(code == 0 and "arrived in 2 step(s)" in out and "Internet" in out,
                   f"next run: goto arrives in one command ({t_goto:.1f}s vs {t_on:.1f}s exploring)")
        code, out, _ = self.tn("check", INTERNET)
        self.check(code == 0, "it really is the Internet screen")
        code, out, _ = self.tn("goto", "internet")
        self.check(code == 0 and "already there" in out, "goto on the screen itself taps nothing")
        self.back_home()
        self.tn("tap", NETWORK, "--wait", "10")
        self.tn("wait", INTERNET, "--timeout", "10")
        code, out, _ = self.tn("goto", "internet")
        self.check(code == 0 and "arrived in 1 step(s)" in out, "from the middle of the route: one step, not two")
        self.back_home()
        code, out, _ = self.tn("goto", "internt")
        self.check(code == 2 and "Did you mean: internet?" in out, "a misspelt phrase only suggests")
        code, out, _ = self.tn("goto", "internet", "--dry-run")
        self.check(code == 0 and "→" in out, "dry run shows the route")
        for _ in range(3):
            self.tn("goto", "internet")
            self.back_home()
        self.tn("stop")
        code, out, _ = self.tn("map", "--edges", "--app", APP)
        self.check(out.count("names: internet") == 1, "asking again and again: one name")
        self.check(out.count(f'tap "{INTERNET}"') == 1, "asking again and again: one step per route")
        code, audit, _ = self.tn("map", "audit", "--app", APP)
        mapfile = Path(self.maps, APP, "map.jsonl").read_text()
        self.check("AndroidWifi" not in mapfile and "T-Mobile" not in mapfile,
                   "no screen data (network names, carriers) in the map")
        failed = [w for ok, w in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} passed; "
              f"exploring {t_on:.1f}s vs goto {t_goto:.1f}s for the same screen")
        print(f"home: {self.home}")
        return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", "-s")
    return Smoke(ap.parse_args().device).run()


if __name__ == "__main__":
    sys.exit(main())
