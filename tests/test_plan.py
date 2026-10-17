"""Plan files: parsing and validation (nothing touches the device), then running them."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from teesri_nazar import plan

GOOD = '''
# Settings smoke test
app com.android.settings
name settings-smoke

flow open_network
  tap "Network & internet" --wait 5
end

case TC-01 "Network page opens"
  launch --restart
  say "Opening Network & internet"
  run open_network
  check "Internet"

case TC-02 "Search works"
  if "Search Settings"
    tap "Search Settings"
  else
    key back
  end
  retry 2
    type "wifi" --into id=search_src_text
    wait "Wi-Fi" --or "No results" --timeout 5
  end
  wait-online --timeout 20
  check "Wi-Fi"
'''


def parse(text: str) -> plan.Plan:
    return plan.parse(text, "x.plan")


class ParseTest(unittest.TestCase):
    def test_header_flows_and_cases(self):
        p = parse(GOOD)
        self.assertEqual((p.app, p.name), ("com.android.settings", "settings-smoke"))
        self.assertEqual([c.id for c in p.cases], ["TC-01", "TC-02"])
        self.assertEqual(p.cases[0].title, "Network page opens")
        self.assertEqual(list(p.flows), ["open_network"])
        self.assertEqual(p.flows["open_network"].body[0].argv, ["tap", "Network & internet", "--wait", "5"])

    def test_blocks_nest_inside_a_case(self):
        body = parse(GOOD).cases[1].body
        self.assertIsInstance(body[0], plan.If)
        self.assertEqual(body[0].target, "Search Settings")
        self.assertEqual([s.argv[0] for s in body[0].then], ["tap"])
        self.assertEqual([s.argv[0] for s in body[0].other], ["key"])
        self.assertIsInstance(body[1], plan.Retry)
        self.assertEqual((body[1].times, len(body[1].body)), (2, 2))
        self.assertEqual(body[2].argv, ["wait-online", "--timeout", "20"])

    def test_steps_remember_their_line(self):
        step = parse(GOOD).cases[0].body[0]
        self.assertEqual((step.line, step.argv), (11, ["launch", "--restart"]))

    def test_name_defaults_to_the_file_name(self):
        self.assertEqual(plan.parse("app a.b\ncase T1\n  check X\n", "plans/login-smoke.plan").name, "login-smoke")


class ValidationTest(unittest.TestCase):
    def error(self, text: str) -> str:
        with self.assertRaises(plan.PlanError) as ctx:
            parse(text)
        return str(ctx.exception)

    def test_app_is_required(self):
        self.assertIn("app", self.error("case T1\n  key back\n"))

    def test_unknown_command_names_the_line(self):
        self.assertIn("line 3", self.error("app a.b\ncase T1\n  teleport home\n"))

    def test_commands_that_manage_runs_are_not_steps(self):
        self.assertIn("not allowed in a plan", self.error("app a.b\ncase T1\n  stop\n"))

    def test_a_bad_flag_is_caught_before_running(self):
        msg = self.error("app a.b\ncase T1\n  tap Login --wiat 5\n")
        self.assertIn("line 3", msg)
        self.assertIn("--wiat", msg)

    def test_a_block_needs_its_end(self):
        self.assertIn("line 3", self.error("app a.b\ncase T1\n  retry 2\n    key back\n"))

    def test_an_end_without_a_block(self):
        self.assertIn("line 4", self.error("app a.b\ncase T1\n  key back\n  end\n"))

    def test_run_needs_a_defined_flow(self):
        self.assertIn("login", self.error("app a.b\ncase T1\n  run login\n"))

    def test_flows_cannot_call_themselves(self):
        self.assertIn("loop", self.error("app a.b\nflow a\n  run b\nend\nflow b\n  run a\nend\ncase T1\n  run a\n"))

    def test_steps_must_be_inside_a_case_or_flow(self):
        self.assertIn("line 2", self.error("app a.b\nkey back\ncase T1\n  key back\n"))

    def test_case_ids_are_unique(self):
        self.assertIn("TC-01", self.error("app a.b\ncase TC-01\n  key back\ncase TC-01\n  key back\n"))

    def test_an_empty_case_is_an_error(self):
        self.assertIn("T1", self.error("app a.b\ncase T1\ncase T2\n  key back\n"))

    def test_a_case_without_any_check_is_caught(self):
        msg = self.error("app a.b\nflow f\n  tap X\nend\ncase T1\n  run f\n")
        self.assertIn("T1", msg)
        self.assertIn("check", msg)

    def test_a_check_inside_a_flow_counts(self):
        parse("app a.b\nflow f\n  check X\nend\ncase T1\n  run f\n")

    def test_retry_needs_a_count(self):
        self.assertIn("line 3", self.error("app a.b\ncase T1\n  retry many\n    key back\n  end\n"))

    def test_load_reads_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "smoke.plan")
            f.write_text(GOOD)
            self.assertEqual(plan.load(f).name, "settings-smoke")


if __name__ == "__main__":
    unittest.main()


from fakedevice import PACKAGE, tnazar  # noqa: E402
from teesri_nazar import live  # noqa: E402
from teesri_nazar.session import Run, get_active, runs_dir  # noqa: E402

from test_cli import CliTestCase  # noqa: E402

LOGIN = f"""
app {PACKAGE}
name demo

