"""The CLI end to end against a fake device: run lifecycle, actions, checks, case results."""

import os
import unittest
from unittest import mock

from fakedevice import PACKAGE, FakeAdb, FakeApp, fake_device, login_app, node, tnazar
from teesri_nazar import report as rpt
from teesri_nazar.session import Run, get_active

CRASH = f"--------- beginning of crash\nFATAL EXCEPTION: main\nProcess: {PACKAGE}, PID: 4242\n"


class CliTestCase(unittest.TestCase):
    app = staticmethod(login_app)

    def setUp(self):
        self.adb = FakeAdb(self.app())
        ctx = fake_device(self.adb)
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def ok(self, *argv):
        code, out, err = tnazar(*argv)
        self.assertEqual(code, 0, f"tnazar {' '.join(argv)} -> {code}\n{out}{err}")
        return out

    def start(self, *extra):
        self.ok("start", "demo", "--package", PACKAGE, *extra)
        return get_active()[0]

    def case_end_event(self, run, case):
        return [e for e in run.events() if e["type"] == "case_end" and e["case"] == case][-1]


class LifecycleTest(CliTestCase):
    def test_start_records_device_and_turns_animations_off(self):
        run = self.start()
        meta = run.meta()
        self.assertEqual((meta["device"], meta["model"], meta["android"]), ("fake-1", "FakePhone", "14"))
        self.assertEqual(meta["app_version"], "1.0 (1)")
        self.assertEqual(meta["animations_before"]["window_animation_scale"], "1.0")
        self.assertEqual(self.adb.settings["window_animation_scale"], "0")

    def test_stop_restores_animations_writes_report_and_clears_active(self):
        run = self.start()
        out = self.ok("stop")
        self.assertIsNone(get_active())
        self.assertEqual(self.adb.settings["window_animation_scale"], "1.0")
        self.assertIn("finished_at", run.meta())
        self.assertTrue((run.path / "report.md").exists())
        self.assertTrue((run.path / "report.html").exists())
        self.assertIn("run finished: demo", out)

    def test_keep_animations(self):
        run = self.start("--keep-animations")
        self.assertNotIn("animations_before", run.meta())
        self.assertEqual(self.adb.settings["window_animation_scale"], "1.0")

    def test_package_not_installed(self):
        code, _, err = tnazar("start", "demo", "--package", "com.missing")
        self.assertEqual(code, 2)
        self.assertIn("not installed", err)
        self.assertIsNone(get_active())

    def test_invalid_run_name_leaves_device_animations_untouched(self):
        code, _, err = tnazar("start", "my test", "--package", PACKAGE)
        self.assertEqual(code, 2)
        self.assertIn("run name", err)
        self.assertIsNone(get_active())
        self.assertEqual(self.adb.settings["window_animation_scale"], "1.0")

    def test_fresh_clears_app_data(self):
        self.adb.current = "home"
        self.start("--fresh")
        self.assertIn(["shell", "pm", "clear", PACKAGE], self.adb.calls)
        self.assertEqual(self.adb.current, "login")

    def test_stop_without_run(self):
        self.assertEqual(tnazar("stop")[0], 2)
        self.assertEqual(tnazar("stop", "--if-active")[0], 0)

    def test_starting_a_new_run_finishes_the_previous_one(self):
        first = self.start()
        self.ok("start", "second")
        self.assertIn("finished_at", first.meta())
        self.assertEqual(get_active()[0].name, "second")

    def test_stop_blocks_an_unfinished_case(self):
        run = self.start()
        self.ok("case", "start", "TC-01", "never ended")
        self.ok("check", "Welcome back")
        self.ok("stop")
        end = self.case_end_event(run, "TC-01")
        self.assertEqual(end["status"], "blocked")
        self.assertIn("stopped before the case finished", end["reason"])


