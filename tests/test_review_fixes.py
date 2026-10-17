"""Regression tests for the full-repo review: each test names the bug it pins."""

import os
import subprocess
from pathlib import Path
from unittest import mock

from fakedevice import PACKAGE, node, tnazar
from teesri_nazar import cli, live
from teesri_nazar.session import Run, get_active

from test_cli import CliTestCase
from test_plan import LOGIN, PlanRunBase


def adb_exit(code: int):
    return mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], code))


class AdbExitCodeTest(PlanRunBase):
    def test_a_failing_adb_command_is_a_plain_failure_whatever_its_own_code(self):
        self.start()
        with adb_exit(5):
            code, _, _ = tnazar("adb", "shell", "false")
        self.assertEqual(code, cli.EXIT_FAIL)  # not 5, which means "paused" to tnazar
        event = [e for e in get_active()[0].events() if e["type"] == "adb"][-1]
        self.assertEqual(event["exit"], 5)  # adb's own code is still recorded

    def test_an_adb_step_exiting_5_in_a_plan_fails_its_case_once(self):
        with adb_exit(5) as run:
            _, result = self.run_plan('''
case TC-01 "adb fails"
  adb shell false
  check Login
''', expect=1)
        self.assertEqual(run.call_count, 1)  # no endless retry
        self.assertEqual(self.cases(result), {"TC-01": "fail"})


class ReplayOddChecksTest(CliTestCase):
    def test_replay_does_not_crash_on_checks_without_a_selector(self):
        self.start()
        self.ok("case", "start", "TC-01")
        self.ok("check", "--result", "pass", "--what", "verified by hand")  # kind defaults to ui
        get_active()[0].log({"type": "check", "kind": "ui", "ok": False, "case": "TC-01",
                             "what": "line 3: tap Nowhere: not found"})  # as the plan runner records a failed step
        self.ok("case", "end")
        self.ok("stop")
        code, out, err = tnazar("replay", "demo")
        self.assertNotIn("Traceback", out + err)
        self.assertIn(code, (0, 1))


class RunnerRobustnessTest(PlanRunBase):
    def test_coordinates_or_an_index_are_not_valid_if_targets(self):
        from teesri_nazar import plan
        for target in ('"#3"', "1,2", '""'):
            with self.assertRaises(plan.PlanError):
                plan.parse(f"app a.b\ncase T1\n  if {target}\n    key back\n  end\n  check X\n", "x.plan")

    def test_a_case_title_that_starts_with_a_dash_works(self):
        _, run = self.run_plan('''
case TC-01 "-smoke login"
  check Login
''')
        self.assertEqual(self.cases(run), {"TC-01": "pass"})

    def test_an_unexpected_error_mid_run_closes_the_case_and_the_run(self):
        from teesri_nazar import runner
        from teesri_nazar.adb import AdbError
        with mock.patch.object(runner.Executor, "visible", side_effect=AdbError("device offline")):
            out, run = self.run_plan('''
case TC-01 "branch"
  if Login
    key back
  end
  check Login
''', expect=cli.EXIT_ERROR)
        self.assertIsNone(get_active())
        self.assertEqual(self.cases(run), {"TC-01": "blocked"})
        self.assertIn("device offline", out)


class SecretOnScreenTest(CliTestCase):
    """A secret typed into a plain (non-password) field is visible on screen and in its UI tree."""

    def setUp(self):
        super().setUp()
        Path(self.home, ".env").write_text("APP_OTP=482913\n")
        self.adb.app.screens["login"].append(node(id="otp_box", text="482913", cls="android.widget.EditText",
                                                  bounds=(60, 1100, 1020, 1200)))

    def test_observe_does_not_print_it(self):
        self.start()
        out = self.ok("observe")
        self.assertNotIn("482913", out)
        self.assertIn("otp_box", out)

    def test_saved_ui_trees_do_not_contain_it(self):
        run = self.start()
        self.ok("observe")
        self.ok("check", "Nowhere", "--timeout", "0") if False else tnazar("check", "Nowhere", "--timeout", "0")
        saved = [p.read_text() for p in run.path.rglob("*.xml")] + [Path(self.home, ".tnazar", "last-screen.xml").read_text()]
        self.assertTrue(saved)
        self.assertFalse(any("482913" in x for x in saved))

    def test_not_found_message_does_not_list_it(self):
        self.start()
        code, out, _ = tnazar("tap", "Nowhere", "--wait", "0")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertNotIn("482913", out)


class StrictCheckTest(CliTestCase):
    def test_a_check_does_not_pass_on_a_longer_label_that_says_the_opposite(self):
        self.adb.app.screens["login"].append(node(text="Payment unsuccessful", bounds=(60, 1300, 1020, 1400)))
        self.start()
        code, _, _ = tnazar("check", "successful", "--timeout", "0")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.ok("check", "~unsuccessful", "--timeout", "0")  # a partial match is still available, explicitly
        self.ok("check", "Payment unsuccessful", "--timeout", "0")

    def test_a_check_still_finds_a_label_kept_in_content_desc(self):
        self.ok("check", "Phone number", "--timeout", "0")


class StrictReplayCheckTest(CliTestCase):
    def test_a_replayed_check_matches_as_strictly_as_the_original(self):
        run = self.start()
        self.ok("case", "start", "TC-01")
        run.log({"type": "check", "kind": "ui", "ok": True, "what": '"successful" is visible',
                 "selector": {"text": "successful"}, "case": "TC-01"})
        self.ok("case", "end", "pass")
        self.ok("stop")
        self.adb.app.screens["login"].append(node(text="Payment unsuccessful", bounds=(60, 1300, 1020, 1400)))
        code, out, _ = tnazar("replay", "demo")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn('FAIL "successful" is visible', out)
