"""The CLI's helpers after the split: targets, secret masking, .env loading, device constants."""

import os
import unittest
from pathlib import Path
from unittest import mock

from fakedevice import FakeApp, node, tnazar
from teesri_nazar import cli, commands, env, targets
from teesri_nazar import device as dev
from teesri_nazar import screen as scr

from test_cli import CliTestCase


class SelectorOfTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(targets.selector_of("Pay now").to_dict(), {"text": "Pay now"})
        self.assertEqual(targets.selector_of("~Pay").to_dict(), {"text": "Pay", "contains": True})
        self.assertEqual(targets.selector_of("Pay", contains=True).to_dict(), {"text": "Pay", "contains": True})
        self.assertEqual(targets.selector_of("id=btn_pay").to_dict(), {"id": "btn_pay"})

    def test_coordinates_or_an_index_are_not_a_selector(self):
        for raw in ("540,1860", "#3", ""):
            with self.assertRaises(cli.UserError):
                targets.selector_of(raw)



class ScrollWithinTest(CliTestCase):
    def test_scroll_within_coordinates_is_a_user_error(self):
        code, _, err = tnazar("scroll", "down", "--within", "1,2")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("error:", err)


class MaskTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(env, "secret_values", return_value={"482913", "42"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_long_values_are_hidden_inside_text_short_ones_only_whole(self):
        self.assertEqual(env.mask("code 482913 sent"), "code •••• sent")
        self.assertEqual(env.mask("42"), "••••")
        self.assertEqual(env.mask("tcp:8420"), "tcp:8420")

    def test_xml_attribute_values_are_masked(self):
        xml = '<hierarchy><node text="482913" content-desc="42" bounds="[0,42][10,50]"/></hierarchy>'
        out = env.mask_xml(xml)
        self.assertNotIn("482913", out)
        self.assertIn('content-desc="••••"', out)
        self.assertIn('bounds="[0,42][10,50]"', out)
        scr.parse(out)  # still valid XML

    def test_describe_masks_text_and_desc(self):
        el = scr.Element(0, "android.widget.EditText", "482913", "app:id/otp_box", "", (0, 0, 10, 10))
        self.assertNotIn("482913", env.describe(el))
        self.assertIn("otp_box", env.describe(el))


class DotenvTest(CliTestCase):
    def test_keys_are_those_of_the_current_dotenv_not_every_one_ever_loaded(self):
        Path(self.home, ".env").write_text("APP_ONE=first-value\n")
        with mock.patch.dict(os.environ, {}):
            env.load_dotenv()
            self.assertEqual(set(env.dotenv_keys()), {"APP_ONE"})
            Path(self.home, ".env").write_text("APP_TWO=second-value\n")
            env.load_dotenv()
            self.assertEqual(set(env.dotenv_keys()), {"APP_TWO"})
            self.assertEqual(cli.redact(["second-value"]), ["••••"])


class StrictWaitTest(CliTestCase):
    def test_exact_wait_ignores_a_longer_label(self):
        self.adb.app.screens["login"].append(node(text="Payment unsuccessful", bounds=(60, 1300, 1020, 1400)))
        ok, _, _ = dev.wait_for(self.adb, scr.Selector(text="successful"), 0, exact=True)
        self.assertFalse(ok)
        ok, _, _ = dev.wait_for(self.adb, scr.Selector(text="successful"), 0)
        self.assertTrue(ok)  # taps and waits keep the loose fallback

    def test_a_wait_still_matches_loosely(self):
        self.adb.app.screens["login"].append(node(text="Payment unsuccessful", bounds=(60, 1300, 1020, 1400)))
        self.ok("wait", "unsuccessful", "--timeout", "0")


class AdbSuccessTest(CliTestCase):
    def test_a_successful_adb_command_exits_0(self):
        import subprocess
        with mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.ok("adb", "shell", "true")


class AdbFailureReasonTest(CliTestCase):
    def test_a_failed_adb_command_records_why_without_secrets(self):
        import subprocess
        run = self.start()
        failed = subprocess.CompletedProcess([], 1, stderr="error: closed 7531\nmore detail\n")
        with mock.patch.object(cli.subprocess, "run", return_value=failed), \
                mock.patch.object(cli.live, "secret_values", return_value=["7531"]):
            code, _, err = tnazar("adb", "shell", "false")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("error: closed", err)
        [e] = [e for e in run.events() if e["type"] == "adb"]
        self.assertTrue(e["error"].startswith("error: closed"))
        self.assertNotIn("7531", e["error"])

    def test_adb_output_streams_through_untouched(self):
        import subprocess
        self.start()
        done = subprocess.CompletedProcess([], 0, stderr="")
        with mock.patch.object(cli.subprocess, "run", return_value=done) as run_adb:
            tnazar("adb", "logcat")
        kwargs = run_adb.call_args.kwargs
        self.assertNotIn("capture_output", kwargs)
        self.assertNotIn("stdout", kwargs)


class RegistryAndConstantsTest(unittest.TestCase):
    def test_cli_uses_the_shared_registry(self):
        self.assertEqual(cli.TIMED, commands.TIMED_EVENTS)
        self.assertEqual(cli.HELD, commands.DEVICE_COMMANDS)

    def test_device_constants(self):
        self.assertEqual(dev.CLEAR_KEYS, 40)
        self.assertEqual(dev.SCROLL_AREA, (0.15, 0.85))
        self.assertEqual(dev.SWIPE_MS, 350)
        self.assertEqual(dev.SCROLL_MAX, 10)
        args = cli.build_parser().parse_args(["scroll", "down"])
        self.assertEqual(args.max, dev.SCROLL_MAX)
        self.assertEqual(cli.build_parser().parse_args(["swipe", "1", "2", "3", "4"]).ms, dev.SWIPE_MS)

    def test_dead_code_is_gone(self):
        self.assertFalse(hasattr(dev, "foreground_package"))


if __name__ == "__main__":
    unittest.main()


class HorizontalScrollTest(CliTestCase):
    """`scroll left/right` finds the screen's horizontal scroller, so plans need no coordinates."""

    @staticmethod
    def app():
        tabs = node(cls="android.widget.HorizontalScrollView", clickable=False, scrollable=True,
                    bounds=(0, 280, 1080, 380))
        return FakeApp(
            screens={
                "first": [tabs, node(text="Cards", bounds=(0, 290, 400, 370)),
                          node(id="list", clickable=False, scrollable=True, bounds=(0, 400, 1080, 2200))],
                "last": [tabs, node(text="Loans", bounds=(680, 290, 1080, 370)),
                         node(id="list", clickable=False, scrollable=True, bounds=(0, 400, 1080, 2200))],
            },
            start="first",
            on_swipe={"first": "last"},
        )

    def swipes(self):
        return [c for c in self.adb.calls if c[:3] == ["shell", "input", "swipe"]]

    def test_scroll_right_swipes_inside_the_tab_bar(self):
        self.ok("scroll", "right", "--to", "Loans")
        y = {int(self.swipes()[0][4]), int(self.swipes()[0][6])}
        self.assertEqual(y, {330})  # the bar's centre line, wherever it is on this screen

    def test_vertical_scroll_still_uses_the_main_area(self):
        self.ok("scroll", "down")
        self.assertNotEqual(int(self.swipes()[0][4]), 330)

    def test_without_a_horizontal_scroller_the_middle_of_the_screen_is_used(self):
        self.adb.app.screens["first"] = [node(text="Cards", bounds=(0, 290, 400, 370))]
        self.ok("scroll", "right")
        self.assertEqual(int(self.swipes()[0][4]), 1200)


class RelativePositionTest(CliTestCase):
    """For an element with no label at all (an unlabelled icon), a position relative to the screen."""

    def test_percent_position_lands_on_the_same_spot_on_any_screen_size(self):
        self.ok("tap", "50%,25%")
        self.assertEqual(self.adb.taps[-1], (540, 600))  # this fake screen is 1080x2400
        self.adb.size = (720, 1600)
        self.ok("tap", "50%,25%")
        self.assertEqual(self.adb.taps[-1], (360, 400))

    def test_a_percent_over_100_is_refused(self):
        code, _, err = tnazar("tap", "120%,5%")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("%", err)

    def test_a_percent_position_is_not_a_selector(self):
        from teesri_nazar.targets import selector_of
        from teesri_nazar.errors import UserError
        with self.assertRaises(UserError):
            selector_of("9%,6%")