class CaseResultTest(CliTestCase):
    """How a case's result is derived: the core of pass/fail correctness."""

    def setUp(self):
        super().setUp()
        self.run = self.start()
        self.ok("case", "start", "TC-01", "Login")

    def end(self, *argv):
        code, out, _ = tnazar("case", "end", *argv)
        self.assertEqual(code, 0, out)
        return self.case_end_event(self.run, "TC-01")

    def test_no_checks_is_blocked_not_pass(self):
        e = self.end()
        self.assertEqual(e["status"], "blocked")
        self.assertEqual(e["reason"], "no checks were recorded")

    def test_all_checks_pass(self):
        self.ok("check", "Welcome back")
        self.ok("check", "id=btn_login")
        self.assertEqual(self.end()["status"], "pass")

    def test_one_failed_check_fails_the_case(self):
        self.ok("check", "Welcome back")
        tnazar("check", "Dashboard", "--timeout", "1")
        e = self.end()
        self.assertEqual(e["status"], "fail")
        self.assertIn('"Dashboard" is visible', e["reason"])
        self.assertEqual(len(e["evidence"]), 2)

    def test_tester_cannot_mark_pass_over_a_failed_check(self):
        tnazar("check", "Dashboard", "--timeout", "1")
        e = self.end("pass", "--reason", "looked fine")
        self.assertEqual(e["status"], "fail")
        self.assertIn("1 failed check(s); marked pass by the tester: looked fine", e["reason"])

    def test_crash_fails_the_case_even_with_passing_checks(self):
        self.ok("check", "Welcome back")
        self.adb.crash_log = CRASH
        e = self.end()
        self.assertEqual(e["status"], "fail")
        self.assertEqual(e["reason"], "app crashed or ANR")
        self.assertIn("FATAL EXCEPTION", (self.run.path / e["crash"]).read_text())

    def test_tester_cannot_mark_pass_over_a_crash(self):
        self.ok("check", "Welcome back")
        self.adb.crash_log = CRASH
        self.assertEqual(self.end("pass")["status"], "fail")

    def test_crash_of_another_app_is_ignored(self):
        self.ok("check", "Welcome back")
        self.adb.crash_log = "FATAL EXCEPTION: main\nProcess: com.other.app, PID: 1\n"
        self.assertEqual(self.end()["status"], "pass")

    def test_crash_before_case_start_is_not_counted(self):
        self.adb.crash_log = CRASH
        self.ok("case", "start", "TC-02")  # clears logcat
        self.ok("check", "Welcome back")
        tnazar("case", "end")
        self.assertEqual(self.case_end_event(self.run, "TC-02")["status"], "pass")

    def test_explicit_blocked_and_skipped(self):
        e = self.end("blocked", "--reason", "backend down")
        self.assertEqual((e["status"], e["reason"]), ("blocked", "backend down"))
        self.ok("case", "start", "TC-02")
        self.ok("case", "end", "skipped", "--reason", "out of scope")
        self.assertEqual(self.case_end_event(self.run, "TC-02")["status"], "skipped")

    def test_starting_next_case_closes_the_open_one(self):
        self.ok("check", "Welcome back")
        self.ok("case", "start", "TC-02")
        e = self.case_end_event(self.run, "TC-01")
        self.assertEqual(e["status"], "pass")
        self.assertIn("next case started", e["reason"])
        self.assertEqual(self.run.current_case(), "TC-02")

    def test_rerun_of_a_case_only_counts_its_latest_checks(self):
        tnazar("check", "Dashboard", "--timeout", "1")
        self.end()
        self.ok("case", "start", "TC-01", "Login again")
        self.ok("check", "Welcome back")
        self.assertEqual(self.end()["status"], "pass")

    def test_case_end_without_open_case(self):
        self.end()
        self.assertEqual(tnazar("case", "end")[0], 2)

    def test_summary_counts(self):
        self.ok("check", "Welcome back")
        self.end()
        self.ok("case", "start", "TC-02")
        tnazar("check", "Nope", "--timeout", "1")
        tnazar("case", "end")
        self.ok("case", "start", "TC-03")
        tnazar("case", "end")
        s = rpt.summarize(self.run)
        self.assertEqual((s["counts"]["pass"], s["counts"]["fail"], s["counts"]["blocked"]), (1, 1, 1))
        self.assertEqual((s["checks_total"], s["checks_failed"]), (2, 1))


