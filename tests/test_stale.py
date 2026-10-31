"""Stale screens: a setup step that did not reach its state blocks the case (`require`) instead of
letting checks run, and pass or fail, on a screen left over from an earlier case."""

from fakedevice import tnazar
from test_plan import PlanRunBase


class RequireTest(PlanRunBase):
    def end(self, run, case="TC-01") -> dict:
        return next(e for e in run.events() if e["type"] == "case_end" and e["case"] == case)

    def test_a_setup_that_did_not_reach_its_state_blocks_the_case_and_stops_it(self):
        out, run = self.run_plan('''
case TC-01 "New chat"
  require "Nowhere" --timeout 1
  key back
  check Login
''', expect=1)
        end = self.end(run)
        self.assertEqual((end["status"], end["cause"]), ("blocked", "precondition"))
        self.assertIn('precondition not met: "Nowhere" is visible', end["reason"])
        self.assertFalse(any(e["type"] == "key" for e in run.events()))  # the case stopped at the setup

    def test_a_setup_that_holds_lets_the_case_go_on(self):
        _, run = self.run_plan('''
case TC-01 "Login"
  require Login --timeout 1
  check Login
''')
        self.assertEqual(self.end(run)["status"], "pass")

    def test_require_in_a_flow_blocks_every_case_that_runs_it(self):
        _, run = self.run_plan('''
flow fresh
  require "Nowhere" --timeout 1
end
case TC-01 "a"
  run fresh
  check Login
''', expect=1)
        self.assertEqual((self.end(run)["status"], self.end(run)["cause"]), ("blocked", "precondition"))

    def test_a_setup_is_never_an_expected_result(self):
        path = self.home + "/bad.plan"
        with open(path, "w") as f:
            f.write('app com.example.app\nname bad\n\ncase TC-01 "a"\n  expect "Home shows"\n'
                    '  require Login --for E1\n  check Login --for E1\n')
        code, out, err = tnazar("run", path)
        self.assertEqual(code, 2, out + err)
        self.assertIn("require", out + err)

    def test_a_require_alone_is_not_a_check(self):
        path = self.home + "/bad.plan"
        with open(path, "w") as f:
            f.write('app com.example.app\nname bad\n\ncase TC-01 "a"\n  require Login\n')
        code, out, err = tnazar("run", path)
        self.assertEqual(code, 2, out + err)
        self.assertIn("has no `check`", out + err)


class RequireReviewTest(PlanRunBase):
    def refused(self, body: str) -> str:
        path = self.home + "/bad.plan"
        with open(path, "w") as f:
            f.write('app com.example.app\nname bad\n\n' + body)
        code, out, err = tnazar("run", path)
        self.assertEqual(code, 2, out + err)
        return out + err

    def test_require_takes_no_expected_result_in_any_spelling_and_no_manual_result(self):
        for opt in ("--for=E1", "--fo E1"):
            self.assertIn("require", self.refused(f'case TC-01 "a"\n  expect "x"\n  require Login {opt}\n'
                                                  f'  check Login --for E1\n'))
        self.assertIn("require", self.refused('case TC-01 "a"\n  require --result fail --kind db --what row\n'
                                              '  check Login\n'))

    def test_a_require_reached_through_a_flow_inside_retry_is_refused(self):
        self.assertIn("require", self.refused('flow f\n  require Login\nend\ncase TC-01 "a"\n  retry 2\n'
                                              '    run f\n  end\n  check Login\n'))

    def test_a_passing_setup_check_never_proves_an_expected_result(self):
        from teesri_nazar import coverage
        states = coverage.expectation_states(["Home shows"], [{"type": "check", "ok": True, "for": ["E1"],
                                                               "setup": True, "what": "x"}])
        self.assertEqual(states[0]["state"], "unverified")

    def test_a_flow_that_failed_on_its_require_blocks_later_cases_as_a_precondition(self):
        _, run = self.run_plan('''
flow fresh
  require "Nowhere" --timeout 1
end
case TC-01 "a"
  run fresh
  check Login
case TC-02 "b"
  run fresh
  check Login
case TC-03 "c"
  run fresh
  check Login
''', expect=1)
        ends = {e["case"]: e for e in run.events() if e["type"] == "case_end"}
        self.assertEqual(ends["TC-03"]["cause"], "precondition")


class RequireReplayTest(PlanRunBase):
    def test_a_require_that_fails_on_replay_blocks_the_case_as_a_precondition(self):
        from teesri_nazar.session import Run
        self.start()
        for argv in (("case", "start", "TC-01", "a"), ("check", "Login", "--setup"), ("check", "Login"),
                     ("case", "end"), ("stop",)):
            self.assertEqual(tnazar(*argv)[0], 0)
        name = sorted(p.name for p in (Run.load("demo").path.parent).iterdir())[0]
        self.adb.current = "home"  # the setup no longer holds
        code, out, err = tnazar("replay", name)
        replayed = sorted(p for p in Run.load(name).path.parent.iterdir() if p.name != name)[-1]
        end = next(e for e in Run(replayed).events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end.get("cause")), ("blocked", "precondition"), out + err)


