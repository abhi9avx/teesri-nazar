"""`tnazar doctor`, the device-state parsing it relies on, and the setup errors every command gives.

Test IDs (P0-01, PARSE-02, ...) are the ones in docs/test-plan-doctor.md.
"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fakedevice import PACKAGE, FakeAdb, fake_device, login_app, tnazar
from teesri_nazar import adb as adbmod
from teesri_nazar import uia2

FIXTURES = Path(__file__).parent / "fixtures" / "adb"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


@contextlib.contextmanager
def adb_says(devices_out: str):
    """`adb devices -l` prints `devices_out`; no real adb is needed or run."""
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, devices_out, "")
    with mock.patch.object(adbmod, "adb_binary", lambda: "/fake/adb"), \
            mock.patch.object(adbmod.subprocess, "run", run):
        yield


def put_apks(version="9.11.1") -> Path:
    base = Path(os.environ["APPIUM_HOME"]) / "node_modules/appium-uiautomator2-server/apks"
    base.mkdir(parents=True)
    for name in (f"appium-uiautomator2-server-v{version}.apk", "appium-uiautomator2-server-debug-androidTest.apk"):
        (base / name).write_bytes(b"")
    return base


class Phase0Test(unittest.TestCase):
    """Today's behaviour, pinned before any change (test plan §3)."""

    def test_p0_01_pick_serial_keeps_its_choices(self):
        with adb_says(fixture("ready")), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANDROID_SERIAL", None)
            self.assertEqual(adbmod.pick_serial(), "emulator-5554")
            self.assertEqual(adbmod.pick_serial("given-1"), "given-1")
            os.environ["ANDROID_SERIAL"] = "env-1"
            self.assertEqual(adbmod.pick_serial(), "env-1")
            os.environ.pop("ANDROID_SERIAL")
        with adb_says(fixture("two_ready")):
            with self.assertRaisesRegex(adbmod.AdbError, "several devices connected"):
                adbmod.pick_serial()

    def test_p0_02_start_line_install_text_is_unchanged(self):
        adb = FakeAdb(login_app())
        with fake_device(adb):
            base = put_apks()
            with mock.patch.object(uia2, "start", lambda a: False):
                line = uia2.start_line(adb)
        self.assertEqual(line, (
            "screen reads: uiautomator dump (~2 s per read)\n"
            "  the UiAutomator2 server is not installed on fake-1; for ~0.05 s reads install it once:\n"
            f"    adb -s fake-1 install -r -g {base / 'appium-uiautomator2-server-v9.11.1.apk'}\n"
            f"    adb -s fake-1 install -r -g {base / 'appium-uiautomator2-server-debug-androidTest.apk'}"))

    def test_p0_03_a_ready_device_is_parsed(self):
        with adb_says(fixture("ready")):
            [d] = adbmod.list_devices()
        self.assertEqual((d.serial, d.state, d.model), ("emulator-5554", "device", "Pixel_9_Pro"))


class ParseDevicesTest(unittest.TestCase):
    """PARSE: `adb devices -l` as every adb version and OS prints it."""

    def devices(self, text):
        return [(d.serial, d.state, d.model) for d in adbmod.parse_devices(text)]

    def test_parse_01_adb_server_start_banner_is_not_a_device(self):
        self.assertEqual(self.devices(fixture("daemon_start")), [("emulator-5554", "device", "Pixel_9_Pro")])

    def test_parse_02_linux_no_permissions_is_one_state(self):
        self.assertEqual(self.devices(fixture("no_permissions")), [("R58M00TEST01", "no permissions", "")])

    def test_parse_03_unauthorized_has_no_model(self):
        self.assertEqual(self.devices(fixture("unauthorized")), [("R58M00TEST01", "unauthorized", "")])

    def test_parse_04_no_devices(self):
        self.assertEqual(self.devices(fixture("empty")), [])

    def test_parse_05_crlf_and_trailing_blank_lines(self):
        text = fixture("mixed").replace("\n", "\r\n") + "\r\n\r\n"
        self.assertEqual(self.devices(text), [("R58M00TEST01", "unauthorized", ""),
                                              ("emulator-5554", "device", "Pixel_9_Pro")])

    def test_parse_06_every_state_adb_prints(self):
        for state in ("offline", "recovery", "sideload", "bootloader", "authorizing", "connecting"):
            with self.subTest(state=state):
                text = f"List of devices attached\nR58M00TEST01\t{state} usb:1-1 transport_id:2\n"
                self.assertEqual(self.devices(text), [("R58M00TEST01", state, "")])

    def test_parse_08_adb_version_mismatch_banner_is_not_a_device(self):
        text = ("adb server version (41) doesn't match this client (39); killing...\n"
                + fixture("ready"))
        self.assertEqual(self.devices(text), [("emulator-5554", "device", "Pixel_9_Pro")])

    def test_parse_07_list_devices_gives_up_when_adb_hangs(self):
        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        with mock.patch.object(adbmod, "adb_binary", lambda: "/fake/adb"), \
                mock.patch.object(adbmod.subprocess, "run", hang):
            with self.assertRaisesRegex(adbmod.AdbError, "adb devices did not answer"):
                adbmod.list_devices()


