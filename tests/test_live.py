"""The live dashboard: timings, narration, the user's inbox and pause switch, runtime secrets,
redaction, and the localhost server the browser talks to."""

import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from fakedevice import FakeClock, tnazar
from teesri_nazar import cli, live, ui
from teesri_nazar.session import Run, set_active

from test_cli import CliTestCase


def serve_dashboard(test: unittest.TestCase) -> ui.Dashboard:
    httpd, dash = ui.start(0)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def stop():
        httpd.shutdown()
        httpd.server_close()
    test.addCleanup(stop)
    return dash


def events(run, type_):
    return [e for e in run.events() if e["type"] == type_]


class TimingTest(CliTestCase):
    def test_actions_record_how_long_they_took(self):
        run = self.start()
        self.ok("tap", "Login")
        tap = events(run, "tap")[0]
        self.assertIsInstance(tap["dur_ms"], int)
        self.assertGreaterEqual(tap["dur_ms"], 0)

    def test_wait_records_screen_polls_until_it_gives_up(self):
        run = self.start()
        code, _, _ = tnazar("wait", "Nowhere", "--timeout", "1")
        self.assertEqual(code, 1)
        wait = events(run, "wait")[0]
        # polls at +0, +0.4, +0.8, +1.2 s on the fake clock
        self.assertEqual((wait["ok"], wait["polls"]), (False, 4))

    def test_check_records_polls(self):
        run = self.start()
        self.ok("check", "Login")
        self.assertEqual(events(run, "check")[0]["polls"], 1)

    def test_narration_is_logged_but_is_not_a_step(self):
        from teesri_nazar import report as rpt
        run = self.start()
        out = self.ok("say", "Logging in with the test account")
        self.assertIn("» Logging in with the test account", out)
        self.assertEqual(events(run, "say")[0]["text"], "Logging in with the test account")
        self.assertNotIn("dur_ms", events(run, "say")[0])
        self.assertEqual(rpt.build_cases(run.events()), [])


class InboxTest(CliTestCase):
    def test_unread_message_holds_the_next_device_command(self):
        run = self.start()
        live.post("log out instead")
        code, out, _ = tnazar("tap", "Login")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertIn(">> USER (dashboard): log out instead", out)
        self.assertIn("Command NOT run", out)
        self.assertEqual(self.adb.taps, [])
        self.assertEqual(events(run, "user_msg")[0]["text"], "log out instead")
        self.ok("tap", "Login")  # delivered once; the retry goes through
        self.assertEqual(len(self.adb.taps), 1)

    def test_non_device_command_shows_the_message_and_the_next_device_command_is_still_held(self):
        self.start()
        live.post("use the second account")
        out = self.ok("say", "Next step")
        self.assertLess(out.index("» Next step"), out.index("use the second account"))
        code, out, _ = tnazar("scroll", "down")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertIn(">> USER (dashboard): use the second account", out)

    def test_messages_sent_before_the_run_started_are_not_delivered(self):
        live.post("stale instruction")
        out = self.ok("start", "demo", "--package", "com.example.app")
        out += self.ok("tap", "Login")
        self.assertNotIn("stale instruction", out)

    def test_a_message_still_being_written_waits_for_the_next_step(self):
        self.start()
        with live.inbox_file().open("a") as f:
            f.write('{"id": "x", "t": "2026-01-01T00:00:00Z", "te')
        self.ok("tap", "Login")
        self.assertEqual(len(self.adb.taps), 1)


