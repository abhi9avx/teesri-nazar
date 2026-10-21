#!/usr/bin/env python3
"""Live check of the dashboard on a real device or emulator: the lines marked *live* in the
dashboard acceptance list (ARCHITECTURE.md). Run it before calling a dashboard change done.

    python3 scripts/smoke_dashboard.py [--device emulator-5554]

It drives the system Settings app (on every Android device; nothing app-specific), in a
throwaway TNAZAR_HOME and TNAZAR_MAP_DIR, so it never touches your runs, `.tnazar/` or your maps. Needs `node` to render
the timeline the way the page does. Exit 0 when every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TNAZAR = str(ROOT / "tnazar")
APP = "com.android.settings"
MAP = f"""app {APP}

flow network
  key back
  key back
  tap '~Network' --wait 10
  wait '~Internet' --timeout 10
end
"""


class Smoke:
    def __init__(self, device: str | None):
        self.home = tempfile.mkdtemp(prefix="tnazar-smoke-")
        self.env = {**os.environ, "TNAZAR_HOME": self.home, "TNAZAR_MAP_DIR": os.path.join(self.home, "maps"),
                    **({"ANDROID_SERIAL": device} if device else {})}
        self.results: list[tuple[str, bool, str]] = []
        self.procs: list[subprocess.Popen] = []

    # ---------------------------------------------------------------- plumbing
    def tn(self, *argv: str) -> tuple[int, str]:
        p = subprocess.run([TNAZAR, *argv], env=self.env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def spawn(self, *argv: str) -> subprocess.Popen:
        p = subprocess.Popen([TNAZAR, *argv], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.procs.append(p)
        return p

    def check(self, da: str, ok: bool, what: str) -> bool:
        self.results.append((da, bool(ok), what))
        print(f"{'PASS' if ok else 'FAIL'}  {da}  {what}", flush=True)
        return bool(ok)

    def api(self, method: str, path: str, body: dict | None = None):
        ui = json.loads((Path(self.home) / ".tnazar" / "ui.json").read_text())
        req = urllib.request.Request(f"http://127.0.0.1:{ui['port']}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Tnazar-Token": ui["token"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def status(self) -> dict:
        return self.api("GET", "/api/state")[1]["agent"]

    def timeline(self) -> list[str]:
        """The timeline as the page draws it: ui.html's own layout() and describe(), in node."""
        events = self.api("GET", "/api/events?run=smoke&from=0")[1]["events"]
        html = (ROOT / "teesri_nazar" / "ui.html").read_text()
        a = html.index("const fmtDur")
        js = (html[a:html.index("\n", a)] + "\n"
              + html[html.index("// Ids are written"):html.index("// ------------------------------------------------------------------ timeline")]
              + f"""
const out = [];
for (const b of layout({json.dumps(events)})) {{
  if (b.kind === "group") {{ out.push("» " + b.e.text); for (const s of b.steps) out.push(describe(s)[1]); }}
  else if (b.kind === "user") out.push("You: " + b.e.text);
  else if (b.kind === "step" || b.kind === "not_run") out.push(describe(b.e)[1]);
}}
console.log(JSON.stringify(out));""")
        return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)

    # ---------------------------------------------------------------- the checks
    def run(self) -> int:
        code, out = self.tn("start", "smoke", "--package", APP)
        if code:
            print(f"could not start a run:\n{out}")
            return 2
        self.tn("launch", "--restart")
        self.spawn("ui", "--no-open", "--port", "0")
        ui_json = Path(self.home) / ".tnazar" / "ui.json"
        for _ in range(50):
            if ui_json.exists():
                break
            time.sleep(0.1)
        listener = self.spawn("wait-message")
        time.sleep(1)
        self.check("DA-07", self.status().get("listening"), "status: a running wait-message shows as Listening")

        # DA-03 / DA-04 / DA-02: two secrets at once into the search field
        self.tn("say", "Smoke: typing two codes at once")
        self.tn("tap", "~Search", "--wait", "10")
        # Send only once the field is ready, as a person would: typing into a screen that is
        # still opening drops the first key, in any app.
        for _ in range(20):
            if any("EditText" in ln and "focused" in ln for ln in self.tn("observe")[1].splitlines()):
                break
            time.sleep(0.25)
        time.sleep(0.5)
        replies = []
        sends = [threading.Thread(target=lambda n, v: replies.append(
                     self.api("POST", "/api/secret", {"name": n, "value": v, "type_now": True})[0]), args=nv)
                 for nv in (("otp", "1111"), ("pin", "2222"))]
        for t in sends:
            t.start()
        for t in sends:
            t.join()
        self.check("DA-03", replies == [200, 200], f"both secrets typed at once (HTTP {replies})")
        _, screen = self.tn("observe")
        field = next((ln for ln in screen.splitlines() if "EditText" in ln), "")
        # Each value is masked only where it appears whole: mixed digits would show in clear.
        self.check("DA-04", 'text="••••••••"' in field, f"two codes typed one after the other ({field.strip()[:60]})")
        self.check("DA-02", listener.poll() is None, "the 'already typed' notes did not wake the agent")

        # DA-01 / DA-07: a message
        began = time.monotonic()
        self.api("POST", "/api/message", {"text": "Smoke: hello"})
        try:
            code = listener.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        took = time.monotonic() - began
        self.check("DA-01", code == 3 and took < 2, f"message reached the agent in {took:.2f}s (exit {code})")
        self.check("DA-07", self.status().get("state") == "got_message", "status: Got your message")
        self.tn("say", "Smoke: acting on the message")
        self.check("DA-07", self.status().get("state") == "working", "status: Working")

        # DA-06: a step that cannot run
        code, _ = self.tn("tap", "Smoke nowhere", "--wait", "1")
        self.check("DA-06", code == 1, "a tap that finds nothing exits 1")

        # DA-12: a saved route
        plan = Path(self.home) / "map.plan"
        plan.write_text(MAP)
        code, out = self.tn("flow", "network", "--plan", str(plan))
        self.check("DA-12", code == 0, f"saved route ran in one command (exit {code})")
        code, out = self.tn("flow", "nope", "--plan", str(plan))
        self.check("DA-12", code == 2 and "network" in out, "a missing route names the existing ones")

        # DA-06 / DA-08 / DA-09: the timeline as drawn
        lines = self.timeline()
        say_at = next((i for i, ln in enumerate(lines) if ln == "You: Smoke: hello"), -1)
        miss_at = next((i for i, ln in enumerate(lines) if ln.startswith('Could not tap "Smoke nowhere"')), -1)
        self.check("DA-06", miss_at >= 0, "the missed tap is a red row with its target")
        self.check("DA-08", 0 <= say_at < miss_at, "your message is drawn before the steps that came after it")
        self.check("DA-09", 'Tap "Search" (partial)' in lines and "You typed the OTP from the dashboard (hidden)" in lines
                   and not any("timed out" in ln for ln in lines), "timeline lines are plain words")

        # DA-14: a declared case that never started is a row once the run ends
        self.tn("case", "plan", "SMOKE-1", "Ran")
        self.tn("case", "plan", "SMOKE-2", "Never started")
        self.tn("case", "start", "SMOKE-1")
        self.tn("check", "--result", "pass", "--kind", "other", "--what", "smoke case ran")
        self.tn("case", "end")
        code, out = self.tn("stop")
        self.check("DA-14", code == 0 and "○ 1 not run" in out and "verified 1/2" in out,
                   "stop counts the case that never started")
        lines = self.timeline()
        self.check("DA-14", "SMOKE-2 Never started: not run" in lines, "the dashboard shows it as a not-run row")
        return 0 if all(ok for _, ok, _ in self.results) else 1

    def close(self) -> None:
        for p in self.procs:
            p.terminate()
        self.tn("stop", "--if-active")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", help="adb serial (default: the one connected device, or $ANDROID_SERIAL)")
    args = ap.parse_args()
    smoke = Smoke(args.device)
    try:
        code = smoke.run()
    finally:
        smoke.close()
    failed = [r for r in smoke.results if not r[1]]
    print(f"\n{len(smoke.results) - len(failed)}/{len(smoke.results)} checks passed"
          + (f"; failed: {', '.join(da for da, _, _ in failed)}" if failed else ""))
    return code


if __name__ == "__main__":
    sys.exit(main())