@contextlib.contextmanager
def no_android_serial():
    with mock.patch.dict(os.environ):
        os.environ.pop("ANDROID_SERIAL", None)
        yield


class PickSerialTest(unittest.TestCase):
    """PICK: every command that finds no ready device says why and points at doctor."""

    def error(self, name_or_text):
        text = fixture(name_or_text) if "\n" not in name_or_text else name_or_text
        with adb_says(text), no_android_serial(), self.assertRaises(adbmod.AdbError) as ctx:
            adbmod.pick_serial()
        return str(ctx.exception)

    def test_pick_01_unauthorized_names_the_device_and_the_fix(self):
        msg = self.error("unauthorized")
        self.assertIn("R58M00TEST01 is unauthorized", msg)
        self.assertIn("Allow USB debugging", msg)
        self.assertIn("./tnazar doctor", msg)
        self.assertNotIn("adb devices is empty", msg)  # the false message this replaces

    def test_pick_02_nothing_listed_keeps_its_message_and_adds_the_hint(self):
        msg = self.error("empty")
        self.assertIn("no device connected (adb devices is empty)", msg)
        self.assertIn("./tnazar doctor", msg)

    def test_pick_03_every_device_that_is_not_ready_is_named(self):
        text = ("List of devices attached\nR58M00TEST01\toffline usb:1-1\n"
                "R58M00TEST02\tunauthorized usb:1-2\n")
        msg = self.error(text)
        self.assertIn("R58M00TEST01 is offline", msg)
        self.assertIn("R58M00TEST02 is unauthorized", msg)
        self.assertIn(adbmod.STATE_FIX["offline"], msg)
        self.assertIn(adbmod.STATE_FIX["unauthorized"], msg)

    def test_pick_04_pick_serial_and_doctor_give_the_same_fix(self):
        from teesri_nazar import doctor
        msg = self.error("unauthorized")
        checks = doctor.run_checks(find_adb=lambda: "/fake/adb", adb_version=lambda p: "35",
                                   devices=lambda: adbmod.parse_devices(fixture("unauthorized")), environ={})
        [c] = [c for c in checks if c.name == "device R58M00TEST01"]
        self.assertIn(c.fix[0], msg)

    def test_pick_06_an_unknown_state_still_gets_a_fix(self):
        msg = self.error("List of devices attached\nR58M00TEST01\twhatever usb:1-1\n")
        self.assertIn("R58M00TEST01 is whatever", msg)
        self.assertIn(adbmod.state_fix("whatever"), msg)

    def test_pick_05_through_the_cli_start_exits_2_with_the_reason(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"TNAZAR_HOME": home}), \
                adb_says(fixture("unauthorized")), no_android_serial():
            code, out, err = tnazar("start", "demo")
        self.assertEqual(code, 2)
        self.assertIn("R58M00TEST01 is unauthorized", err)
        self.assertIn("./tnazar doctor", err)


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # The server APKs a test puts there, never the ones on the machine running it.
        patcher = mock.patch.dict(os.environ, {"APPIUM_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.adbs = {}

    def adb_for(self, serial):
        if serial not in self.adbs:
            self.adbs[serial] = FakeAdb(login_app())
            self.adbs[serial].serial = serial
        return self.adbs[serial]

    def run_checks(self, devices="ready", python=(3, 9, 6), find_adb=lambda: "/sdk/platform-tools/adb",
                   adb_version=lambda path: "35.0.2", only=None, environ=None):
        from teesri_nazar import doctor
        list_devices = devices if callable(devices) else (lambda: adbmod.parse_devices(fixture(devices)))
        return doctor.run_checks(python=python, executable="/usr/bin/python3", find_adb=find_adb,
                                 adb_version=adb_version, devices=list_devices, adb_for=self.adb_for,
                                 only=only, environ=environ or {})

    def get(self, checks, name):
        found = [c for c in checks if c.name == name]
        self.assertEqual(len(found), 1, f"{name!r} in {[c.name for c in checks]}")
        return found[0]

    def verdict(self, checks):
        from teesri_nazar import doctor
        return doctor.verdict(checks)


class PythonAndAdbTest(DoctorTestCase):
    def test_py_01_supported_python_shows_version_and_interpreter(self):
        c = self.get(self.run_checks(), "python")
        self.assertEqual(c.status, "ok")
        self.assertIn("3.9.6", c.detail)
        self.assertIn("/usr/bin/python3", c.detail)

    def test_py_02_old_python_is_a_blocker_with_a_fix(self):
        checks = self.run_checks(python=(3, 8, 10))
        c = self.get(checks, "python")
        self.assertEqual(c.status, "fail")
        self.assertIn("3.9", " ".join(c.fix))
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_adb_01_missing_adb_blocks_and_skips_the_device_checks(self):
        def missing():
            raise adbmod.AdbError("adb not found: install platform-tools or set ANDROID_HOME")
        listed = mock.Mock(side_effect=AssertionError("devices must not be listed without adb"))
        checks = self.run_checks(find_adb=missing, devices=listed)
        c = self.get(checks, "adb")
        self.assertEqual(c.status, "fail")
        self.assertIn("platform-tools", " ".join(c.fix))
        self.assertEqual(self.get(checks, "devices").status, "skip")
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_adb_05_missing_adb_names_the_sdk_variable_that_has_none(self):
        def missing():
            raise adbmod.AdbError("adb not found: install platform-tools or set ANDROID_HOME")
        c = self.get(self.run_checks(find_adb=missing, environ={"ANDROID_SDK_ROOT": "/old/sdk"}), "adb")
        self.assertEqual(c.status, "fail")
        self.assertIn("ANDROID_SDK_ROOT=/old/sdk has no adb", " ".join(c.fix))

    def test_adb_02_android_home_without_adb_is_noted_when_path_has_one(self):
        c = self.get(self.run_checks(environ={"ANDROID_HOME": "/nowhere"}), "adb")
        self.assertEqual(c.status, "ok")
        self.assertIn("ANDROID_HOME", c.detail)
        self.assertIn("/sdk/platform-tools/adb", c.detail)
        self.assertIn("35.0.2", c.detail)

    def test_adb_03_adb_that_will_not_run_is_a_blocker(self):
        def broken(path):
            raise OSError(13, "Permission denied")
        checks = self.run_checks(adb_version=broken)
        c = self.get(checks, "adb")
        self.assertEqual(c.status, "fail")
        self.assertIn("/sdk/platform-tools/adb", c.detail)
        self.assertTrue(c.fix)
        self.assertEqual(self.get(checks, "devices").status, "skip")

    def test_adb_04_adb_that_hangs_is_a_blocker_with_restart_fix(self):
        def hangs(path):
            raise adbmod.AdbError("adb version did not answer within 5s")
        c = self.get(self.run_checks(adb_version=hangs), "adb")
        self.assertEqual(c.status, "fail")
        self.assertIn("adb kill-server", " ".join(c.fix))


class DevicesTest(DoctorTestCase):
    def test_dev_01_no_device(self):
        checks = self.run_checks("empty")
        c = self.get(checks, "devices")
        self.assertEqual(c.status, "fail")
        self.assertIn("no device connected", c.detail)
        fix = " ".join(c.fix)
        self.assertIn("emulator", fix)
        self.assertIn("USB debugging", fix)
        self.assertIn("cable that carries data", fix)
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_dev_02_to_05_a_device_that_is_not_ready_gets_its_state_fix(self):
        for name, state in (("unauthorized", "unauthorized"), ("offline", "offline"),
                            ("no_permissions", "no permissions"), ("recovery", "recovery")):
            with self.subTest(state=state):
                checks = self.run_checks(name)
                c = self.get(checks, "device R58M00TEST01")
                self.assertEqual(c.status, "fail")
                self.assertIn(state, c.detail)
                self.assertEqual(c.fix, [adbmod.state_fix(state)])
                self.assertEqual(self.verdict(checks)[0], 2)

    def test_dev_06_a_blocked_phone_beside_a_ready_emulator_does_not_block(self):
        checks = self.run_checks("mixed")
        self.assertEqual(self.get(checks, "device R58M00TEST01").status, "fail")
        self.assertEqual(self.get(checks, "device emulator-5554").status, "ok")
        code, line = self.verdict(checks)
        self.assertEqual(code, 0)
        self.assertIn("emulator-5554", line)
        self.assertNotIn("R58M00TEST01", line)

    def test_dev_07_several_ready_devices_are_all_checked_and_a_choice_is_asked(self):
        checks = self.run_checks("two_ready")
        for serial in ("emulator-5554", "emulator-5556"):
            self.assertEqual(self.get(checks, f"device {serial}").status, "ok")
            self.assertEqual(self.get(checks, f"{serial} screen").status, "ok")
        c = self.get(checks, "devices")
        self.assertEqual(c.status, "warn")
        self.assertIn("--device", " ".join(c.fix))
        self.assertIn("ANDROID_SERIAL", " ".join(c.fix))
        self.assertEqual(self.verdict(checks)[0], 0)

    def test_dev_08_only_narrows_to_one_device(self):
        checks = self.run_checks("two_ready", only="emulator-5556")
        names = [c.name for c in checks]
        self.assertIn("device emulator-5556", names)
        self.assertFalse([n for n in names if "emulator-5554" in n])
        self.assertNotIn("devices", names)  # no "pick one" warning
        self.assertNotIn("emulator-5554", self.adbs)  # never touched

    def test_dev_09_only_a_serial_adb_does_not_list_is_a_blocker(self):
        checks = self.run_checks("ready", only="NOPE")
        c = self.get(checks, "device NOPE")
        self.assertEqual(c.status, "fail")
        self.assertIn("not listed", c.detail)
        fix = " ".join(c.fix)
        # The usual reasons a plugged-in phone is missing: USB debugging off, a charge-only cable, a USB mode without data.
        self.assertIn("USB debugging", fix)
        self.assertIn("File transfer", fix)
        self.assertIn("cable that carries data", fix)
        self.assertIn("emulator-5554", fix)  # the serials adb does list, for a typo
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_dev_12_a_device_still_booting_is_not_ready(self):
        self.adb_for("emulator-5554").booting = True
        checks = self.run_checks()
        c = self.get(checks, "device emulator-5554")
        self.assertEqual(c.status, "fail")
        self.assertIn("still booting", c.detail)
        self.assertIn("finish booting", " ".join(c.fix))
        self.assertNotIn("emulator-5554 screen reads", [c.name for c in checks])  # no false "slow reads"
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_dev_13_a_device_that_does_not_answer_is_not_ready(self):
        adb = self.adb_for("emulator-5554")
        adb.shell = mock.Mock(side_effect=adbmod.AdbError("adb timed out after 10s: shell getprop"))
        checks = self.run_checks()
        c = self.get(checks, "device emulator-5554")
        self.assertEqual(c.status, "fail")
        self.assertIn("timed out", c.detail)
        self.assertIn("adb kill-server", " ".join(c.fix))
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_dev_14_every_device_question_is_time_limited(self):
        from teesri_nazar import doctor
        adb = self.adb_for("emulator-5554")
        seen = []
        real_shell, real_size = adb.shell, adb.screen_size
        adb.shell = lambda *a, timeout=60, **kw: seen.append(("shell", a[0], timeout)) or real_shell(*a, **kw)
        adb.screen_size = lambda timeout=60: seen.append(("size", timeout)) or real_size()
        self.run_checks()
        self.assertEqual(len(seen), 3, seen)
        self.assertTrue(all(t[-1] <= doctor.DEVICE_TIMEOUT for t in seen), seen)

    def test_dev_15_adb_vanishing_mid_check_is_that_devices_blocker(self):
        adb = self.adb_for("emulator-5554")
        adb.shell = mock.Mock(side_effect=FileNotFoundError(2, "No such file or directory"))
        checks = self.run_checks()
        self.assertEqual(self.get(checks, "device emulator-5554").status, "fail")
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_dev_16_readiness_does_not_depend_on_the_label(self):
        from teesri_nazar import doctor
        checks = self.run_checks()
        for c in checks:
            c.name = c.name.upper()
        self.assertEqual(doctor.usable(checks), ["emulator-5554"])

    def test_dev_11_adb_devices_that_hangs_is_a_blocker(self):
        def hangs():
            raise adbmod.AdbError("adb devices did not answer within 10s")
        checks = self.run_checks(devices=hangs)
        c = self.get(checks, "devices")
        self.assertEqual(c.status, "fail")
        self.assertIn("adb kill-server", " ".join(c.fix))
        self.assertEqual(self.verdict(checks)[0], 2)


class VerdictTest(unittest.TestCase):
    """OUT-05: the exit code and last line for each mix of blockers, warnings and usable devices."""

    def test_out_05_verdict_table(self):
        from teesri_nazar.doctor import Check, verdict
        ready = Check("ok", "device d1", "ready", device="d1", kind="device")
        ready2 = Check("ok", "device d2", "ready", device="d2", kind="device")
        slow = Check("warn", "d1 screen reads", "slow", device="d1")
        bad_d2 = Check("fail", "d2 screen", "unreadable", device="d2")
        old_py = Check("fail", "python", "3.8")
        cases = [
            ([ready], (0, "all good: runs can start on d1")),
            ([ready, slow], (0, "1 warning: runs can start on d1")),
            ([ready, ready2, bad_d2], (0, "1 blocker: runs can start on d1 only")),
            ([ready, ready2, bad_d2, slow], (0, "1 blocker, 1 warning: runs can start on d1 only")),
            ([ready, old_py], (2, "1 blocker: no run can start until the blockers above are fixed")),
            ([ready2, bad_d2], (2, "1 blocker: no run can start until the blockers above are fixed")),
            ([], (2, "no run can start: no device was checked")),
        ]
        for checks, expected in cases:
            with self.subTest(checks=[c.name for c in checks]):
                self.assertEqual(verdict(checks), expected)


class ScreenAndReadsTest(DoctorTestCase):
    def test_size_01_screen_size_is_shown(self):
        c = self.get(self.run_checks(), "emulator-5554 screen")
        self.assertEqual((c.status, c.detail), ("ok", "1080x2400"))

    def test_size_02_override_size_wins_like_every_other_command(self):
        class WmAdb(adbmod.Adb):
            def __init__(self):
                self.serial = "emulator-5554"

            def shell(self, *args, **kw):
                if args[:1] == ("getprop",):
                    return "1\n"
                return "Physical size: 1080x2400\nOverride size: 720x1600\n"
        self.adbs["emulator-5554"] = WmAdb()
        self.assertEqual(self.get(self.run_checks(), "emulator-5554 screen").detail, "720x1600")

    def test_size_03_unreadable_size_makes_that_device_unusable(self):
        adb = self.adb_for("emulator-5554")
        adb.screen_size = mock.Mock(side_effect=adbmod.AdbError("could not read screen size: ''"))
        checks = self.run_checks()
        c = self.get(checks, "emulator-5554 screen")
        self.assertEqual(c.status, "fail")
        self.assertIn("wm size", " ".join(c.fix))
        self.assertEqual(self.verdict(checks)[0], 2)

    def test_size_04_unreadable_size_on_one_device_leaves_the_other_usable(self):
        self.adb_for("emulator-5554").screen_size = mock.Mock(side_effect=adbmod.AdbError("no size"))
        code, line = self.verdict(self.run_checks("two_ready"))
        self.assertEqual(code, 0)
        self.assertIn("emulator-5556", line)
        self.assertNotIn("emulator-5554", line)

    def test_uia2_01_installed_server_means_fast_reads(self):
        self.adb_for("emulator-5554").uia2_installed = True
        c = self.get(self.run_checks(), "emulator-5554 screen reads")
        self.assertEqual(c.status, "ok")
        self.assertIn("fast", c.detail)

    def test_uia2_02_missing_server_warns_with_the_same_commands_start_prints(self):
        base = put_apks()
        checks = self.run_checks()
        c = self.get(checks, "emulator-5554 screen reads")
        self.assertEqual(c.status, "warn")
        self.assertIn("~2 s per read", c.detail)
        cmds = uia2.install_hint(self.adb_for("emulator-5554"))
        self.assertEqual(cmds, [
            f"adb -s emulator-5554 install -r -g {base / 'appium-uiautomator2-server-v9.11.1.apk'}",
            f"adb -s emulator-5554 install -r -g {base / 'appium-uiautomator2-server-debug-androidTest.apk'}"])
        self.assertEqual(c.fix[1:], cmds)
        self.assertEqual(self.verdict(checks), (0, "1 warning: runs can start on emulator-5554"))

    def test_uia2_03_no_local_apks_says_how_to_get_them(self):
        c = self.get(self.run_checks(), "emulator-5554 screen reads")
        self.assertEqual(c.status, "warn")
        self.assertIn("appium driver install uiautomator2", " ".join(c.fix))
        self.assertEqual(uia2.install_hint(self.adb_for("emulator-5554")), [])


class DoctorCliTest(unittest.TestCase):
    """`tnazar doctor` end to end against the fake device."""

    def setUp(self):
        self.adb = FakeAdb(login_app())
        ctx = fake_device(self.adb)
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        from teesri_nazar import cli
        self.cli = cli
        for name, value in (("adb_binary", lambda: "/sdk/platform-tools/adb"),
                            ("adb_version", lambda path: "35.0.2")):
            p = mock.patch.object(cli, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ANDROID_SERIAL", None)

    def listing(self, name):
        listed = adbmod.parse_devices(fixture(name))
        # fake_device answers every serial with the one FakeAdb: give it the serial being checked.
        self.adb.serial = next((d.serial for d in listed if d.state == "device"), self.adb.serial)
        p = mock.patch.object(self.cli, "list_devices", lambda: listed)
        p.start()
        self.addCleanup(p.stop)

    def doctor(self, *argv):
        code, out, err = tnazar("doctor", *argv)
        self.assertEqual(err, "")
        return code, out

    def test_ready_device_exits_0_and_warns_about_slow_reads(self):
        code, out = self.doctor()
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("OK    python: "))
        self.assertIn("OK    adb: /sdk/platform-tools/adb (35.0.2)", lines)
        self.assertIn("OK    device fake-1: ready (FakePhone)", lines)
        self.assertIn("OK    fake-1 screen: 1080x2400", lines)
        self.assertIn("WARN  fake-1 screen reads: slow, uiautomator dump (~2 s per read)", lines)
        self.assertEqual(lines[-1], "1 warning: runs can start on fake-1")

    def test_all_ok_with_the_server(self):
        self.adb.uia2_installed = True
        code, out = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "all good: runs can start on fake-1")
        self.assertNotIn("WARN", out)
        self.assertNotIn("fix:", out)

    def test_out_01_status_column_and_indented_fixes(self):
        self.listing("mixed")
        put_apks()
        code, out = self.doctor()
        lines = out.splitlines()
        for line in lines[:-1]:
            with self.subTest(line=line):
                self.assertRegex(line, r"^(OK    |WARN  |FAIL  |SKIP  )\S|^        fix: \S|^          \S")
        i = lines.index("FAIL  device R58M00TEST01: unauthorized")
        self.assertTrue(lines[i + 1].startswith("        fix: accept the \"Allow USB debugging\" prompt"))
        j = lines.index("WARN  emulator-5554 screen reads: slow, uiautomator dump (~2 s per read)")
        self.assertEqual(lines[j + 1], "        fix: install the UiAutomator2 server once for ~0.05 s reads:")
        self.assertTrue(lines[j + 2].startswith("          adb -s emulator-5554 install -r -g "))
        self.assertEqual(lines[-1], "1 blocker, 1 warning: runs can start on emulator-5554 only")
        self.assertEqual(code, 0)

    def test_out_03_done_when_1_no_device_exits_2(self):
        self.listing("empty")
        code, out = self.doctor()
        self.assertEqual(code, 2)
        self.assertIn("FAIL  devices: no device connected (adb devices is empty)", out)
        self.assertEqual(out.splitlines()[-1], "1 blocker: no run can start until the blockers above are fixed")

    def test_out_03_done_when_2_unauthorized_exits_2(self):
        self.listing("unauthorized")
        code, out = self.doctor()
        self.assertEqual(code, 2)
        self.assertIn("FAIL  device R58M00TEST01: unauthorized", out)
        self.assertIn("Allow USB debugging", out)

    def test_out_04_no_colour_codes(self):
        self.listing("mixed")
        self.assertNotIn("\x1b", self.doctor()[1])

    def test_out_06_help_lists_device(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            self.cli.main(["doctor", "--help"])
        self.assertIn("--device", buf.getvalue())

    def test_dev_08_device_flag_before_or_after_the_command(self):
        self.listing("two_ready")
        for argv in (("--device", "emulator-5556", "doctor"), ("doctor", "--device", "emulator-5556"),
                     ("doctor", "-s", "emulator-5556")):
            with self.subTest(argv=argv):
                code, out, _ = tnazar(*argv)
                self.assertEqual(code, 0)
                self.assertIn("device emulator-5556: ready", out)
                self.assertNotIn("emulator-5554", out)

    def test_dev_10_android_serial_narrows_like_device(self):
        self.listing("two_ready")
        os.environ["ANDROID_SERIAL"] = "emulator-5554"
        code, out = self.doctor()
        self.assertEqual(code, 0)
        self.assertNotIn("emulator-5556", out)

    def test_safe_01_only_reads_the_device(self):
        put_apks()
        self.doctor()
        self.assertTrue(self.adb.calls)
        for call in self.adb.calls:
            with self.subTest(call=call):
                self.assertEqual(call[0], "shell")
                self.assertIn(call[1:3], (["getprop", "sys.boot_completed"], ["wm", "size"], ["pm", "list"]))

    def test_safe_02_safe_while_a_run_is_in_progress_and_even_when_paused(self):
        from teesri_nazar import live
        from teesri_nazar.session import get_active
        self.adb.uia2_installed = True
        self.assertEqual(tnazar("start", "demo", "--package", PACKAGE)[0], 0)
        before_run = get_active()[0].name
        state = uia2._state_file().read_text()
        live.set_paused(True)
        code, out = self.doctor()
        self.assertEqual(code, 0)
        self.assertNotIn("paused", out)
        self.assertEqual(get_active()[0].name, before_run)
        self.assertEqual(uia2._state_file().read_text(), state)
        live.set_paused(False)
        self.assertEqual(tnazar("observe")[0], 0)

    def test_safe_03_writes_nothing(self):
        def files():  # cli.main may create the empty state folder, as for every command
            return sorted(str(p) for p in Path(self.home).rglob("*") if p.is_file())
        before = files()
        self.doctor()
        self.assertEqual(files(), before)

    def test_perf_01_adb_calls_per_device_are_pinned(self):
        self.doctor()
        self.assertLessEqual(len(self.adb.calls), 3, self.adb.calls)


LAUNCHER = Path(__file__).resolve().parent.parent / "tnazar"


class LauncherTest(unittest.TestCase):
    """PY-03/04: the `./tnazar` launcher stops old Python before importing anything."""

    def launch(self, version):
        import runpy
        import sys
        from teesri_nazar import cli
        err = io.StringIO()
        main = mock.Mock(return_value=0)
        with mock.patch.object(sys, "version_info", version), mock.patch.object(cli, "main", main), \
                mock.patch.object(sys, "argv", ["tnazar", "doctor"]), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as ctx:
            runpy.run_path(str(LAUNCHER), run_name="__main__")
        return ctx.exception.code, err.getvalue(), main

    def test_py_03_old_python_prints_the_fix_and_exits_2_without_importing(self):
        code, err, main = self.launch((3, 8, 0, "final", 0))
        self.assertEqual(code, 2)
        self.assertIn("tnazar needs Python 3.9+; this is 3.8.0 at ", err)
        self.assertIn("doctor", err)
        main.assert_not_called()

    def test_py_03b_supported_python_runs_the_cli(self):
        code, err, main = self.launch((3, 9, 6, "final", 0))
        self.assertEqual((code, err), (0, ""))
        main.assert_called_once()

    def test_py_04_launcher_parses_on_any_python_3(self):
        import ast
        tree = ast.parse(LAUNCHER.read_text())
        newer = (ast.JoinedStr, ast.AnnAssign, ast.NamedExpr, ast.AsyncFunctionDef)
        self.assertFalse([type(n).__name__ for n in ast.walk(tree) if isinstance(n, newer)])
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and
                          (n.returns or any(a.annotation for a in n.args.args))])


class DocsTest(unittest.TestCase):
    """DOC-01..03: the agent and new users are sent to doctor."""

    def test_doc_agent_playbook_skill_readme_and_architecture_mention_doctor(self):
        root = LAUNCHER.parent
        for rel in ("CLAUDE.md", ".claude/skills/test-feature/SKILL.md", "README.md", "ARCHITECTURE.md"):
            with self.subTest(file=rel):
                self.assertIn("./tnazar doctor", (root / rel).read_text())
        exit_codes = (root / "ARCHITECTURE.md").read_text().split("### Exit codes", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`doctor`", exit_codes)


if __name__ == "__main__":
    unittest.main()
