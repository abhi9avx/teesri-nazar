"""Replay against a fake device: a faithful app passes, a changed app is caught, never a false pass."""

import copy
import os
import unittest
from unittest import mock

from fakedevice import PACKAGE, FakeAdb, fake_device, login_app, node, tnazar
from teesri_nazar import report as rpt
from teesri_nazar.session import Run, set_active

CRASH = f"FATAL EXCEPTION: main\nProcess: {PACKAGE}, PID: 4242\n"


class ReplayTestCase(unittest.TestCase):
    def setUp(self):
        self.adb = FakeAdb(login_app())
        ctx = fake_device(self.adb)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def ok(self, *argv):
        code, out, err = tnazar(*argv)
        self.assertEqual(code, 0, f"tnazar {' '.join(argv)} -> {code}\n{out}{err}")
        return out

    def record_login(self, *extra_case_steps):
        """Record `demo`: TC-01 logs in and checks home, TC-02 checks the balance."""
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "start", "TC-01", "Login")
        self.ok("type", "9876543210", "--into", "id=phone_input")
        self.ok("tap", "Login")
        self.ok("check", "Hi Asha")
        for step in extra_case_steps:
            self.ok(*step)
        self.ok("case", "end")
        self.ok("case", "start", "TC-02", "Balance")
        self.ok("check", "id=balance")
        self.ok("case", "end")
        self.ok("stop")

    def fresh_device(self, change=None):
        """Put the device back on the login screen, optionally with a changed app."""
        if change:
            app = copy.deepcopy(self.adb.app)
            change(app)
            self.adb.app = app
        self.adb.current = self.adb.app.start
        self.adb.typed.clear()
        self.adb.taps.clear()

    def replay(self, *extra):
        code, out, _ = tnazar("replay", "demo", *extra)
        runs = sorted(p.name for p in Run.load("demo").path.parent.iterdir() if "-replay-" in p.name)
        return code, out, Run.load(runs[-1])

    def statuses(self, run):
        return {c["id"]: c["status"] for c in rpt.summarize(run)["cases"]}


class FaithfulReplayTest(ReplayTestCase):
    def test_same_app_replays_green(self):
        self.record_login()
        self.fresh_device()
        code, out, run = self.replay()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.statuses(run), {"TC-01": "pass", "TC-02": "pass"})
        self.assertEqual(run.meta()["replay_of"], "demo")
        self.assertEqual(self.adb.typed, ["9876543210"])
        self.assertEqual(self.adb.current, "home")
        rechecks = [e for e in run.events() if e["type"] == "check"]
        self.assertTrue(all(e["recorded_ok"] and e["ok"] for e in rechecks))

    def test_restores_animations(self):
        self.record_login()
        self.fresh_device()
        self.replay()
        self.assertEqual(self.adb.settings["window_animation_scale"], "1.0")

    def test_fresh_clears_app_data(self):
        self.record_login()
        self.fresh_device()
        self.adb.calls.clear()
        self.replay("--fresh")
        self.assertIn(["shell", "pm", "clear", PACKAGE], self.adb.calls)


    def test_replayed_restart_starts_clean(self):
        self.adb.app.screens["search"] = [node(id="search_box", text="Search results", clickable=False)]
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "start", "TC-01", "Relaunch")
        self.ok("launch", "--restart")
        self.ok("check", "Welcome back")
        self.ok("case", "end")
        self.ok("stop")
        self.fresh_device()
        self.adb.foreign_top = "search"  # left behind by an earlier, failed replay
        code, out, run = self.replay()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.statuses(run), {"TC-01": "pass"})


class RegressionDetectionTest(ReplayTestCase):
    def test_changed_text_fails_the_check(self):
        self.record_login()

        def rename_greeting(app):
            app.screens["home"][0]["text"] = "Hello Asha"

        self.fresh_device(rename_greeting)
        code, _, run = self.replay()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(run), {"TC-01": "fail", "TC-02": "pass"})

    def test_missing_element_blocks_the_rest_of_the_case(self):
        self.record_login()

        def rename_button(app):
            app.screens["login"][3].update(id="btn_signin", text="Sign in")

        self.fresh_device(rename_button)
        code, out, run = self.replay()
        self.assertEqual(code, 1)
        statuses = self.statuses(run)
        self.assertEqual(statuses["TC-01"], "blocked")
        self.assertIn("step 2 failed", out)
        tc1 = rpt.case_events(run.events(), "TC-01")
        # The failed step is recorded as a failed check; the later "Hi Asha" check never ran.
        self.assertEqual([e["what"] for e in tc1 if e["type"] == "check"],
                         ['step 2: tap: could not find "Login" within 10s'])

    def test_state_change_is_caught(self):
        self.record_login(("check", "id=balance", "--enabled", "true"))

        def disable_balance(app):
            app.screens["home"][1]["enabled"] = False

        self.fresh_device(disable_balance)
        code, _, run = self.replay()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(run)["TC-01"], "fail")

    def test_crash_during_replay_fails_the_case(self):
        self.record_login()
        self.fresh_device()
        original = self.adb._shell

        def crash_on_login(a):
            out = original(a)
            if a[:2] == ["input", "tap"] and self.adb.current == "home":
                self.adb.crash_log = CRASH
            return out

        self.adb._shell = crash_on_login
        code, _, run = self.replay()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(run)["TC-01"], "fail")