class SteeringUnderLoadTest(CliTestCase):
    def test_a_burst_of_messages_is_delivered_once_each_in_order(self):
        run = self.start()
        for i in range(5):
            live.post(f"instruction {i}")
        code, out, _ = tnazar("tap", "Login")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertEqual([ln for ln in out.splitlines() if ln.startswith(">> USER")],
                         [f">> USER (dashboard): instruction {i}" for i in range(5)])
        self.assertEqual([e["text"] for e in events(run, "user_msg")], [f"instruction {i}" for i in range(5)])
        self.ok("tap", "Login")  # nothing left to deliver

    def test_a_message_arriving_while_others_are_delivered_is_not_lost(self):
        self.start()
        live.post("first")
        real = live.messages
        calls = []

        def messages():
            calls.append(1)
            snapshot = real()
            if len(calls) == 1:  # lands right after the inbox was read, before it is marked read
                live.post("arrived mid-delivery")
            return snapshot
        with mock.patch.object(live, "messages", messages):
            code, out, _ = tnazar("tap", "Login")
        self.assertIn("first", out)
        code, out, _ = tnazar("tap", "Login")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertIn(">> USER (dashboard): arrived mid-delivery", out)

    def test_messages_posted_at_the_same_moment_never_interleave(self):
        self.start()
        dash = serve_dashboard(self)
        threads = [threading.Thread(target=dash.message, args=({"text": f"msg {i} " + "x" * 1900},))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        texts = sorted(m["text"].split()[1] for m in live.messages())
        self.assertEqual(texts, sorted(str(i) for i in range(10)))


class PauseTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        patcher = mock.patch.object(live, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_paused_run_holds_the_command_and_tells_the_agent_to_wait(self):
        self.start()
        live.set_paused(True)
        code, out, _ = tnazar("tap", "Login")
        self.assertEqual(code, live.EXIT_PAUSED)  # still paused: not the same as a message (EXIT_HELD)
        self.assertIn("PAUSED", out)
        self.assertIn("wait-resume", out)
        self.assertEqual(self.adb.taps, [])
        self.assertEqual(tnazar("wait-resume")[0], live.EXIT_PAUSED)
        live.set_paused(False)
        self.assertIn("resumed", self.ok("wait-resume"))
        self.ok("tap", "Login")

    def test_resume_within_the_wait_runs_the_command(self):
        self.start()
        live.set_paused(True)

        def sleep(secs):
            self.clock.now += secs
            live.set_paused(False)
        self.clock.sleep = sleep
        run = cli.get_active()[0]
        out = self.ok("tap", "Login")
        self.assertIn("resumed", out)
        self.assertEqual(len(self.adb.taps), 1)
        self.assertEqual([(e["reason"], e["resumed"]) for e in events(run, "hold")], [("paused", True)])

    def test_stop_and_start_clear_a_leftover_pause(self):
        self.start()
        live.set_paused(True)
        self.ok("stop")
        self.assertFalse(live.paused())
        live.set_paused(True)
        self.start()
        self.assertFalse(live.paused())


class SecretTest(CliTestCase):
    def test_type_env_uses_a_secret_from_the_dashboard_without_recording_it(self):
        run = self.start()
        dash = serve_dashboard(self)
        self.assertEqual(dash.secret({"name": "otp", "value": "482913"})["name"], "UI_OTP")
        code, out, _ = tnazar("type", "--env", "UI_OTP", "--into", "id=phone_input")
        self.assertEqual(code, live.EXIT_HELD)  # the agent first reads how to use it
        self.assertIn("--env UI_OTP", out)
        self.assertNotIn("482913", out)
        self.assertEqual(events(run, "user_msg")[0]["secret"], "UI_OTP")
        out = self.ok("type", "--env", "UI_OTP", "--into", "id=phone_input")
        self.assertEqual(self.adb.typed, list("482913"))
        self.assertNotIn("482913", out)
        typed = events(run, "type")[0]
        self.assertEqual((typed["text"], typed["secret"], typed["env"]), (None, True, "UI_OTP"))
        on_disk = "".join(p.read_text(errors="ignore") for p in Path(self.home).rglob("*") if p.is_file())
        self.assertNotIn("482913", on_disk)

    def test_secret_with_spaces_and_symbols_is_typed_exactly(self):
        self.start()
        dash = serve_dashboard(self)
        dash.secrets["UI_PASS"] = "a b&c'd"
        live.mark_all_read()
        self.ok("type", "--env", "UI_PASS", "--into", "id=phone_input")
        self.assertEqual(self.adb.typed, ["a%sb\\&c\\'d"])  # adb input text escaping

    def test_type_env_that_is_set_nowhere_is_an_error(self):
        self.start()
        code, _, err = tnazar("type", "--env", "UI_NOPE", "--into", "id=phone_input")
        self.assertEqual(code, 2)
        self.assertIn("UI_NOPE is not set", err)

    def test_adb_command_line_hides_env_file_values(self):
        Path(self.home, ".env").write_text("APP_MPIN=4321\n")
        run = self.start()
        done = subprocess.CompletedProcess([], 0)
        with mock.patch.object(cli.subprocess, "run", return_value=done):
            self.ok("adb", "shell", "input", "text", "4321")
        self.assertEqual(events(run, "adb")[0]["args"], ["shell", "input", "text", "••••"])

    def test_a_very_short_secret_is_hidden_when_it_is_a_whole_argument(self):
        Path(self.home, ".env").write_text("APP_PIN=42\n")
        run = self.start()
        with mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.ok("adb", "shell", "input", "text", "42")
            self.ok("adb", "forward", "tcp:8420", "tcp:6790")  # "42" inside other words is not touched
        args = [e["args"] for e in events(run, "adb")]
        self.assertEqual(args, [["shell", "input", "text", "••••"], ["forward", "tcp:8420", "tcp:6790"]])

    def test_adb_command_line_hides_dashboard_secrets(self):
        run = self.start()
        dash = serve_dashboard(self)
        dash.secrets["UI_PIN"] = "9090"
        with mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.ok("adb", "shell", "input", "text", "pin9090")
        self.assertEqual(events(run, "adb")[0]["args"][-1], "pin••••")


class ServerTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        env = mock.patch.dict(os.environ, {"TNAZAR_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.dash = serve_dashboard(self)
        self.base = f"http://127.0.0.1:{self.dash.port}"

    def call(self, method, path, body=None, token=True, host=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Tnazar-Token"] = self.dash.token
        if host:
            headers["Host"] = host
        data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def active_run(self) -> Run:
        run = Run.create("demo", {"package": "com.example.app"})
        set_active(run, "fake-1")
        return run

    def test_page_carries_the_session_token(self):
        status, body = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.dash.token, body.decode())
        self.assertNotIn("__TNAZAR_TOKEN__", body.decode())

    def test_other_hosts_are_refused(self):
        status, _ = self.call("GET", "/api/state", host=f"evil.example:{self.dash.port}")
        self.assertEqual(status, 403)

    def test_changes_need_the_token(self):
        status, _ = self.call("POST", "/api/pause", token=False)
        self.assertEqual(status, 403)
        self.assertFalse(live.paused())
        self.assertEqual(self.call("POST", "/api/pause")[0], 200)
        self.assertTrue(live.paused())
        self.assertEqual(self.call("POST", "/api/resume")[0], 200)
        self.assertFalse(live.paused())

    def test_messages_need_an_active_run(self):
        status, body = self.call("POST", "/api/message", {"text": "hello"})
        self.assertEqual(status, 400)
        self.assertIn("no active run", json.loads(body)["error"])
        self.active_run()
        self.assertEqual(self.call("POST", "/api/message", {"text": "hello"})[0], 200)
        self.assertEqual([m["text"] for m in live.unread()], ["hello"])
        state = json.loads(self.call("GET", "/api/state")[1])
        self.assertEqual([(m["text"], m["delivered"]) for m in state["inbox"]], [("hello", False)])

    def test_secret_is_served_only_with_the_token_and_never_written(self):
        self.active_run()
        status, body = self.call("POST", "/api/secret", {"name": "mpin", "value": "7531"})
        self.assertEqual((status, json.loads(body)["name"]), (200, "UI_MPIN"))
        self.assertEqual(self.call("GET", "/api/secret/UI_MPIN", token=False)[0], 403)
        self.assertEqual(json.loads(self.call("GET", "/api/secret/UI_MPIN")[1]), {"value": "7531"})
        on_disk = "".join(p.read_text(errors="ignore") for p in Path(self.home).rglob("*") if p.is_file())
        self.assertNotIn("7531", on_disk)
        self.assertIn("UI_MPIN", on_disk)  # the agent is told the name

    def test_sending_the_same_name_again_replaces_the_value(self):
        self.active_run()
        self.call("POST", "/api/secret", {"name": "otp", "value": "1111"})
        self.call("POST", "/api/secret", {"name": "otp", "value": "2222"})
        self.assertEqual(json.loads(self.call("GET", "/api/secret/UI_OTP")[1]), {"value": "2222"})

    def test_a_messy_name_becomes_a_safe_variable_name(self):
        self.active_run()
        status, body = self.call("POST", "/api/secret", {"name": "my otp!", "value": "5"})
        self.assertEqual((status, json.loads(body)["name"]), (200, "UI_MY_OTP_"))

    def test_empty_or_too_long_values_are_refused(self):
        self.active_run()
        for value in ("", "9" * 257):
            self.assertEqual(self.call("POST", "/api/secret", {"name": "otp", "value": value})[0], 400)
        self.assertEqual(self.call("GET", "/api/secret/UI_OTP")[0], 404)

    def test_secrets_do_not_carry_over_into_the_next_run(self):
        self.active_run()
        self.call("POST", "/api/secret", {"name": "mpin", "value": "7531"})
        next_run = Run.create("next", {"package": "com.example.app"})
        set_active(next_run, "fake-1")
        self.assertEqual(self.call("GET", "/api/secret/UI_MPIN")[0], 404)
        self.assertEqual(json.loads(self.call("GET", "/api/state")[1])["secrets"], [])

    def test_secret_shows_in_the_inbox_by_name_and_note_only(self):
        self.active_run()
        self.call("POST", "/api/secret", {"name": "otp", "value": "4455", "note": "use it on the OTP screen"})
        [msg] = json.loads(self.call("GET", "/api/state")[1])["inbox"]
        self.assertEqual((msg["secret"], msg["note"]), ("UI_OTP", "use it on the OTP screen"))
        self.assertNotIn("4455", json.dumps(msg))

    def test_events_resume_from_an_offset_and_skip_a_half_written_line(self):
        run = self.active_run()
        run.log({"type": "say", "text": "one"})
        run.log({"type": "say", "text": "two"})
        with run.events_path.open("a") as f:
            f.write('{"t": "x", "type": "say", "te')
        j = json.loads(self.call("GET", "/api/events?run=demo&from=0")[1])
        self.assertEqual([e["text"] for e in j["events"]], ["one", "two"])
        with run.events_path.open("a") as f:
            f.write('xt": "three"}\n')
        j = json.loads(self.call("GET", f"/api/events?run=demo&from={j['next']}")[1])
        self.assertEqual([e["text"] for e in j["events"]], ["three"])

    def test_run_files_are_served_but_nothing_outside_runs(self):
        run = self.active_run()
        (run.path / "shots" / "a.png").write_bytes(b"\x89PNGx")
        status, body = self.call("GET", "/runs/demo/shots/a.png")
        self.assertEqual((status, body), (200, b"\x89PNGx"))
        for path in ("/runs/../.tnazar/ui.json", "/runs/%2e%2e/.tnazar/ui.json", "/runs/demo/../../.tnazar/ui.json"):
            self.assertEqual(self.call("GET", path)[0], 404, path)


DUMPSYS = """WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #0 Window{a1 u0 StatusBar}:
    mAttrs={(0,0)(fillx63) ty=STATUS_BAR fmt=TRANSLUCENT
      fl=NOT_FOCUSABLE HARDWARE_ACCELERATED
      pfl=SECURE_LOOKING_PRIVATE_FLAG}
    isOnScreen=true
    isVisible=true
  Window #1 Window{b2 u0 com.example.bank/com.example.bank.OtpActivity}:
    mAttrs={(0,0)(fillxfill) ty=BASE_APPLICATION fmt=TRANSLUCENT
      fl=LAYOUT_IN_SCREEN SECURE HARDWARE_ACCELERATED
      pfl=NONE}
    isOnScreen=%s
    isVisible=%s
"""


class SecureWindowTest(unittest.TestCase):
    def test_on_screen_secure_window_is_reported(self):
        self.assertEqual(ui.secure_window(DUMPSYS % ("true", "true")),
                         "com.example.bank/com.example.bank.OtpActivity")

    def test_secure_window_in_the_background_is_ignored(self):
        self.assertIsNone(ui.secure_window(DUMPSYS % ("false", "false")))

    def test_private_flags_do_not_count(self):
        self.assertIsNone(ui.secure_window(DUMPSYS.split("  Window #1")[0]))


if __name__ == "__main__":
    unittest.main()