flow login
  type "9876543210" --into id=phone_input
  tap Login
end
"""


class RetryRuleTest(unittest.TestCase):
    def test_check_is_not_allowed_inside_retry(self):
        with self.assertRaises(plan.PlanError) as ctx:
            parse("app a.b\ncase T1\n  retry 2\n    check Home\n  end\n")
        self.assertIn("line 4", str(ctx.exception))
        self.assertIn("wait", str(ctx.exception))


class PlanRunBase(CliTestCase):
    def run_plan(self, body: str, *flags: str, expect: int = 0):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + body)
        code, out, err = tnazar("run", path, *flags)
        self.assertEqual(code, expect, out + err)
        return out, Run.load("demo")

    def cases(self, run):
        return {e["case"]: e["status"] for e in run.events() if e["type"] == "case_end"}


class RunTest(PlanRunBase):

    def test_a_passing_plan_runs_every_case_and_writes_the_report(self):
        out, run = self.run_plan('''
case TC-01 "Login"
  run login
  check "Pay now"
case TC-02 "Back to login"
  key back
  check Login
''')
        self.assertEqual(self.cases(run), {"TC-01": "pass", "TC-02": "pass"})
        self.assertIsNone(get_active())
        self.assertTrue((run.path / "report.html").exists())
        self.assertIn("TC-01", out)
        self.assertIn("2 case(s): ✅ 2 pass", out)

    def test_a_failed_check_is_recorded_and_the_case_goes_on(self):
        _, run = self.run_plan('''
case TC-01 "Login"
  check "Nowhere" --timeout 1
  run login
  check "Pay now"
''', expect=1)
        self.assertEqual(self.cases(run), {"TC-01": "fail"})
        self.assertEqual(self.adb.current, "home")  # steps after the failed check still ran

    def test_a_failed_action_skips_the_rest_of_its_case_only(self):
        out, run = self.run_plan('''
case TC-01 "Broken"
  tap "Nowhere"
  run login
  check "Pay now"
case TC-02 "Login"
  run login
  check "Pay now"
''', expect=1)
        self.assertEqual(self.cases(run), {"TC-01": "fail", "TC-02": "pass"})
        self.assertIn("tap Nowhere", out)  # the failing step and what the screen showed
        self.assertIn('not found: "Nowhere"', out)
        failed = [e for e in run.events() if e["type"] == "check" and not e["ok"]]
        self.assertIn("line", failed[0]["what"])

    def test_retry_repeats_a_block_until_it_passes(self):
        self.adb.drop_taps = 1  # the first tap on Login is lost
        _, run = self.run_plan('''
case TC-01 "Login"
  retry 2
    tap Login
    wait "Pay now" --timeout 1
  end
  check "Pay now"
''')
        self.assertEqual(self.cases(run), {"TC-01": "pass"})

    def test_retry_that_never_passes_fails_the_case(self):
        out, run = self.run_plan('''
case TC-01 "Login"
  retry 2
    wait "Nowhere" --timeout 1
  end
  check "Nowhere"
''', expect=1)
        self.assertEqual(self.cases(run), {"TC-01": "fail"})
        self.assertIn("2 attempts", out)

    def test_if_takes_the_branch_that_matches_the_screen(self):
        _, run = self.run_plan('''
case TC-01 "Login from wherever the app is"
  if "Pay now"
    say "already logged in"
  else
    run login
  end
  check "Pay now"
''')
        self.assertEqual(self.cases(run), {"TC-01": "pass"})
        branch = [e for e in run.events() if e["type"] == "if"][0]
        self.assertEqual((branch["target"], branch["found"]), ("Pay now", False))

    def test_only_and_from_pick_cases(self):
        body = '''
case TC-01 "one"
  check Login
case TC-02 "two"
  check Login
case TC-03 "three"
  check Login
'''
        _, run = self.run_plan(body, "--only", "TC-02")
        self.assertEqual(list(self.cases(run)), ["TC-02"])
        _, run = self.run_plan(body, "--from", "TC-02")
        self.assertEqual(list(self.cases(Run.load(sorted(p.name for p in runs_dir().iterdir())[-1]))), ["TC-02", "TC-03"])

    def test_stop_on_fail_ends_the_run_at_the_first_failed_case(self):
        _, run = self.run_plan('''
case TC-01 "fails"
  check "Nowhere" --timeout 1
case TC-02 "never runs"
  check Login
''', "--stop-on-fail", expect=1)
        self.assertEqual(self.cases(run), {"TC-01": "fail"})

    def test_a_plan_error_touches_nothing(self):
        path = os.path.join(self.home, "bad.plan")
        with open(path, "w") as f:
            f.write("app x.y\ncase T1\n  teleport\n")
        code, _, err = tnazar("run", path)
        self.assertEqual(code, 2)
        self.assertIn("line 3", err)
        self.assertIsNone(get_active())
        self.assertEqual(self.adb.calls, [])

    def test_a_dashboard_message_hands_the_run_back(self):
        live_post = live.post
        posted = []
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + '''
case TC-01 "Login"
  run login
  check "Pay now"
case TC-02 "Back"
  say "going back"
  key back
  check Login
''')
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main

        def main(argv):
            if argv[:2] == ["say", "going back"] and not posted:
                posted.append(1)
                live_post("stop and log out instead")
            return real_main(argv)
        with mock.patch("teesri_nazar.cli.main", main):
            code, out, _ = tnazar("run", path)
        self.assertEqual(code, live.EXIT_HELD)
        self.assertIn("stop and log out instead", out)
        self.assertIn("--from TC-02", out)
        run = get_active()[0]  # the run stays open so the agent can act, then continue
        self.assertEqual(self.cases(run), {"TC-01": "pass", "TC-02": "blocked"})
        self.assertEqual(self.adb.current, "home")  # `key back` was not run

    def test_continuing_with_from_adds_to_the_same_run(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + '''
case TC-01 "Login"
  run login
  check "Pay now"
case TC-02 "Pay"
  check "Pay now"
''')
        self.ok("start", "demo", "--package", PACKAGE)
        get_active()[0].update_meta(plan=os.path.realpath(path))
        code, out, _ = tnazar("run", path, "--from", "TC-01")
        self.assertEqual(code, 0, out)
        self.assertEqual(len([p for p in runs_dir().iterdir()]), 1)

    def test_pause_waits_for_resume_and_carries_on(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + '''
case TC-01 "Login"
  run login
  check "Pay now"
''')
        from fakedevice import FakeClock
        clock = FakeClock()
        resumes = []

        def sleep(secs):
            clock.now += secs
            if clock.now > 1100 and not resumes:  # resume after ~100 s of pause
                resumes.append(1)
                live.set_paused(False)
        clock.sleep = sleep
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main

        def main(argv):
            if argv[:1] == ["tap"] and not resumes:
                live.set_paused(True)
            return real_main(argv)
        with mock.patch.object(live, "time", clock), mock.patch("teesri_nazar.cli.main", main):
            code, out, _ = tnazar("run", path)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cases(Run.load("demo")), {"TC-01": "pass"})


class IfWaitTest(PlanRunBase):
    def test_if_can_wait_for_whichever_screen_comes_first(self):
        p = parse('app a.b\ncase T1\n  if "Pay now" --or Login --wait 10\n    say home\n  end\n  check X\n')
        block = p.cases[0].body[0]
        self.assertEqual((block.target, block.alts, block.wait), ("Pay now", ["Login"], 10.0))

    def test_if_stops_waiting_as_soon_as_an_alternative_shows(self):
        before = len(self.adb.calls)
        _, run = self.run_plan('''
case TC-01 "Login from wherever the app opened"
  if "Pay now" --or Login --wait 15
    say "already logged in"
  else
    run login
  end
  check "Pay now"
''')
        self.assertEqual(self.cases(run), {"TC-01": "pass"})
        branch = [e for e in run.events() if e["type"] == "if"][0]
        self.assertEqual((branch["found"], branch["reads"]), (False, 1))

    def test_a_bad_if_option_is_caught(self):
        with self.assertRaises(plan.PlanError) as ctx:
            parse('app a.b\ncase T1\n  if Login --wiat 5\n  end\n  check X\n')
        self.assertIn("line 3", str(ctx.exception))