class SecretsTest(ReplayTestCase):
    def record_with_secret(self):
        with mock.patch.dict(os.environ, {"APP_MPIN": "2468"}):
            self.ok("start", "demo", "--package", PACKAGE)
            self.ok("case", "start", "TC-01")
            self.ok("type", "--env", "APP_MPIN", "--into", "id=mpin_input")
            self.ok("tap", "Login")
            self.ok("check", "Hi Asha")
            self.ok("case", "end")
            self.ok("stop")

    def test_secret_comes_from_env_on_replay(self):
        self.record_with_secret()
        self.fresh_device()
        with mock.patch.dict(os.environ, {"APP_MPIN": "1357"}):
            code, _, run = self.replay()
        self.assertEqual(code, 0)
        self.assertEqual(self.adb.typed, ["1357"])

    def test_missing_secret_blocks_instead_of_passing(self):
        self.record_with_secret()
        self.fresh_device()
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("APP_MPIN", None)
            code, out, run = self.replay()
        self.assertEqual(code, 1)
        self.assertIn("set $APP_MPIN", out)
        self.assertEqual(self.statuses(run)["TC-01"], "blocked")
        self.assertEqual(self.adb.typed, [])


class NonUiChecksTest(ReplayTestCase):
    def test_db_only_case_is_blocked_on_replay_not_passed(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "start", "TC-01")
        self.ok("check", "--result", "pass", "--kind", "db", "--what", "txn status SUCCESS")
        self.ok("case", "end")
        self.ok("stop")
        self.fresh_device()
        code, _, run = self.replay()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(run)["TC-01"], "blocked")
        notes = [e["text"] for e in run.events() if e["type"] == "note"]
        self.assertEqual(notes, ["[db] not re-verified on replay: txn status SUCCESS"])


class ReplayGuardsTest(ReplayTestCase):
    def test_refuses_while_a_run_is_active(self):
        self.record_login()
        set_active(Run.load("demo"), "fake-1")
        code, out, _ = tnazar("replay", "demo")
        self.assertEqual(code, 2)
        self.assertIn("a run is active", out)

    def test_unknown_run(self):
        self.assertEqual(tnazar("replay", "nope")[0], 2)

    def test_coordinate_taps_scale_to_the_new_screen(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("tap", "540,860")
        self.ok("stop")
        self.fresh_device()
        self.adb.size = (1440, 3200)
        self.replay()
        self.assertEqual(self.adb.taps, [(720, 1147)])


if __name__ == "__main__":
    unittest.main()


class KeyboardBackTest(ReplayTestCase):
    """`back` closes the keyboard when it is up and navigates when it is not; replay must not care which."""

    def setUp(self):
        super().setUp()
        self.adb.app.screens["launcher"] = [node(id="workspace", text="Launcher home", clickable=False)]
        self.adb.app.on_key[("login", 4)] = "launcher"

    def record(self, expect):
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "start", "TC-01", "Type then back")
        self.ok("type", "98765", "--into", "id=phone_input")
        self.ok("key", "back")
        self.ok("check", expect)
        self.ok("case", "end")
        self.ok("stop")

    def test_back_that_only_closed_the_keyboard_is_skipped_when_it_is_already_hidden(self):
        self.record("Welcome back")
        self.fresh_device()
        self.adb.keyboard_on_focus = False
        code, out, run = self.replay()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.adb.current, "login")
        self.assertEqual(self.statuses(run), {"TC-01": "pass"})
        self.assertIn("back skipped", out)

    def test_navigating_back_first_closes_a_keyboard_that_is_up_on_replay(self):
        self.adb.keyboard_on_focus = False
        self.record("Launcher home")
        self.fresh_device()
        self.adb.keyboard_on_focus = True
        code, out, run = self.replay()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.adb.current, "launcher")
        self.assertEqual(self.statuses(run), {"TC-01": "pass"})
        self.assertIn("extra back", out)
