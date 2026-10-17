"""Plan parser and runner fixes from the review: each test names the bug it pins."""

import os
from pathlib import Path
from unittest import mock

from fakedevice import tnazar
from teesri_nazar import cli, commands, live, plan, runner
from teesri_nazar.session import get_active

from test_plan import LOGIN, PlanRunBase, parse

ROOT = Path(__file__).resolve().parent.parent


class ParseFixTest(PlanRunBase):
    def error(self, text: str) -> str:
        with self.assertRaises(plan.PlanError) as ctx:
            parse(text)
        return str(ctx.exception)

    def test_an_or_alternative_must_be_a_selector_too(self):
        for alt in ("1,2", '"#3"', '""'):
            msg = self.error(f'app a.b\ncase T1\n  if Login --or {alt}\n    key back\n  end\n  check X\n')
            self.assertIn("line 3", msg)

    def test_an_empty_if_target_names_its_line(self):
        self.assertIn("line 3", self.error('app a.b\ncase T1\n  if ""\n  end\n  check X\n'))

    def test_selector_targets_are_accepted(self):
        for t in ("Login", "~Log", "id=btn", "desc=Back"):
            parse(f'app a.b\ncase T1\n  if {t} --or id=other\n  end\n  check X\n')

    def test_a_second_else_is_an_error(self):
        msg = self.error('app a.b\ncase T1\n  if Login\n    key back\n  else\n    key back\n  else\n  end\n  check X\n')
        self.assertIn("line 7", msg)

    def test_a_non_ascii_retry_count_names_its_line(self):
        msg = self.error('app a.b\ncase T1\n  retry ²\n    key back\n  end\n  check X\n')
        self.assertIn("line 3", msg)

    def test_plan_steps_come_from_the_command_registry(self):
        self.assertEqual(set(plan.STEPS), set(commands.PLAN_STEPS))
        self.assertEqual(set(plan.NOT_IN_PLANS), set(commands.NOT_IN_PLANS))

    def test_the_docstring_explains_wait_or_and_exit_codes(self):
        doc = plan.__doc__
        self.assertIn("--or", doc)
        self.assertIn("exit 4", doc)
        self.assertIn("--wait", doc)
        for code in ("0", "1", "2", "3"):
            self.assertIn(code, doc)

    def test_the_example_retry_clears_the_field_before_typing_again(self):
        p = plan.load(ROOT / "examples" / "settings.plan")
        retry = next(i for i in p.cases[1].body if isinstance(i, plan.Retry))
        typed = [s.argv for s in retry.body if s.argv[0] == "type"]
        self.assertTrue(typed and all("--clear" in a for a in typed))


class RunnerFixTest(PlanRunBase):
    def test_a_one_word_title_starting_with_a_dash_works(self):
        _, run = self.run_plan('''
case TC-01 -smoke
  check Login
''')
        self.assertEqual(self.cases(run), {"TC-01": "pass"})
        start = [e for e in run.events() if e["type"] == "case_start"][0]
        self.assertEqual(start["title"], "-smoke")

    def test_a_user_error_mid_run_closes_the_case_and_the_run(self):
        with mock.patch.object(runner.Executor, "visible", side_effect=cli.UserError("bad target")):
            out, run = self.run_plan('''
case TC-01 "branch"
  if Login
    key back
  end
  check Login
''', expect=cli.EXIT_ERROR)
        self.assertIsNone(get_active())
        self.assertEqual(self.cases(run), {"TC-01": "blocked"})
        self.assertIn("bad target", out)
        self.assertTrue((run.path / "report.html").exists())

    def test_a_setup_failure_says_it_is_a_setup_error(self):
        self.adb.installed = []
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + 'case TC-01 "x"\n  check Login\n')
        code, out, err = tnazar("run", path)
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("setup error:", err)
        self.assertIn("not installed", err)

    def test_if_waits_while_the_dashboard_is_paused(self):
        reads = []

        def visible(_self, target, alts, wait):
            reads.append(live.paused())
            return True, 1
        resumed = []

        def wait_resume(limit=live.PAUSE_WAIT):
            resumed.append(1)
            live.set_paused(False)
            return True
        real_main = cli.main

        def main(argv):
            code = real_main(argv)
            if argv == ["say", "before"]:
                live.set_paused(True)
            return code
        with mock.patch.object(runner.Executor, "visible", visible), \
                mock.patch.object(live, "wait_resume", wait_resume), mock.patch("teesri_nazar.cli.main", main):
            _, run = self.run_plan('''
case TC-01 "branch"
  say "before"
  if Login
    say "found"
  end
  check Login
''')
        self.assertEqual(resumed, [1])
        self.assertEqual(reads, [False])  # the screen was read only after resume

    def test_if_hands_back_on_an_unread_message(self):
        visible = mock.Mock(return_value=(True, 1))
        real_main = cli.main

        def main(argv):
            code = real_main(argv)
            if argv[:1] == ["say"]:
                live.post("log out first")
            return code
        with mock.patch.object(runner.Executor, "visible", visible), mock.patch("teesri_nazar.cli.main", main):
            out, _ = self.run_plan('''
case TC-01 "branch"
  say "before"
  if Login
    key back
  end
  check Login
''', expect=live.EXIT_HELD)
        visible.assert_not_called()
        self.assertIn("log out first", out)
        run = get_active()[0]
        self.assertEqual(self.cases(run), {"TC-01": "blocked"})
        self.assertTrue(any(e["type"] == "user_msg" for e in run.events()))


class DeviceChoiceTest(PlanRunBase):
    def test_run_starts_on_the_device_named_with_device(self):
        # With a phone and an emulator both connected, `--device` must reach `start`.
        from fakedevice import SERIAL
        picked = []

        def pick(explicit=None):
            picked.append(explicit)
            return SERIAL
        with mock.patch.object(cli, "pick_serial", pick):
            self.run_plan('''
case TC-01 "on the chosen device"
  check Login
''', "--device", "phone-1")
        self.assertEqual(picked[0], "phone-1")

    def test_device_can_also_come_before_the_command(self):
        from fakedevice import SERIAL
        picked = []
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + "\ncase TC-01 \"x\"\n  check Login\n")
        with mock.patch.object(cli, "pick_serial", lambda explicit=None: picked.append(explicit) or SERIAL):
            code, out, err = tnazar("--device", "phone-2", "run", path)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(picked[0], "phone-2")
