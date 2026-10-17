import os
import shutil
import tempfile
import unittest
from unittest import mock

from teesri_nazar import session
from teesri_nazar.session import Run


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"TNAZAR_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)


class RunCreateTest(SessionTestCase):
    def test_layout_and_meta(self):
        run = Run.create("login", {"package": "com.example.app"})
        for sub in ("shots", "dumps", "logs"):
            self.assertTrue((run.path / sub).is_dir())
        self.assertTrue(run.events_path.exists())
        meta = run.meta()
        self.assertEqual(meta["name"], "login")
        self.assertEqual(meta["package"], "com.example.app")
        self.assertTrue(meta["started_at"].endswith("Z"))

    def test_rejects_unsafe_names(self):
        for bad in ("", "../escape", "a/b", "-dash-first", "with space", "x" * 65):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                Run.create(bad, {})

    def test_update_meta_merges(self):
        run = Run.create("r", {"a": 1})
        run.update_meta(b=2)
        self.assertEqual((run.meta()["a"], run.meta()["b"]), (1, 2))


class RunLoadTest(SessionTestCase):
    def test_by_name_and_by_path(self):
        run = Run.create("r", {})
        self.assertEqual(Run.load("r").path, run.path)
        self.assertEqual(Run.load(str(run.path)).path, run.path)

    def test_missing_run(self):
        with self.assertRaises(FileNotFoundError):
            Run.load("nope")


class EventsTest(SessionTestCase):
    def test_log_adds_timestamp_and_keeps_unicode(self):
        run = Run.create("r", {})
        run.log({"type": "note", "text": "₹500 shown"})
        self.assertIn("₹500", run.events_path.read_text())
        e = run.events()[0]
        self.assertEqual(e["text"], "₹500 shown")
        self.assertIn("t", e)

    def test_blank_lines_are_ignored(self):
        run = Run.create("r", {})
        run.log({"type": "note"})
        with run.events_path.open("a") as f:
            f.write("\n   \n")
        run.log({"type": "note"})
        self.assertEqual(len(run.events()), 2)

    def test_current_case_follows_start_and_end(self):
        run = Run.create("r", {})
        self.assertIsNone(run.current_case())
        run.log({"type": "case_start", "case": "TC-01"})
        self.assertEqual(run.current_case(), "TC-01")
        run.log({"type": "case_end", "case": "TC-01", "status": "pass"})
        self.assertIsNone(run.current_case())
        run.log({"type": "case_start", "case": "TC-02"})
        self.assertEqual(run.current_case(), "TC-02")


class ArtifactTest(SessionTestCase):
    def test_label_cannot_escape_the_run_dir(self):
        run = Run.create("r", {})
        p = run.artifact("shots", "../../etc/passwd", "png")
        self.assertEqual(p.parent, run.path / "shots")
        self.assertNotIn("..", p.name)

    def test_empty_label_uses_kind(self):
        run = Run.create("r", {})
        self.assertTrue(run.artifact("dumps", "///", "xml").name.endswith("-dumps.xml"))

    def test_rel_is_relative_to_run(self):
        run = Run.create("r", {})
        self.assertEqual(run.rel(run.path / "shots" / "a.png"), os.path.join("shots", "a.png"))


class ActivePointerTest(SessionTestCase):
    def test_set_get_clear(self):
        self.assertIsNone(session.get_active())
        run = Run.create("r", {})
        session.set_active(run, "emulator-5554")
        active_run, serial = session.get_active()
        self.assertEqual((active_run.path, serial), (run.path, "emulator-5554"))
        session.clear_active()
        self.assertIsNone(session.get_active())
        session.clear_active()  # idempotent

    def test_pointer_to_deleted_run_is_dropped(self):
        run = Run.create("r", {})
        session.set_active(run, "s")
        shutil.rmtree(run.path)
        self.assertIsNone(session.get_active())
        self.assertFalse((session.state_dir() / "active.json").exists())


if __name__ == "__main__":
    unittest.main()