class FinalReviewTest(PlanRunBase):
    def refused(self, body: str) -> str:
        path = self.home + "/bad.plan"
        with open(path, "w") as f:
            f.write('app com.example.app\nname bad\n\n' + body)
        code, out, err = tnazar("run", path)
        self.assertEqual(code, 2, out + err)
        return out + err

    def test_check_setup_takes_no_manual_result_and_no_expected_result(self):
        self.start()
        tnazar("case", "plan", "TC-01", "a", "--expect", "Home shows")
        tnazar("case", "start", "TC-01", "a")
        code, out, err = tnazar("check", "--setup", "--result", "fail", "--kind", "db", "--what", "row")
        self.assertEqual(code, 2, out + err)
        code, out, err = tnazar("check", "Login", "--setup", "--for", "E1")
        self.assertEqual(code, 2, out + err)

    def test_a_plan_writes_a_setup_as_require_not_check_setup(self):
        self.assertIn("require", self.refused('case TC-01 "a"\n  check Login --setup\n  check Login\n'))

    def test_a_check_on_an_empty_read_leaves_its_expected_result_unverified(self):
        from teesri_nazar import coverage
        states = coverage.expectation_states(["Order details show"], [
            {"type": "check", "ok": False, "for": ["E1"], "what": "x", "blank_screen": True}])
        self.assertEqual(states[0]["state"], "unverified")

    def test_a_flow_that_dies_on_a_require_in_a_flow_it_runs_blocks_later_cases_as_a_precondition(self):
        _, run = self.run_plan('''
flow inner
  require "Nowhere" --timeout 1
end
flow outer
  run inner
end
case TC-01 "a"
  run outer
  check Login
case TC-02 "b"
  run outer
  check Login
case TC-03 "c"
  run outer
  check Login
''', expect=1)
        ends = {e["case"]: e for e in run.events() if e["type"] == "case_end"}
        self.assertEqual(ends["TC-03"]["cause"], "precondition")


class ReplayJudgmentTest(PlanRunBase):
    def replay(self, *steps):
        from teesri_nazar.session import Run
        self.start()
        for argv in (("case", "start", "TC-01", "a"), *steps, ("case", "end"), ("stop",)):
            tnazar(*argv)
        name = sorted(p.name for p in Run.load("demo").path.parent.iterdir())[0]
        return name

    def replayed_end(self, name):
        from teesri_nazar.session import Run
        code, out, err = tnazar("replay", name)
        newest = sorted(p for p in Run.load(name).path.parent.iterdir() if p.name != name)[-1]
        return next(e for e in Run(newest).events() if e["type"] == "case_end")

    def test_a_check_on_an_empty_read_during_replay_is_blocked_as_on_a_live_run(self):
        name = self.replay(("check", "Login"))
        self.adb.app.screens["empty"] = []
        self.adb.current = "empty"
        end = self.replayed_end(name)
        self.assertEqual((end["status"], end.get("cause")), ("blocked", "step"))

    def test_a_setup_that_fails_on_replay_says_so_in_the_reason(self):
        name = self.replay(("check", "Login", "--setup"), ("check", "Login"))
        self.adb.current = "home"
        end = self.replayed_end(name)
        self.assertIn("the setup did not hold", end["reason"])


class ClosedCaseTest(PlanRunBase):
    def test_stopping_a_run_does_not_hide_a_check_that_already_failed(self):
        self.start()
        for argv in (("case", "start", "TC-01", "a"), ("check", "Payment successful", "--timeout", "1"), ("stop",)):
            tnazar(*argv)
        from teesri_nazar.session import Run
        end = next(e for e in Run.load("demo").events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end.get("cause")), ("fail", "app"))


class PrReviewTest(PlanRunBase):
    def test_stop_never_hides_a_crash_in_the_open_case(self):
        from test_cli import CRASH
        from teesri_nazar.session import Run
        self.start()
        tnazar("case", "start", "TC-01", "a")
        tnazar("check", "Login")
        self.adb.crash_log = CRASH
        tnazar("stop")
        end = next(e for e in Run.load("demo").events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end.get("cause")), ("fail", "app"))
        self.assertIn("app crashed", end["reason"])

    def test_replay_fails_the_case_on_the_error_screen_the_plan_named(self):
        from teesri_nazar.session import Run
        self.start()
        for argv in (("case", "start", "TC-01", "a"), ("wait", "Login", "--or", "Pay now", "--timeout", "1"),
                     ("check", "Login"), ("case", "end"), ("stop",)):
            tnazar(*argv)
        name = sorted(p.name for p in Run.load("demo").path.parent.iterdir())[0]
        self.adb.current = "home"  # the screen the plan named as failure
        tnazar("replay", name)
        newest = sorted(p for p in Run.load(name).path.parent.iterdir() if p.name != name)[-1]
        end = next(e for e in Run(newest).events() if e["type"] == "case_end")
        self.assertEqual((end["status"], end.get("cause")), ("fail", "app"))

    def test_require_takes_no_setup_flag_of_its_own(self):
        path = self.home + "/bad.plan"
        with open(path, "w") as f:
            f.write('app com.example.app\nname bad\n\ncase TC-01 "a"\n  require Login --setup\n  check Login\n')
        code, out, err = tnazar("run", path)
        self.assertEqual(code, 2, out + err)
