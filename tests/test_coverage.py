"""Coverage ledger: every requested case and expected result is accounted for.

docs/prd-coverage.md, docs/trd-coverage.md, docs/test-plan-coverage.md (test IDs below).
Phase 0 pins today's behaviour for runs that declare nothing: it must never change.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from teesri_nazar import plan
from teesri_nazar import report as rpt
from teesri_nazar import ui, usage
from teesri_nazar.session import Run

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).with_name("fixtures") / "coverage"
HTML = Path(ui.__file__).with_name("ui.html").read_text()


def temp_home(test: unittest.TestCase) -> None:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    env = mock.patch.dict(os.environ, {"TNAZAR_HOME": tmp.name})
    env.start()
    test.addCleanup(env.stop)


def legacy_run() -> Run:
    """A finished hand-driven run as tnazar records it today: no declared cases, every status."""
    run = Run.create("legacy", {"package": "com.example.app", "prompt": "test login",
                                "started_at": "2026-10-21T10:00:00.000Z", "model": "Pixel", "device": "fake-1",
                                "android": "15", "app_version": "1.0"})
    run.update_meta(started_at="2026-10-21T10:00:00.000Z", finished_at="2026-10-21T10:05:00.000Z")
    events = [
        {"type": "case_start", "case": "TC-01", "title": "Login works"},
        {"type": "tap", "selector": {"text": "Login"}, "xy": [1, 1], "label": "Login", "dur_ms": 900},
        {"type": "check", "kind": "ui", "ok": True, "what": "Home visible", "case": "TC-01", "shot": "shots/a.png"},
        {"type": "case_end", "case": "TC-01", "status": "pass", "reason": ""},
        {"type": "case_start", "case": "TC-02", "title": "Wrong MPIN"},
        {"type": "check", "kind": "db", "ok": False, "what": "attempts incremented", "detail": "got 0", "case": "TC-02"},
        {"type": "note", "kind": "bug", "text": "no error shown", "case": "TC-02"},
        {"type": "case_end", "case": "TC-02", "status": "fail", "reason": "failed: attempts incremented",
         "evidence": ["shots/b.png", "logs/l.txt"]},
        {"type": "case_start", "case": "TC-03", "title": "Offline"},
        {"type": "case_end", "case": "TC-03", "status": "blocked", "reason": "network down"},
        {"type": "case_start", "case": "TC-04", "title": "Out of scope"},
        {"type": "case_end", "case": "TC-04", "status": "skipped", "reason": "not in this release"},
        {"type": "check", "kind": "ui", "ok": True, "what": "loose check", "case": None},
    ]
    with run.events_path.open("w") as f:
        for i, e in enumerate(events):
            f.write(json.dumps({"t": f"2026-10-21T10:0{i // 6}:{(i % 6) * 10:02d}.000Z", **e}) + "\n")
    return run


def node_eval(expr: str):
    a = HTML.index("const fmtDur")
    script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
              HTML.index("// ------------------------------------------------------------------ timeline")]
              + f"\nconsole.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


class Phase0UndeclaredRunsTest(unittest.TestCase):
    """PH0: a run that declares nothing reports exactly as it did before this feature."""

    def setUp(self):
        temp_home(self)
        no_transcripts = mock.patch.object(usage, "transcripts", return_value=[])
        no_transcripts.start()
        self.addCleanup(no_transcripts.stop)

    def test_ph0_1_summary_line_is_unchanged(self):
        self.assertEqual(rpt.summary_line(rpt.summarize(legacy_run())),
                         "4 case(s): ✅ 1 pass  ❌ 1 fail  ⛔ 1 blocked  ⏭ 1 skipped  | checks 2/3 passed")

    def test_ph0_1_summary_line_with_no_cases(self):
        run = Run.create("empty", {"package": "com.example.app"})
        self.assertEqual(rpt.summary_line(rpt.summarize(run)), "0 case(s): none recorded  | checks 0/0 passed")

    def test_ph0_2_report_is_unchanged(self):
        run = legacy_run()
        md = rpt.to_markdown(run, rpt.summarize(run))
        self.assertEqual(md, (GOLDEN / "legacy_report.md").read_text())

    def test_ph0_2_html_report_is_unchanged(self):
        run = legacy_run()
        page = rpt.to_html(run, rpt.summarize(run))
        self.assertEqual(page, (GOLDEN / "legacy_report.html").read_text())

    def test_ph0_5_example_plans_parse_as_before(self):
        p = plan.load(str(ROOT / "examples" / "settings.plan"))
        self.assertEqual([(c.id, c.title) for c in p.cases],
                         [("TC-01", "Network & internet opens"), ("TC-02", "Search finds Wi-Fi settings"),
                          ("TC-03", "Back from search returns to Settings")])

    @unittest.skipUnless(shutil.which("node"), "needs node to run the dashboard's layout()")
    def test_ph0_7_dashboard_timeline_is_unchanged(self):
        events = [json.loads(line) for line in legacy_run().events_path.read_text().splitlines()]
        blocks = node_eval(f"layout({json.dumps(events)}).map((b) => [b.kind, b.e ? b.e.type : null])")
        self.assertEqual(blocks, [["case_start", "case_start"], ["step", "tap"], ["step", "check"],
                                  ["case_end", "case_end"], ["case_start", "case_start"], ["step", "check"],
                                  ["step", "note"], ["case_end", "case_end"], ["case_start", "case_start"],
                                  ["case_end", "case_end"], ["case_start", "case_start"], ["case_end", "case_end"],
                                  ["step", "check"]])


if __name__ == "__main__":
    unittest.main()
