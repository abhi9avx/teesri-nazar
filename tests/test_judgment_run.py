"""The judgment through `tnazar run` on the fake device: a step that could not run blocks its case
with the real error, an error screen the plan named fails it, and a slow step is never the reason."""

from test_plan import PlanRunBase


class StepFailureVerdictTest(PlanRunBase):
    def end(self, run, case="TC-01") -> dict:
        return next(e for e in run.events() if e["type"] == "case_end" and e["case"] == case)

    def test_a_target_not_on_screen_blocks_the_case_and_names_the_miss_not_the_slowness(self):
        _, run = self.run_plan('''
case TC-01 "Help"
  tap Nowhere --wait 6
  check Login
''', expect=1)
        end = self.end(run)
        self.assertEqual((end["status"], end["cause"]), ("blocked", "step"))
        self.assertIn('not found: "Nowhere"', end["reason"])
        self.assertNotIn("SLOW", end["reason"])
        [step] = [e["step"] for e in run.events() if e["type"] == "check" and "step" in e]
        self.assertEqual((step["cmd"], step["code"]), ("tap", 1))

    def test_the_error_screen_a_plan_names_with_or_fails_the_case_as_the_apps(self):
        _, run = self.run_plan('''
case TC-01 "Home"
  wait Nowhere --or Login --timeout 2
  check Login
''', expect=1)
        end = self.end(run)
        self.assertEqual((end["status"], end["cause"]), ("fail", "app"))


class BlankScreenVerdictTest(PlanRunBase):
    def test_a_check_on_an_empty_screen_read_blocks_the_case_instead_of_failing_the_app(self):
        self.adb.app.screens["empty"] = []
        self.adb.current = "empty"
        _, run = self.run_plan('''
case TC-01 "Details"
  check "Order details for your purchase" --timeout 1
''', expect=1)
        [check] = [e for e in run.events() if e["type"] == "check"]
        self.assertTrue(check["blank_screen"])
        end = next(e for e in run.events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end["cause"]), ("blocked", "step"))
        self.assertIn("the screen could not be read", end["reason"])

    def test_a_white_screen_with_no_text_is_the_apps_failure(self):
        from fakedevice import node
        self.adb.app.screens["white"] = [node(id="root", clickable=False, bounds=(0, 0, 1080, 2400))]
        self.adb.current = "white"
        _, run = self.run_plan('''
case TC-01 "Home"
  check "Home" --timeout 1
''', expect=1)
        [check] = [e for e in run.events() if e["type"] == "check"]
        self.assertNotIn("blank_screen", check)
        end = next(e for e in run.events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end["cause"]), ("fail", "app"))

    def test_a_spinner_that_never_goes_away_is_the_apps_failure(self):
        from fakedevice import node
        self.adb.app.screens["spinning"] = [node(id="loader", clickable=False, bounds=(500, 1100, 580, 1180))]
        self.adb.current = "spinning"
        _, run = self.run_plan('''
case TC-01 "Loads"
  check id=loader --gone --timeout 1
''', expect=1)
        end = next(e for e in run.events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end["cause"]), ("fail", "app"))

    def test_a_check_that_misses_on_a_screen_with_text_still_fails_the_app(self):
        _, run = self.run_plan('''
case TC-01 "Login"
  check "Payment successful" --timeout 1
''', expect=1)
        [check] = [e for e in run.events() if e["type"] == "check"]
        self.assertNotIn("blank_screen", check)
        end = next(e for e in run.events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end["cause"]), ("fail", "app"))


class RetryEvidenceTest(PlanRunBase):
    def test_an_error_screen_in_any_retry_attempt_fails_the_case(self):
        _, run = self.run_plan('''
case TC-01 "Home"
  retry 2
    if Login
      tap Login
      wait Nowhere --or "Pay now" --timeout 1
    else
      wait Nowhere --timeout 1
    end
  end
  check "Pay now"
''', expect=1)
        end = next(e for e in run.events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end["cause"]), ("fail", "app"))

    def test_a_precondition_block_names_the_step(self):
        _, run = self.run_plan('''
flow enter
  tap Nowhere
end
case TC-01 "a"
  run enter
  check Login
case TC-02 "b"
  run enter
  check Login
case TC-03 "c"
  run enter
  check Login
''', expect=1)
        ends = {e["case"]: e for e in run.events() if e["type"] == "case_end"}
        self.assertEqual(ends["TC-03"].get("cause"), "step")


class CauseShownTest(PlanRunBase):
    def test_the_report_says_why_a_case_did_not_pass(self):
        from teesri_nazar import report as rpt
        self.assertEqual(rpt.result_label("blocked", "step"), "blocked · step could not run")
        self.assertEqual(rpt.result_label("blocked", "unverified"), "blocked · not verified")
        self.assertEqual(rpt.result_label("blocked", "device"), "blocked · device")
        self.assertEqual(rpt.result_label("fail", "app"), "fail · app")
        self.assertEqual(rpt.result_label("pass", None), "pass")
        _, run = self.run_plan('''
case TC-01 "Help"
  tap Nowhere
  check Login
''', expect=1)
        md = (run.path / "report.md").read_text()
        html = (run.path / "report.html").read_text()
        self.assertIn("blocked · step could not run", md)
        self.assertIn("blocked · step could not run", html)

    def test_the_dashboard_says_why_a_case_did_not_pass(self):
        import json
        import shutil
        import subprocess
        import unittest
        if not shutil.which("node"):
            raise unittest.SkipTest("needs node")
        from test_fix_ui import HTML
        a = HTML.index("const fmtDur")
        script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
                  HTML.index("// ------------------------------------------------------------------ timeline")]
                  + "\nconsole.log(JSON.stringify([resultLabel('blocked', 'device'), resultLabel('fail', 'app'),"
                    " resultLabel('pass'), resultLabel('blocked'), resultLabel('blocked', 'step')]));")
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["BLOCKED · DEVICE", "FAIL · APP", "PASS", "BLOCKED", "BLOCKED · STEP COULD NOT RUN"])