class NoRunTest(CliTestCase):
    def test_case_needs_a_run(self):
        code, _, err = tnazar("case", "start", "TC-01")
        self.assertEqual(code, 2)
        self.assertIn("active run", err)

    def test_actions_work_without_a_run(self):
        self.ok("tap", "Login")
        self.assertEqual(self.adb.current, "home")
        self.ok("check", "Hi Asha")


class UiCheckTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.start()
        self.ok("case", "start", "TC-01")

    def last_check(self):
        return [e for e in self.run.events() if e["type"] == "check"][-1]

    def test_pass_records_selector_case_and_screenshot(self):
        out = self.ok("check", "id=btn_login")
        self.assertIn("PASS [ui]", out)
        e = self.last_check()
        self.assertEqual((e["ok"], e["kind"], e["case"], e["selector"]), (True, "ui", "TC-01", {"id": "btn_login"}))
        self.assertTrue((self.run.path / e["shot"]).read_bytes().startswith(b"\x89PNG"))

    def test_fail_exits_1_saves_dump_and_lists_visible_labels(self):
        code, out, _ = tnazar("check", "Dashboard", "--timeout", "2")
        self.assertEqual(code, 1)
        self.assertIn("FAIL [ui]", out)
        self.assertIn('"Welcome back"', out)
        e = self.last_check()
        self.assertFalse(e["ok"])
        self.assertIn("btn_login", (self.run.path / e["dump"]).read_text())

    def test_waits_for_element_to_appear(self):
        # Element appears only after the device moves to home; the check polls until then.
        original = self.adb.exec_out
        calls = {"n": 0}

        def later(cmd, timeout=60):
            calls["n"] += 1
            if calls["n"] == 3:
                self.adb.current = "home"
            return original(cmd, timeout)

        self.adb.exec_out = later
        self.ok("check", "Hi Asha", "--timeout", "5")

    def test_gone(self):
        self.ok("check", "Dashboard", "--gone")
        self.assertEqual(tnazar("check", "Welcome back", "--gone", "--timeout", "1")[0], 1)

    def test_enabled_and_checked_state(self):
        self.ok("check", "id=btn_login", "--enabled", "true")
        self.ok("check", "id=btn_forgot", "--enabled", "false")
        self.assertEqual(tnazar("check", "id=btn_forgot", "--enabled", "true", "--timeout", "1")[0], 1)
        self.ok("check", "Remember me", "--checked", "true")
        self.assertEqual(tnazar("check", "Remember me", "--checked", "false", "--timeout", "1")[0], 1)

    def test_state_check_on_missing_element_fails(self):
        self.assertEqual(tnazar("check", "id=nope", "--enabled", "true", "--timeout", "1")[0], 1)

    def test_description_says_what_was_checked(self):
        self.ok("check", "id=btn_forgot", "--enabled", "false")
        self.assertEqual(self.last_check()["what"], "id=btn_forgot is visible, disabled")
        self.ok("check", "id=btn_login", "--what", "Login button shown")
        self.assertEqual(self.last_check()["what"], "Login button shown")


class ManualCheckTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.start()
        self.ok("case", "start", "TC-01")

    def test_requires_what(self):
        code, _, err = tnazar("check", "--result", "pass", "--kind", "db")
        self.assertEqual(code, 2)
        self.assertIn("--what is required", err)

    def test_pass_and_fail_are_recorded_with_kind_and_detail(self):
        self.ok("check", "--result", "pass", "--kind", "db", "--what", "txn row SUCCESS", "--detail", "status=SUCCESS")
        code, _, _ = tnazar("check", "--result", "fail", "--kind", "figma", "--what", "CTA copy", "--shot")
        self.assertEqual(code, 1)
        checks = [e for e in self.run.events() if e["type"] == "check"]
        self.assertEqual([(c["kind"], c["ok"]) for c in checks], [("db", True), ("figma", False)])
        self.assertEqual(checks[0]["detail"], "status=SUCCESS")
        self.assertIn("shot", checks[1])
        self.assertNotIn("shot", checks[0])


class TapTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.start()

    def last(self, kind):
        return [e for e in self.run.events() if e["type"] == kind][-1]

    def test_by_text_taps_center_and_records_selector(self):
        out = self.ok("tap", "Login")
        self.assertEqual(self.adb.taps[-1], (540, 860))
        self.assertEqual(self.adb.current, "home")
        e = self.last("tap")
        self.assertEqual((e["selector"], e["xy"], e["label"], e["screen"]), ({"text": "Login"}, [540, 860], "Login", [1080, 2400]))
        self.assertIn("tapped 'Login' at 540,860", out)

    def test_by_id_desc_and_partial(self):
        self.ok("tap", "id=phone_input")
        self.ok("tap", "desc=Phone number")
        self.ok("tap", "~logi")
        self.assertEqual(self.adb.current, "home")

    def test_not_found_exits_1_without_tapping(self):
        code, out, _ = tnazar("tap", "Pay now")
        self.assertEqual(code, 1)
        self.assertIn("not found", out)
        self.assertEqual(self.adb.taps, [])

    def test_wait_for_element(self):
        self.ok("tap", "Login")
        self.ok("tap", "Pay now", "--wait", "5")

    def test_index_needs_a_previous_observe(self):
        self.assertEqual(tnazar("tap", "#3")[0], 2)

    def test_index_from_observe_is_recorded_as_a_stable_selector(self):
        out = self.ok("observe")
        line = next(ln for ln in out.splitlines() if "id=btn_login" in ln)
        index = line.split("]")[0].lstrip("[")
        self.ok("tap", f"#{index}")
        self.assertEqual(self.last("tap")["selector"], {"id": "btn_login"})
        self.assertEqual(self.adb.current, "home")
        self.assertEqual(tnazar("tap", "#99")[0], 1)

    def test_coordinates(self):
        self.ok("tap", "540,860")
        self.assertEqual(self.last("tap")["selector"], None)
        self.assertEqual(self.adb.current, "home")

    def test_long_press(self):
        self.ok("tap", "Login", "--long")
        self.assertIn(["shell", "input", "swipe", "540", "860", "540", "860", "800"], self.adb.calls)
        self.assertTrue(self.last("tap")["long"])


class TypeTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.start()

    def last_type(self):
        return [e for e in self.run.events() if e["type"] == "type"][-1]

    def test_plain_text_is_recorded(self):
        self.ok("type", "9876543210", "--into", "id=phone_input")
        e = self.last_type()
        self.assertEqual((e["text"], e["secret"], e["selector"]), ("9876543210", False, {"id": "phone_input"}))
        self.assertEqual(self.adb.typed, ["9876543210"])

    def test_env_value_is_typed_but_never_recorded_or_printed(self):
        with mock.patch.dict(os.environ, {"APP_MPIN": "2468"}):
            out = self.ok("type", "--env", "APP_MPIN", "--into", "id=mpin_input")
        e = self.last_type()
        self.assertEqual((e["text"], e["secret"], e["env"]), (None, True, "APP_MPIN"))
        self.assertEqual(self.adb.typed, ["2468"])
        self.assertNotIn("2468", out)
        self.assertNotIn("2468", self.run.events_path.read_text())

    def test_password_field_is_secret_automatically(self):
        out = self.ok("type", "1357", "--into", "id=mpin_input")
        self.assertEqual((self.last_type()["text"], self.last_type()["secret"]), (None, True))
        self.assertNotIn("1357", out)

    def test_missing_env_var(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APP_MISSING", None)
            code, _, err = tnazar("type", "--env", "APP_MISSING")
        self.assertEqual(code, 2)
        self.assertIn("APP_MISSING is not set", err)
        self.assertEqual(self.adb.typed, [])

    def test_dotenv_is_loaded_from_home(self):
        with open(os.path.join(self.home, ".env"), "w") as f:
            f.write("# comment\nAPP_OTP='123456'\n")
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("APP_OTP", None)
            self.ok("type", "--env", "APP_OTP")
        self.assertEqual(self.adb.typed, ["123456"])

    def test_clear_and_enter(self):
        self.ok("type", "hi there", "--into", "id=phone_input", "--clear", "--enter")
        self.assertTrue(any(c[:3] == ["shell", "input", "keyevent"] and "123" in c for c in self.adb.calls))
        self.assertIn(["shell", "input", "keyevent", "66"], self.adb.calls)
        self.assertEqual(self.adb.typed, ["hi%sthere"])

    def test_nothing_to_type(self):
        self.assertEqual(tnazar("type")[0], 2)


class NavigationTest(CliTestCase):
    @staticmethod
    def app():
        return FakeApp(
            screens={
                "top": [node(id="list", text="", clickable=False, bounds=(0, 300, 1080, 2000)),
                        node(text="Item 1", bounds=(0, 300, 1080, 400))],
                "bottom": [node(id="list", text="", clickable=False, bounds=(0, 300, 1080, 2000)),
                           node(text="Transaction history", bounds=(0, 1800, 1080, 1900))],
            },
            start="top",
            on_swipe={"top": "bottom"},
        )

    def test_scroll_to_finds_target(self):
        out = self.ok("scroll", "down", "--to", "Transaction history")
        self.assertIn("found", out)
        self.assertEqual(self.adb.current, "bottom")

    def test_scroll_to_stops_at_end_and_fails(self):
        code, out, _ = tnazar("scroll", "down", "--to", "Settings")
        self.assertEqual(code, 1)
        self.assertIn("not found", out)
        swipes = [c for c in self.adb.calls if c[:3] == ["shell", "input", "swipe"]]
        self.assertEqual(len(swipes), 2)  # stops once the screen no longer changes

    def test_wait_timeout(self):
        self.assertEqual(tnazar("wait", "Transaction history", "--timeout", "2")[0], 1)
        self.ok("wait", "Transaction history", "--gone", "--timeout", "1")

    def test_unknown_key(self):
        self.assertEqual(tnazar("key", "teleport")[0], 2)


class ReportTest(CliTestCase):
    def test_full_flow_report(self):
        run = self.start()
        self.ok("case", "start", "TC-01", "Login lands on home")
        self.ok("type", "9876543210", "--into", "id=phone_input")
        self.ok("tap", "Login")
        self.ok("check", "Hi Asha")
        self.ok("case", "end")
        self.ok("case", "start", "TC-02", "Pay is offered")
        tnazar("check", "Pay later", "--timeout", "1")
        self.ok("note", "Pay later CTA missing", "--kind", "bug")
        self.ok("case", "end")
        out = self.ok("stop")
        self.assertIn("2 case(s): ✅ 1 pass  ❌ 1 fail  | checks 1/2 passed", out)
        md = (run.path / "report.md").read_text()
        self.assertIn("| TC-01 | Login lands on home | ✅ pass |", md)
        self.assertIn("| TC-02 | Pay is offered | ❌ fail |", md)
        self.assertIn("Pay later CTA missing", md)
        runs = self.ok("runs")
        self.assertIn("demo", runs)
        self.assertEqual(Run.load("demo").meta()["name"], "demo")


if __name__ == "__main__":
    unittest.main()
