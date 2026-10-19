import argparse
import unittest

from teesri_nazar import device as dev
from teesri_nazar.cli import UserError, parse_target
from teesri_nazar.replay import _scale


class InputTextTest(unittest.TestCase):
    def test_spaces_and_shell_characters(self):
        self.assertEqual(dev.escape_input_text("a b"), "a%sb")
        self.assertEqual(dev.escape_input_text("it's $5 & (ok)"), "it\\'s%s\\$5%s\\&%s\\(ok\\)")
        self.assertEqual(dev.escape_input_text("user@x.com"), "user@x.com")

    def test_non_ascii_is_rejected(self):
        with self.assertRaises(ValueError):
            dev.escape_input_text("₹500")


class ScrollTest(unittest.TestCase):
    def test_down_moves_finger_up(self):
        x1, y1, x2, y2 = dev.scroll_vector("down", (0, 0, 1000, 2000))
        self.assertEqual(x1, x2)
        self.assertGreater(y1, y2)

    def test_bad_direction(self):
        with self.assertRaises(ValueError):
            dev.scroll_vector("sideways", (0, 0, 10, 10))


class TargetTest(unittest.TestCase):
    def parse(self, raw, **kw):
        return parse_target(raw, argparse.Namespace(**kw))

    def test_forms(self):
        self.assertEqual(self.parse("540,1860"), ("xy", (540, 1860)))
        self.assertEqual(self.parse("#12"), ("index", 12))
        self.assertEqual(self.parse("id=btn_pay")[1].to_dict(), {"id": "btn_pay"})
        self.assertEqual(self.parse("desc=Back")[1].to_dict(), {"desc": "Back"})
        self.assertEqual(self.parse("~Pay")[1].to_dict(), {"text": "Pay", "contains": True})
        self.assertEqual(self.parse("Pay now")[1].to_dict(), {"text": "Pay now"})
        self.assertEqual(self.parse("a=b")[1].to_dict(), {"text": "a=b"})

    def test_flags_combine(self):
        kind, sel = self.parse(None, id="row", text="Rent", nth=1)
        self.assertEqual(sel.to_dict(), {"id": "row", "text": "Rent", "nth": 1})

    def test_empty_is_an_error(self):
        with self.assertRaises(UserError):
            self.parse(None)


class ScaleTest(unittest.TestCase):
    def test_scales_to_new_screen(self):
        self.assertEqual(_scale([540, 1200], [1080, 2400], (1440, 3200)), (720, 1600))

    def test_no_recorded_size_keeps_point(self):
        self.assertEqual(_scale([10, 20], None, (1, 1)), (10, 20))


if __name__ == "__main__":
    unittest.main()


class AdbOutputTest(unittest.TestCase):
    def test_bytes_that_are_not_utf8_do_not_crash_a_read(self):
        # Logcat carries whatever apps log; one stray byte used to crash `case end`.
        import os, stat, tempfile
        from teesri_nazar.adb import Adb
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "adb")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\nprintf 'before \\300 after'\n")
            os.chmod(fake, stat.S_IRWXU)
            adb = Adb("fake-1")
            adb._bin = fake
            self.assertEqual(dev.logcat_dump(adb), "before � after")
