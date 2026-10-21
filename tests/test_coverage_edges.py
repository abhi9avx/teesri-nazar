"""Coverage ledger edge cases (docs/test-plan-coverage.md; PRD §11 S10)."""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from fakedevice import PACKAGE, tnazar  # noqa: E402
from test_cli import CliTestCase  # noqa: E402
from test_coverage import legacy_run, temp_home  # noqa: E402
from test_plan import LOGIN, PlanRunBase  # noqa: E402
from test_replay import ReplayTestCase  # noqa: E402
from teesri_nazar import coverage, ui, usage  # noqa: E402
from teesri_nazar import report as rpt  # noqa: E402
from teesri_nazar.session import Run, get_active, runs_dir  # noqa: E402

TEN = "".join(f'case TC-{i:02d} "case {i}"\n  check Login\n' for i in range(1, 11))


def rows(run):
    return {c["id"]: (c["status"], c["reason"]) for c in rpt.summarize(run)["cases"]}


class InterruptTest(PlanRunBase):
    def test_ctrl_c_mid_run_then_stop_still_accounts_for_every_case(self):
        from teesri_nazar import runner
        body = TEN.replace('case TC-03 "case 3"\n  check Login\n',
                           'case TC-03 "case 3"\n  if Login\n    key back\n  end\n  check Login\n')
        path = os.path.join(self.home, "demo.plan")
        Path(path).write_text(LOGIN + body)
        with mock.patch.object(runner.Executor, "visible", side_effect=KeyboardInterrupt):
            code, _, _ = tnazar("run", path)
        self.assertEqual(code, 130)
        self.ok("stop")
        r = rows(Run.load("demo"))
        self.assertEqual(len(r), 10)
        self.assertEqual(r["TC-03"], ("blocked", "run stopped before the case finished"))
        self.assertEqual(r["TC-04"][0], "not_run")


class FromOnAFreshRunTest(PlanRunBase):
    def test_from_without_an_open_run_requests_the_rest_and_says_so(self):
        out, run = self.run_plan(TEN, "--from", "TC-04")
        self.assertEqual(len(coverage.requested(run.events())), 7)
        self.assertIn("7 of 10 plan cases selected (--from TC-04)", rpt.to_markdown(run, rpt.summarize(run)))
        self.assertIn("verified 7/7", out)


class TextEdgesTest(CliTestCase):
    def test_unicode_and_very_long_expected_results_survive_to_the_report(self):
        long = "The bot explains the fee " + "and keeps explaining " * 30 + "in the end."
        run = self.start()
        self.ok("case", "plan", "TC-01", "₹ हिंदी ✅", "--expect", "₹500 का शुल्क दिखे 🎉", "--expect", long)
        self.ok("case", "start", "TC-01")
        self.ok("check", "Login", "--for", "E1,E2")
        self.ok("case", "end")
        self.ok("stop")
        md = (run.path / "report.md").read_text()
        self.assertIn("✅ E1 ₹500 का शुल्क दिखे 🎉", md)
        self.assertIn(long, md)
        self.assertIn("₹ हिंदी ✅", (run.path / "report.html").read_text())


class ManyCasesTest(PlanRunBase):
    def test_a_200_case_plan_reports_every_case(self):
        body = "".join(f'case C{i:03d} "case {i}"\n  expect "Login shown"\n  check Login --for E1\n'
                       for i in range(1, 201))
        began = time.monotonic()
        out, run = self.run_plan(body)
        self.assertIn("200 case(s): ✅ 200 pass  | checks 200/200 passed  | verified 200/200", out)
        self.assertLess(time.monotonic() - began, 60)


class ReplayOfAReplayTest(ReplayTestCase):
    def test_the_requested_list_and_not_run_rows_survive_two_replays(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "plan", "TC-01", "Login", "--expect", "Home shown")
        self.ok("case", "plan", "TC-02", "Never ran")
        self.ok("case", "start", "TC-01")
        self.ok("type", "9876543210", "--into", "id=phone_input")
        self.ok("tap", "Login")
        self.ok("check", "Hi Asha", "--for", "E1")
        self.ok("case", "end")
        self.ok("stop")
        self.fresh_device()
        _, _, first = self.replay()
        self.fresh_device()
        code, out, _ = tnazar("replay", first.name)
        second = Run.load(sorted(p.name for p in runs_dir().iterdir() if p.name.startswith(first.name + "-replay-"))[-1])
        r = rows(second)
        self.assertEqual([c["id"] for c in coverage.requested(second.events())], ["TC-01", "TC-02"])
        self.assertEqual(r["TC-02"], ("not_run", "not run in the recorded run"))
        # A replay records its checks but not its taps, so a replay of it has no steps to repeat
        # (older limitation of replay): it must fail, never pass on nothing.
        self.assertNotEqual(r["TC-01"][0], "pass")
        self.assertEqual(code, 1)


class OldRunsInTheDashboardTest(unittest.TestCase):
    def setUp(self):
        temp_home(self)
        p = mock.patch.object(usage, "transcripts", return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def test_the_run_list_shows_an_old_run_exactly_as_before(self):
        legacy_run()
        [entry] = [r for r in ui.Dashboard(0).runs() if r["name"] == "legacy"]
        self.assertEqual(entry["summary"], "4 case(s): ✅ 1 pass  ❌ 1 fail  ⛔ 1 blocked  ⏭ 1 skipped  | checks 2/3 passed")


if __name__ == "__main__":
    unittest.main()
