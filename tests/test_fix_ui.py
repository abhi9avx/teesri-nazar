"""Replay, report and dashboard fixes: shared event lists, cleanup, exit codes, slow threshold, a11y."""

import contextlib
import io
import json
import os
import re
import socket
import shutil
import stat
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from fakedevice import PACKAGE, FakeAdb, fake_device, login_app, tnazar
from teesri_nazar import cli, commands, live, replay, ui
from teesri_nazar import report as rpt
from teesri_nazar.session import Run, get_active

import test_coverage
import test_live
from test_replay import ReplayTestCase

HTML = Path(ui.__file__).with_name("ui.html").read_text()


class ReplayRegistryTest(ReplayTestCase):
    def test_if_and_wait_online_events_replay_and_if_is_not_a_step(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "start", "TC-01")
        run = get_active()[0]
        run.log({"type": "if", "target": "Login", "found": True, "reads": 1, "case": "TC-01"})
        run.log({"type": "wait_online", "ok": True, "timeout": 5, "case": "TC-01"})
        run.log({"type": "tap", "selector": {"text": "Nowhere"}, "case": "TC-01"})
        self.ok("case", "end")
        self.ok("stop")
        self.fresh_device()
        code, out, _ = self.replay("--step-timeout", "1")
        self.assertNotIn("Traceback", out)
        self.assertIn("step 2 failed", out)  # wait_online is step 1; `if` only narrates
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_device_is_restored_when_the_screen_server_fails_to_start(self):
        self.record_login()
        self.fresh_device()
        with mock.patch.object(replay.uia2, "start", side_effect=RuntimeError("boom")):
            with contextlib.suppress(RuntimeError):
                tnazar("replay", "demo")
        self.assertEqual(self.adb.settings["window_animation_scale"], "1.0")


class ReportSharedListsTest(ReplayTestCase):
    def test_actions_come_from_the_registry(self):
        self.assertEqual(set(rpt.ACTIONS), set(commands.ACTION_EVENTS))

    def test_slow_heading_follows_the_threshold(self):
        self.record_login()
        from teesri_nazar.session import Run
        run = Run.load("demo")
        run.log({"type": "wait", "selector": {"text": "X"}, "ok": True, "dur_ms": 12000, "case": "TC-02"})
        with mock.patch.object(rpt, "SLOW_MS", 10000):
            s = rpt.summarize(run)
            self.assertIn("## Slow steps (> 10 s)", rpt.to_markdown(run, s))
            self.assertIn("Slow steps (&gt; 10 s)", rpt.to_html(run, s))


