"""One run per device: a run driven by a live process is not taken over by another `start`/`run`."""

import json
import os
import subprocess
import sys
from unittest import mock

from fakedevice import PACKAGE, tnazar
from teesri_nazar import cli
from teesri_nazar.session import Run, get_active, state_dir

from test_plan import LOGIN, PlanRunBase

PLAN = LOGIN + '''
case TC-01 "Login"
  run login
  check "Pay now"
'''


class OwnershipTest(PlanRunBase):
    def other_process(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    def hand_to(self, pid, started=None):
        """Make the active run look owned by another process."""
        f = state_dir() / "active.json"
        data = json.loads(f.read_text())
        data["owner"] = {"pid": pid, "started": started if started is not None else _started(pid)}
        f.write_text(json.dumps(data))

    def test_start_records_the_owning_process(self):
        self.start()
        owner = json.loads((state_dir() / "active.json").read_text())["owner"]
        self.assertEqual(owner["pid"], os.getpid())
        self.assertEqual(owner["started"], _started(os.getpid()))

    def test_start_refuses_while_another_live_process_drives_the_run(self):
        run = self.start()
        other = self.other_process()
        self.hand_to(other.pid)
        code, out, err = tnazar("start", "second", "--package", PACKAGE)
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn(f"a run is in progress on this device: demo (pid {other.pid}); stop it or wait", err)
        self.assertEqual(get_active()[0].name, "demo")
        self.assertNotIn("finished_at", run.meta())

    def test_a_run_whose_owner_is_gone_is_finished_quietly(self):
        run = self.start()
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.hand_to(gone.pid, started="")
        self.ok("start", "second", "--package", PACKAGE)
        self.assertEqual(get_active()[0].name, "second")
        self.assertIn("finished_at", Run(run.path).meta())

    def test_a_reused_pid_is_not_the_owner(self):
        self.start()
        other = self.other_process()
        self.hand_to(other.pid, started="Thu Jan  1 00:00:00 1970")
        self.ok("start", "second", "--package", PACKAGE)
        self.assertEqual(get_active()[0].name, "second")

    def test_stop_still_ends_a_run_another_process_owns(self):
        self.start()
        self.hand_to(self.other_process().pid)
        self.ok("stop")
        self.assertIsNone(get_active())

    def test_run_refuses_to_take_over_a_live_run(self):
        self.start()
        other = self.other_process()
        self.hand_to(other.pid)
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(PLAN)
        code, out, err = tnazar("run", path)
        self.assertEqual(code, cli.EXIT_ERROR, out + err)
        self.assertIn(f"a run is in progress on this device: demo (pid {other.pid})", err)
        self.assertEqual(get_active()[0].name, "demo")

    def test_resuming_a_plan_run_refuses_while_its_old_owner_lives(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(PLAN)
        self.start()
        get_active()[0].update_meta(plan=os.path.realpath(path))
        other = self.other_process()
        self.hand_to(other.pid)
        code, out, err = tnazar("run", path, "--from", "TC-01")
        self.assertEqual(code, cli.EXIT_ERROR, out + err)
        self.assertIn("a run is in progress on this device", err)

    def test_resuming_a_plan_run_takes_ownership(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(PLAN)
        self.start()
        get_active()[0].update_meta(plan=os.path.realpath(path))
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.hand_to(gone.pid, started="")
        self.run_plan('case TC-01 "Login"\n  run login\n  check "Pay now"\n')


def _started(pid):
    from teesri_nazar.session import process_started
    return process_started(pid)


class DeviceOwnerTest(PlanRunBase):
    """One device, many homes (teams, scripts, machines): the device itself says who drives it.
    Found when a smoke script in its own TNAZAR_HOME started while another run was live."""

    def other_home(self):
        other = os.path.join(self.home, "other-team")
        os.makedirs(other, exist_ok=True)
        return mock.patch.dict(os.environ, {"TNAZAR_HOME": other})

    def test_a_second_home_cannot_start_on_a_device_another_home_is_driving(self):
        self.start()
        with self.other_home():
            code, out, err = tnazar("start", "theirs", "--package", PACKAGE)
        self.assertEqual(code, 2, out + err)
        self.assertIn("is in use by run", err)
        self.assertIn(os.path.realpath(self.home), err)

    def test_after_stop_another_home_may_start(self):
        self.start()
        self.assertEqual(tnazar("stop")[0], 0)
        with self.other_home():
            code, out, err = tnazar("start", "theirs", "--package", PACKAGE)
        self.assertEqual(code, 0, out + err)

    def test_a_run_whose_home_stopped_tracking_it_is_taken_over_with_a_note(self):
        self.start()
        (state_dir() / "active.json").unlink()  # crashed, or its folder was wiped: not running any more
        with self.other_home():
            code, out, err = tnazar("start", "theirs", "--package", PACKAGE)
        self.assertEqual(code, 0, out + err)
        self.assertIn("took over the device from run", out)

    def test_take_over_is_explicit(self):
        self.start()
        with self.other_home():
            code, out, err = tnazar("start", "theirs", "--package", PACKAGE, "--take-over")
        self.assertEqual(code, 0, out + err)

    def test_the_same_home_still_replaces_its_own_unstopped_run(self):
        self.start()
        code, out, err = tnazar("start", "again", "--package", PACKAGE)
        self.assertEqual(code, 0, out + err)


class DeviceOwnerHostTest(PlanRunBase):
    def test_the_same_home_path_on_another_machine_is_another_owner(self):
        import time
        from teesri_nazar import device
        device.claim_device(self.adb, {"host": "ci-runner-7", "home": os.path.realpath(self.home),
                                       "run": "theirs", "t": int(time.time())})
        code, out, err = tnazar("start", "mine", "--package", PACKAGE)
        self.assertEqual(code, 2, out + err)
        self.assertIn("is in use by run theirs", err)

    def test_an_owner_whose_state_cannot_be_read_is_treated_as_running(self):
        self.start()
        other = os.path.join(self.home, "other-user")
        os.makedirs(other)
        with mock.patch.dict(os.environ, {"TNAZAR_HOME": other}), \
                mock.patch("pathlib.Path.read_text", side_effect=PermissionError("not yours")):
            from teesri_nazar import cli
            self.assertTrue(cli._still_running({"host": cli.socket.gethostname(), "home": self.home,
                                                "run": "demo", "t": "0"}, "fake-1"))
