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
import unittest
from pathlib import Path
from unittest import mock

from fakedevice import PACKAGE, FakeAdb, fake_device, login_app, tnazar
from teesri_nazar import cli, commands, live, replay, ui
from teesri_nazar import report as rpt
from teesri_nazar.session import get_active

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

    def test_a_wait_that_found_the_other_screen_says_so(self):
        [line] = self.describe({"type": "wait", "ok": False, "selector": {"id": "get_started_button"},
                                "found_instead": {"text": "Something went wrong"}, "timeout": 20})
        self.assertEqual(line, 'Expected "get started", but "Something went wrong" showed instead')

    def test_a_real_timeout_names_the_time(self):
        [line] = self.describe({"type": "wait", "ok": False, "selector": {"desc": "homeScreenTag"}, "timeout": 20})
        self.assertEqual(line, '"home screen" did not appear within 20s')

    def test_ids_read_like_words_and_labels_stay_as_written(self):
        lines = self.describe({"type": "tap", "selector": {"id": "HOME_TOOLBAR_PROFILE_BTN_ID"}, "label": "HOME_TOOLBAR_PROFILE_BTN_ID"},
                              {"type": "tap", "selector": {"desc": "App settings"}, "label": "App settings"},
                              {"type": "type", "secret": True, "env": "UI_MPIN", "selector": {"id": "pin_entry_input_field"}},
                              {"type": "type", "secret": True, "env": "UI_MPIN", "by": "dashboard"},
                              {"type": "launch", "restart": True})
        self.assertEqual(lines, ['Tap "home toolbar profile"', 'Tap "App settings"',
                                 'Type the MPIN (hidden) into "pin entry"',
                                 "You typed the MPIN from the dashboard (hidden)", "Restart the app"])