class DashboardFixesTest(unittest.TestCase):
    setUp = test_live.ServerTest.setUp
    call = test_live.ServerTest.call
    active_run = test_live.ServerTest.active_run

    def test_state_serves_the_slow_threshold(self):
        status, body = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["slow_ms"], rpt.SLOW_MS)

    def test_negative_offset_is_clamped_and_junk_is_rejected(self):
        run = self.active_run()
        run.log({"type": "note", "text": "hello"})
        status, body = self.call("GET", "/api/events?run=demo&from=-1")
        self.assertEqual(status, 200)
        self.assertEqual([e["type"] for e in json.loads(body)["events"]], ["note"])
        status, _ = self.call("GET", "/api/events?run=demo&from=abc")
        self.assertEqual(status, 400)

    def test_ui_json_is_created_private(self):
        modes = []
        real_open = os.open

        def spy(path, flags, mode=0o777, *a, **kw):
            if str(path).endswith("ui.json"):
                modes.append(mode)
            return real_open(path, flags, mode, *a, **kw)

        with mock.patch.object(ui.os, "open", spy):
            httpd, _ = ui.start(0)
            httpd.server_close()
        self.assertEqual(modes, [0o600])
        self.assertEqual(stat.S_IMODE(live.ui_file().stat().st_mode), 0o600)

    def test_port_in_use_errors_to_stderr(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = ui.serve(s.getsockname()[1], open_browser=False)
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("cannot listen", err.getvalue())
        self.assertEqual(out.getvalue(), "")


class PageTest(unittest.TestCase):
    def test_slow_threshold_is_not_hardcoded(self):
        self.assertNotRegex(HTML, r"dur_ms > \d")
        self.assertIn("state.slow_ms", HTML)

    def test_colour_tokens_match_the_report(self):
        self.assertNotRegex(HTML, r"--ok\b|--bad\b")
        self.assertIn("var(--pass)", HTML)
        self.assertIn("var(--fail)", HTML)

    def test_live_regions_and_keyboard_rows(self):
        for id_ in ("msg-err", "sec-err", "status-text"):
            self.assertRegex(HTML, rf'id="{id_}"[^>]*aria-live="polite"')
        self.assertIn('"role", "button"', HTML)
        self.assertIn("tabIndex = 0", HTML)
        self.assertRegex(HTML, r'ev\.key === "Enter" \|\| ev\.key === " "')

    def test_dark_mode_fills_use_dark_text(self):
        dark = re.search(r"@media \(prefers-color-scheme: dark\) \{(.*?)\n\}", HTML, re.S).group(1)
        self.assertIn("--on-fill: #111418", dark)
        self.assertIn(".dur.slow { color: var(--on-fill)", HTML)
        self.assertIn(".pill { font-size: 12px; padding: 1px 8px; border-radius: 10px; color: var(--on-fill)", HTML)


@unittest.skipUnless(shutil.which("node"), "needs node to run the dashboard's describe()")
class TimelineWordingTest(unittest.TestCase):
    """The timeline says what happened in plain words, and never 'timed out' when something else showed."""

    def describe(self, *events):
        a = HTML.index("const fmtDur")
        script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
                  HTML.index("// ------------------------------------------------------------------ timeline")]
                  + f"\nfor (const e of {json.dumps(events)}) console.log(describe(e)[1]);")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
        return out.splitlines()

    def describe_rows(self, *events):
        """[line, detail] for each event."""
        a = HTML.index("const fmtDur")
        script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
                  HTML.index("// ------------------------------------------------------------------ timeline")]
                  + f"\nconsole.log(JSON.stringify({json.dumps(events)}.map((e) => {{ const d = describe(e); return [d[1], d[2] || \"\"]; }})));")
        return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)

    def agent_line(self, status: dict, age_ms: int) -> str:
        a = HTML.index("const fmtDur")
        script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
                  HTML.index("// ------------------------------------------------------------------ timeline")]
                  + f"\nconsole.log(agentLine({json.dumps(status)}, {age_ms})[0]);")
        return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()

    def test_a_wait_that_found_the_other_screen_says_so(self):
        [line] = self.describe({"type": "wait", "ok": False, "selector": {"id": "btn_continue"},
                                "found_instead": {"text": "Something went wrong"}, "timeout": 20})
        self.assertEqual(line, 'Expected "continue", but "Something went wrong" showed instead')

    def test_a_real_timeout_names_the_time_and_an_old_event_without_one_still_reads(self):
        lines = self.describe({"type": "wait", "ok": False, "selector": {"desc": "mainScreen"}, "timeout": 20},
                              {"type": "wait", "ok": False, "selector": {"desc": "mainScreen"}},
                              {"type": "wait_online", "ok": False})
        self.assertEqual(lines, ['"main screen" did not appear within 20s', '"main screen" did not appear',
                                 "Still offline"])

    def test_ids_read_like_words_and_words_stay_as_written(self):
        lines = self.describe({"type": "tap", "selector": {"id": "PROFILE_BTN_ID"}, "label": "PROFILE_BTN_ID"},
                              {"type": "tap", "selector": {"desc": "App settings"}, "label": "App settings"},
                              {"type": "tap", "selector": {"desc": "OK"}, "label": "OK"},
                              {"type": "tap", "selector": {"text": "Pay", "contains": True}, "label": "Pay now"},
                              {"type": "type", "secret": True, "env": "UI_MPIN", "selector": {"id": "pin_input_field"}},
                              {"type": "type", "secret": True, "env": "UI_MPIN", "by": "dashboard"},
                              {"type": "launch", "restart": True})
        self.assertEqual(lines, ['Tap "profile"', 'Tap "App settings"', 'Tap "OK"', 'Tap "Pay" (partial)',
                                 'Type the MPIN (hidden) into "pin"',
                                 "You typed the MPIN from the dashboard (hidden)", "Restart the app"])

    def test_check_and_if_lines_name_the_element_in_words(self):
        lines = self.describe({"type": "check", "ok": False, "what": "id=pin_input_field is visible"},
                              {"type": "check", "ok": False, "what": "id=wifi_switch is visible, checked"},
                              {"type": "check", "ok": False, "what": "desc=Navigate up is visible"},
                              {"type": "check", "ok": True, "what": '"Settings" is visible'},
                              {"type": "check", "ok": True, "kind": "db", "what": "txn row status = SUCCESS"},
                              {"type": "if", "target": "id=btn_continue", "found": True},
                              {"type": "if", "target": "desc=Navigate up", "found": True},
                              {"type": "if", "target": "~Pay", "found": False},
                              {"type": "if", "target": "Home", "found": True},
                              {"type": "tap", "selector": {"id": "com.example.app:id/btn_pay"}})
        self.assertEqual(lines, ['"pin" is visible', '"wifi switch" is visible, checked',
                                 '"Navigate up" is visible', '"Settings" is visible',
                                 "[db] txn row status = SUCCESS",
                                 'Is "continue" on screen? Yes', 'Is "Navigate up" on screen? Yes',
                                 'Is "Pay" (partial) on screen? No', 'Is "Home" on screen? Yes', 'Tap "pay"'])

    def test_a_step_that_could_not_run_says_why(self):
        rows = self.describe_rows({"type": "tap", "ok": False, "target": "Nowhere", "error": 'not found: "Nowhere"'},
                                  {"type": "type", "by": "dashboard", "ok": False, "env": "UI_MPIN",
                                   "error": "the device is offline"},
                                  {"type": "adb", "args": ["shell", "false"], "exit": 1, "error": "error: closed"})
        self.assertEqual(rows, [['Could not tap "Nowhere"', 'not found: "Nowhere"'],
                                ["Typing the MPIN from the dashboard failed", "the device is offline"],
                                ["Device command: adb shell false", "failed (exit 1): error: closed"]])

    def test_the_detail_line_keeps_what_the_old_timeline_showed(self):
        rows = self.describe_rows({"type": "launch", "package": "com.example.app"},
                                  {"type": "swipe", "from": [1, 2], "to": [3, 4]},
                                  {"type": "shot", "label": "login"},
                                  {"type": "hold", "waited_ms": 60000, "resumed": False})
        self.assertEqual(rows, [["Open the app", "com.example.app"], ["Swipe", "from 1,2 to 3,4"],
                                ["Screenshot: login", ""],
                                ["Paused by you for 1m 0s", "the agent was told to wait until you resume"]])

    def js(self, expr: str):
        a = HTML.index("const fmtDur")
        script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
                  HTML.index("// ------------------------------------------------------------------ timeline")]
                  + f"\nconsole.log(JSON.stringify({expr}));")
        return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)

    def test_your_message_is_never_shown_below_steps_that_came_after_it(self):
        events = [{"type": "say", "text": "Opening search"}, {"type": "tap", "label": "Search"},
                  {"type": "user_msg", "text": "now go back"}, {"type": "key", "key": "back"},
                  {"type": "say", "text": "Going back"}, {"type": "key", "key": "back"}]
        blocks = self.js(f"layout({json.dumps(events)}).map((b) => [b.kind, b.steps ? b.steps.map((e) => e.type) : b.e.type])")
        self.assertEqual(blocks, [["group", ["tap"]], ["user", "user_msg"], ["step", "key"], ["group", ["key"]]])

    def test_the_time_shows_only_on_lines_it_belongs_to(self):
        timed = lambda st, age: self.js(f"agentLine({json.dumps(st)}, {age})")[1]
        self.assertTrue(timed({"state": "got_message", "text": "x", "listening": True}, 1000))
        self.assertTrue(timed({"state": "working", "text": "x", "listening": True}, 1000))
        self.assertFalse(timed({"state": "working", "text": "x", "listening": True}, 120000))
        self.assertFalse(timed({"state": "idle", "text": "", "listening": False}, 0))

    def test_the_status_line_never_gets_stuck(self):
        listening = {"state": "idle", "text": "", "listening": True}
        self.assertTrue(self.agent_line(listening, 0).startswith("🟢 Listening"))
        self.assertTrue(self.agent_line({**listening, "listening": False}, 0).startswith("⚪ Not listening"))
        got = {"state": "got_message", "text": "open help", "listening": False}
        self.assertTrue(self.agent_line(got, 1000).startswith("📨 Got your message"))
        self.assertTrue(self.agent_line(got, 10000).startswith("🤔 Thinking about your message"))
        self.assertIn("may have stopped", self.agent_line(got, 300000))
        working = {"state": "working", "text": "Opening settings", "listening": True}
        self.assertTrue(self.agent_line(working, 1000).startswith("⚙️ Working"))
        self.assertTrue(self.agent_line(working, 120000).startswith("🟢 Listening"))



