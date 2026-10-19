"""Phase 0 of the navigation map: pin today's behaviour before anything changes.

These tests describe what the tool does now (output, screen reads, which commands refresh
`last-screen.xml`, exit codes). Every later change must leave them green: "never worse".
"""

import unittest

from fakedevice import PACKAGE, FakeAdb, FakeApp, fake_device, login_app, node, tnazar
from teesri_nazar.session import state_dir

OBSERVE = (
    'screen: com.example.app  7 elements (6 shown)\n'
    '[1] TextView id=title text="Welcome back" @540,250\n'
    '[2] EditText id=phone_input desc="Phone number" @540,460 tap\n'
    '[3] EditText id=mpin_input @540,620 tap password\n'
    '[4] Button id=btn_login text="Login" @540,860 tap\n'
    '[5] TextView id=btn_forgot text="Forgot MPIN" @540,935 tap disabled\n'
    '[6] CheckBox id=remember text="Remember me" @540,975 tap checked\n'
)
OBSERVE_ALL = OBSERVE.replace("(6 shown)\n", "(7 shown)\n[0] FrameLayout @540,1200\n")


def icon_app() -> FakeApp:
    """A toolbar with an unlabelled icon, a list that grows, and a tab bar."""
    return FakeApp(
        screens={
            "main": [
                node(cls="android.widget.ImageView", bounds=(980, 100, 1060, 180)),
                node(id="title", text="Inbox", clickable=False, bounds=(60, 100, 600, 180)),
                node(id="list", cls="androidx.recyclerview.widget.RecyclerView", clickable=False,
                     scrollable=True, bounds=(0, 200, 1080, 2000)),
                node(id="row", text="Row 1", bounds=(0, 200, 1080, 400)),
            ],
            "more": [
                node(cls="android.widget.ImageView", bounds=(980, 100, 1060, 180)),
                node(id="title", text="Inbox", clickable=False, bounds=(60, 100, 600, 180)),
                node(id="list", cls="androidx.recyclerview.widget.RecyclerView", clickable=False,
                     scrollable=True, bounds=(0, 200, 1080, 2000)),
                node(id="row", text="Row 9", bounds=(0, 200, 1080, 400)),
            ],
        },
        start="main",
        on_swipe={"main": "more"},
    )


class Phase0(unittest.TestCase):
    app = staticmethod(login_app)

    def setUp(self):
        self.adb = FakeAdb(self.app())
        ctx = fake_device(self.adb)
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def run_(self, *argv, code=0):
        got, out, err = tnazar(*argv)
        self.assertEqual(got, code, f"tnazar {' '.join(argv)} -> {got}\n{out}{err}")
        return out

    def start(self):
        self.run_("start", "demo", "--package", PACKAGE)

    def reads(self, *argv, code=0):
        dumps, server = len(self.adb.calls), self.adb.uia2_reads
        self.run_(*argv, code=code)
        dump_reads = sum(1 for c in self.adb.calls[dumps:] if "uiautomator dump" in " ".join(c))
        return dump_reads + self.adb.uia2_reads - server


class ObserveOutputTest(Phase0):
    def test_observe_output_is_byte_identical(self):
        self.assertEqual(self.run_("observe"), OBSERVE)
        self.assertEqual(self.run_("observe", "--all"), OBSERVE_ALL)

    def test_index_counts_every_element_not_only_the_shown_ones(self):
        self.run_("observe")
        self.run_("tap", "#4")  # Login is element 4 of 7 (element 0 is the bare layout, not shown)
        self.assertEqual(self.adb.current, "home")


class UnlabelledIconTest(Phase0):
    app = staticmethod(icon_app)

    def test_an_unlabelled_icon_prints_as_a_bare_class(self):
        out = self.run_("observe")
        self.assertIn("[1] ImageView @1020,140 tap\n", out)

    def test_scrolling_stops_when_the_screen_stops_changing_even_if_only_text_changed(self):
        self.start()
        self.run_("scroll", "down", "--to", "Row 9")
        self.assertEqual(self.adb.current, "more")


class ReadBudgetTest(Phase0):
    """Screen reads per command: the cost "never worse" is measured in. Both readers."""

    TABLE = [
        (("observe",), 1, 0),
        (("tap", "Login"), 1, 0),
        (("key", "back"), 0, 0),
        (("tap", "540,860"), 0, 0),
        (("wait", "Pay now"), 1, 0),
        (("check", "Pay now"), 1, 0),
        (("check", "Login", "--gone"), 1, 0),
        (("scroll", "down"), 0, 0),
        (("scroll", "down", "--to", "Pay now"), 1, 0),
        (("launch",), 0, 0),
        (("open-url", "app://x"), 0, 0),
        (("say", "x"), 0, 0),
        (("shot",), 0, 0),
    ]

    def check_table(self):
        for argv, want, code in self.TABLE:
            if argv[0] == "tap" and argv[1] == "Login":
                self.adb.current = "login"
            with self.subTest(argv=argv):
                self.assertEqual(self.reads(*argv, code=code), want)

    def test_dump_reader(self):
        self.start()
        self.check_table()

    def test_server_reader(self):
        self.adb.uia2_installed = True
        self.start()
        self.assertTrue(self.adb.uia2_up)
        self.check_table()

    def test_index_tap_and_type_into(self):
        self.start()
        self.run_("observe")
        self.assertEqual(self.reads("type", "98", "--into", "id=phone_input"), 1)
        self.assertEqual(self.reads("tap", "#4"), 0)


class LastScreenTest(Phase0):
    """Which commands refresh last-screen.xml (what `#n` points at). Only reading `observe`,
    reading `scroll` and `--wait 0` taps do; waits, checks and default taps do not."""

    def last(self) -> str:
        f = state_dir() / "last-screen.xml"
        return f.read_text() if f.exists() else ""

    def test_writers(self):
        self.start()
        self.run_("observe")
        self.assertIn("Welcome back", self.last())
        self.run_("tap", "Login")  # default --wait 3: reads through the device poll
        self.run_("check", "Pay now")
        self.run_("wait", "Pay now")
        self.assertIn("Welcome back", self.last())  # still the login screen
        self.run_("scroll", "down", "--to", "Pay now")
        self.assertIn("Pay now", self.last())
        self.adb.current = "login"
        self.run_("tap", "Login", "--wait", "0")
        self.assertIn("Welcome back", self.last())  # read before the tap


class ExitCodeTest(Phase0):
    def test_exit_codes(self):
        self.start()
        self.run_("check", "Nothing here", "--timeout", "1", code=1)
        self.run_("tap", "Nothing here", "--wait", "0", code=1)
        self.run_("wait", "Pay now", "--or", "Login", "--timeout", "1", code=4)
        self.run_("flow", "nope", code=2)
        self.run_("check", "Login")


class MapIsolationTest(Phase0):
    def test_tests_never_learn_into_the_users_map(self):
        import os
        from pathlib import Path
        from teesri_nazar import navmap
        self.assertNotEqual(navmap.map_dir(), Path.home() / ".tnazar" / "maps")
        self.assertTrue(str(navmap.map_dir()).startswith(os.environ["TNAZAR_HOME"]))


if __name__ == "__main__":
    unittest.main()
