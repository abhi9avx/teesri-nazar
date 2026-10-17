"""Fast screen reads through the UiAutomator2 server, with `uiautomator dump` as the fallback."""

import unittest

from fakedevice import PACKAGE, server_xml, tnazar, login_app
from teesri_nazar import screen as scr
from teesri_nazar.session import state_dir

from test_cli import CliTestCase


def dumps(adb, since=0):
    return sum(1 for c in adb.calls[since:] if "uiautomator dump" in " ".join(c))


class ServerXmlTest(unittest.TestCase):
    def test_server_format_parses_like_a_dump(self):
        screen = scr.parse(server_xml(login_app().screens["login"]))
        login = scr.find(screen, scr.Selector(id="btn_login"))
        self.assertEqual((login.text, login.center, login.clickable), ("Login", (540, 860), True))
        self.assertEqual(scr.find(screen, scr.Selector(desc="Phone number")).short_id, "phone_input")
        self.assertTrue(scr.find(screen, scr.Selector(id="mpin_input")).password)

    def test_elements_the_server_marks_not_displayed_are_left_out(self):
        screen = scr.parse(server_xml(login_app().screens["login"]))
        self.assertIsNone(scr.find(screen, scr.Selector(text="Off-screen")))


class FastReadsTest(CliTestCase):
    def setUp(self):
        super().setUp()
        self.adb.uia2_installed = True

    def test_a_run_reads_the_screen_through_the_server(self):
        out = self.ok("start", "demo", "--package", PACKAGE)
        self.assertIn("screen reads: fast", out)
        since = len(self.adb.calls)
        self.ok("observe")
        self.ok("tap", "Login")
        self.ok("check", "Pay now")
        self.assertEqual(dumps(self.adb, since), 0)
        self.assertEqual(self.adb.uia2_reads, 3)

    def test_server_that_stops_answering_is_shut_down_and_dumps_take_over(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.adb.uia2_broken = True
        self.ok("check", "Login")  # still works, through uiautomator dump
        self.assertIn(["shell", "am", "force-stop", "io.appium.uiautomator2.server.test"], self.adb.calls)
        self.assertFalse((state_dir() / "uia2.json").exists())
        since = len(self.adb.calls)
        self.ok("observe")
        self.assertEqual(dumps(self.adb, since), 1)

    def test_a_passing_server_error_is_retried_not_treated_as_a_dead_server(self):
        self.ok("start", "demo", "--package", PACKAGE)
        # Android 14+: "Cannot set AccessibilityNodeInfo's field 'mSealed' to 'true'", now and then,
        # sometimes several reads in a row while a window changes.
        self.adb.uia2_errors = 5
        since = len(self.adb.calls)
        self.ok("check", "Login")
        self.assertEqual(dumps(self.adb, since), 0)
        self.assertTrue((state_dir() / "uia2.json").exists())

    def test_falling_back_to_slow_reads_is_announced(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.adb.uia2_broken = True
        _, _, err = tnazar("observe")
        self.assertIn("fast screen reads stopped", err)

    def test_stop_shuts_the_server_down(self):
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("stop")
        self.assertFalse(self.adb.uia2_up)
        self.assertFalse((state_dir() / "uia2.json").exists())

    def test_waits_poll_faster_when_reads_are_fast(self):
        self.ok("start", "demo", "--package", PACKAGE)
        tnazar("wait", "Nowhere", "--timeout", "1")
        self.assertEqual(self.adb.uia2_reads, 8)  # every 0.15 s instead of every 0.4 s


class NoServerTest(CliTestCase):
    def test_without_the_server_reads_use_uiautomator_dump(self):
        out = self.ok("start", "demo", "--package", PACKAGE)
        self.assertIn("screen reads: uiautomator dump", out)
        since = len(self.adb.calls)
        self.ok("observe")
        self.assertEqual(dumps(self.adb, since), 1)


if __name__ == "__main__":
    unittest.main()


class ServerErrorBodyTest(unittest.TestCase):
    def test_an_error_whose_body_cannot_be_read_still_gives_a_reason(self):
        # Python 3.9 builds a half-initialised HTTPError when there is no body; reading it raises
        # KeyError. Reading the reason must never turn a retryable server error into a crash.
        import urllib.error
        from teesri_nazar import uia2

        class Unreadable:
            def read(self, *a):
                raise KeyError("file")

            def close(self):
                pass
        err = urllib.error.HTTPError("/source", 500, "Internal Server Error", {}, Unreadable())
        self.assertEqual(uia2._error_message(err), "")