@unittest.skipUnless(shutil.which("node"), "needs node to run the dashboard's nextView()")
class LatestRunTest(unittest.TestCase):
    """The open page moves to a run that starts, and with nothing running shows the newest run."""

    RUNS = [{"name": "new", "started_at": "2026-10-30T10:00:00.000Z"},
            {"name": "old", "started_at": "2026-10-21T18:00:00.000Z"}]

    def next_view(self, cur: dict, active, runs=None):
        return TimelineWordingTest.js(self, f"nextView({json.dumps(cur)}, {json.dumps(active)}, "
                                            f"{json.dumps(self.RUNS if runs is None else runs)})")

    def test_a_run_that_starts_takes_over_the_open_page(self):
        old = {"view": "old", "follow": True, "lastActive": None, "started": "2026-10-21T18:00:00.000Z"}
        self.assertEqual(self.next_view(old, "run2"), "run2")
        picked = {**old, "follow": False}  # a past run you chose from the list
        self.assertEqual(self.next_view(picked, "run2"), "run2")
        self.assertIsNone(self.next_view({**old, "view": "run2", "lastActive": "run2"}, "run2"))

    def test_a_past_run_you_pick_while_one_runs_stays_until_another_starts(self):
        picked = {"view": "old", "follow": False, "lastActive": "run2", "started": "2026-10-21T18:00:00.000Z"}
        self.assertIsNone(self.next_view(picked, "run2"))
        self.assertEqual(self.next_view(picked, "run3"), "run3")

    def test_with_nothing_running_the_page_shows_the_newest_run_and_never_an_older_one(self):
        self.assertEqual(self.next_view({"view": None, "follow": True, "lastActive": None}, None), "new")
        old = {"view": "old", "follow": True, "lastActive": None, "started": "2026-10-21T18:00:00.000Z"}
        self.assertEqual(self.next_view(old, None), "new")
        just_ended = {"view": "run2", "follow": True, "lastActive": "run2", "started": "2026-10-30T11:00:00.000Z"}
        self.assertIsNone(self.next_view(just_ended, None))  # the list is polled later: keep the newer run
        self.assertIsNone(self.next_view({**old, "follow": False}, None))  # you picked it
        self.assertIsNone(self.next_view({**old, "started": None}, None))  # start unknown: stay
        self.assertIsNone(self.next_view({"view": None, "follow": True, "lastActive": None}, None, []))

    def test_the_run_list_is_newest_start_first_whatever_touched_the_folders(self):
        test_coverage.temp_home(self)
        Run.create("newer", {"package": "com.example.app"})
        older = Run.create("older", {"package": "com.example.app"})
        older.update_meta(started_at="2020-01-01T00:00:00.000Z")
        (older.path / "report.html").write_text("rewritten")  # moves the folder's mtime
        future = time.time() + 60
        os.utime(older.path, (future, future))
        (ui.runs_dir() / "not-a-run").mkdir()
        self.assertEqual([r["name"] for r in ui.Dashboard(0).runs()], ["newer", "older"])
