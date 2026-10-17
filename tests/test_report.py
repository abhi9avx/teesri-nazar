import os
import tempfile
import unittest
from unittest import mock

from teesri_nazar import report as rpt
from teesri_nazar import visual
from teesri_nazar.session import Run


def temp_home(test: unittest.TestCase) -> None:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    env = mock.patch.dict(os.environ, {"TNAZAR_HOME": tmp.name})
    env.start()
    test.addCleanup(env.stop)


class ReportTest(unittest.TestCase):
    def setUp(self):
        temp_home(self)
        self.run = Run.create("demo", {"package": "com.example.app", "prompt": "test <login>"})
        log = self.run.log
        log({"type": "case_start", "case": "TC-01", "title": "Login works"})
        log({"type": "tap", "selector": {"text": "Login"}, "xy": [1, 1], "label": "Login", "dur_ms": 900})
        log({"type": "wait", "selector": {"desc": "homeScreen"}, "ok": True, "polls": 4, "dur_ms": 8400})
        log({"type": "check", "kind": "ui", "ok": True, "what": "Home visible", "case": "TC-01",
             "shot": "shots/a.png"})
        log({"type": "case_end", "case": "TC-01", "status": "pass", "reason": ""})
        log({"type": "case_start", "case": "TC-02", "title": "Wrong MPIN"})
        log({"type": "check", "kind": "db", "ok": False, "what": "attempts incremented",
             "detail": "got 0", "case": "TC-02"})
        log({"type": "note", "kind": "bug", "text": "no error shown", "case": "TC-02"})
        log({"type": "case_end", "case": "TC-02", "status": "fail", "reason": "failed: attempts"})
        log({"type": "case_start", "case": "TC-03", "title": "Never finished"})

    def test_steps_over_five_seconds_are_listed_with_their_screen_reads(self):
        s = rpt.summarize(self.run)
        self.assertEqual([(e["type"], e["dur_ms"]) for e in s["slow"]], [("wait", 8400)])
        md = rpt.to_markdown(self.run, s)
        self.assertIn("## Slow steps (> 5 s)", md)
        self.assertIn('| TC-01 | wait desc="homeScreen" | 8.4s | 4 screen reads |', md)
        self.assertIn("Slow steps", rpt.to_html(self.run, s))

    def test_summary(self):
        s = rpt.summarize(self.run)
        self.assertEqual(s["counts"]["pass"], 1)
        self.assertEqual(s["counts"]["fail"], 1)
        self.assertEqual(s["counts"]["running"], 1)
        self.assertEqual((s["checks_total"], s["checks_failed"]), (2, 1))
        self.assertEqual(len(s["bugs"]), 1)
        self.assertEqual(self.run.current_case(), "TC-03")

    def test_case_events_only_latest_case(self):
        events = rpt.case_events(self.run.events(), "TC-02")
        self.assertEqual([e["type"] for e in events], ["check", "note"])

    def test_write_escapes_html(self):
        md, page = rpt.write(self.run)
        self.assertIn("| TC-02 | Wrong MPIN | ❌ fail |", md.read_text())
        html = page.read_text()
        self.assertIn("test &lt;login&gt;", html)
        self.assertNotIn("test <login>", html)

    def test_duplicate_run_name_gets_suffix(self):
        again = Run.create("demo", {})
        self.assertNotEqual(again.name, "demo")
        self.assertTrue(again.name.startswith("demo-"))

    def test_artifact_names_do_not_collide(self):
        a = self.run.artifact("shots", "x", "png")
        a.write_bytes(b"1")
        self.assertNotEqual(a, self.run.artifact("shots", "x", "png"))


class ScreenshotTest(unittest.TestCase):
    """The report shows one screenshot per distinct screen, and puts failures first, highlighted."""

    def setUp(self):
        temp_home(self)
        self.run = Run.create("shots", {})
        log = self.run.log
        log({"type": "case_start", "case": "TC-01", "title": "Help page"})
        for what, shot in (("greeting shown", "a.png"), ("chat shown", "b.png"), ("menu shown", "c.png")):
            log({"type": "check", "kind": "ui", "ok": True, "what": what, "shot": f"shots/{shot}",
                 "screen_sig": "help", "case": "TC-01"})
        log({"type": "check", "kind": "ui", "ok": True, "what": "article opens", "shot": "shots/d.png",
             "screen_sig": "article", "case": "TC-01"})
        log({"type": "check", "kind": "ui", "ok": False, "what": "title matches", "shot": "shots/e.png",
             "screen_sig": "article", "case": "TC-01"})
        log({"type": "case_end", "case": "TC-01", "status": "fail", "reason": "failed: title matches",
             "evidence": ["shots/end.png", "logs/x.txt"]})

    def test_same_screen_is_shown_once_with_every_check_it_proves(self):
        page = rpt.to_html(self.run, rpt.summarize(self.run))
        self.assertIn("shots/a.png", page)
        self.assertNotIn("shots/b.png", page)
        self.assertNotIn("shots/c.png", page)
        self.assertIn("3 checks on this screen", page)

    def test_failures_come_first_and_are_highlighted(self):
        page = rpt.to_html(self.run, rpt.summarize(self.run))
        self.assertIn("class='shot failed'", page)
        self.assertIn("Failed here: title matches", page)
        self.assertLess(page.index("shots/e.png"), page.index("shots/a.png"))

    def test_end_of_case_screen_only_when_no_check_shows_the_failure(self):
        page = rpt.to_html(self.run, rpt.summarize(self.run))
        self.assertNotIn("shots/end.png", page)  # the failed check's own shot already shows it

    def test_links_are_readable_in_dark_mode(self):
        css = rpt.CSS
        self.assertIn("a{color:var(--link)}", css)
        self.assertEqual(css.count("--link:"), 2)  # one per theme


class VisualParseTest(unittest.TestCase):
    def test_envelope_and_fences(self):
        out = '{"type":"result","result":"```json\\n{\\"results\\":[{\\"pair\\":1,\\"ok\\":true,\\"reasoning\\":\\"same\\"}]}\\n```"}'
        self.assertEqual(visual._parse(out)[1]["ok"], True)

    def test_garbage(self):
        self.assertEqual(visual._parse("nope"), {})


if __name__ == "__main__":
    unittest.main()
