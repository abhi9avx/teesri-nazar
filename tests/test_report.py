import os
import tempfile
import unittest

from teesri_nazar import report as rpt
from teesri_nazar import visual
from teesri_nazar.session import Run


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TNAZAR_HOME"] = self.tmp.name
        self.run = Run.create("demo", {"package": "com.example.app", "prompt": "test <login>"})
        log = self.run.log
        log({"type": "case_start", "case": "TC-01", "title": "Login works"})
        log({"type": "tap", "selector": {"text": "Login"}, "xy": [1, 1]})
        log({"type": "check", "kind": "ui", "ok": True, "what": "Home visible", "case": "TC-01",
             "shot": "shots/a.png"})
        log({"type": "case_end", "case": "TC-01", "status": "pass", "reason": ""})
        log({"type": "case_start", "case": "TC-02", "title": "Wrong MPIN"})
        log({"type": "check", "kind": "db", "ok": False, "what": "attempts incremented",
             "detail": "got 0", "case": "TC-02"})
        log({"type": "note", "kind": "bug", "text": "no error shown", "case": "TC-02"})
        log({"type": "case_end", "case": "TC-02", "status": "fail", "reason": "failed: attempts"})
        log({"type": "case_start", "case": "TC-03", "title": "Never finished"})

    def tearDown(self):
        os.environ.pop("TNAZAR_HOME", None)
        self.tmp.cleanup()

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


class VisualParseTest(unittest.TestCase):
    def test_envelope_and_fences(self):
        out = '{"type":"result","result":"```json\\n{\\"results\\":[{\\"pair\\":1,\\"ok\\":true,\\"reasoning\\":\\"same\\"}]}\\n```"}'
        self.assertEqual(visual._parse(out)[1]["ok"], True)

    def test_garbage(self):
        self.assertEqual(visual._parse("nope"), {})


if __name__ == "__main__":
    unittest.main()
