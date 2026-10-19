"""The app map: `tnazar flow <name>` runs a saved route against the active run; `tnazar flows` lists them."""

import os
import unittest
from unittest import mock

from fakedevice import PACKAGE, tnazar
from teesri_nazar import live, plan, runner
from teesri_nazar.session import get_active

from test_cli import CliTestCase

MAP = f"""
app {PACKAGE}

flow login
  type "9876543210" --into id=phone_input
  tap Login
  wait "Pay now" --timeout 5
end

flow logout
  if "Pay now"
    key back
  end
  check Login
end

flow broken
  tap "Nowhere" --wait 1
  tap Login
end
"""


class MapFileTest(unittest.TestCase):
    def test_a_file_of_flows_only_is_a_valid_app_map(self):
        flows = plan.parse(MAP, "plans/app.plan", flows_only=True).flows
        self.assertEqual(list(flows), ["login", "logout", "broken"])

    def test_a_plan_still_needs_a_case(self):
        with self.assertRaises(plan.PlanError):
            plan.parse(MAP, "plans/app.plan")


class FlowBase(CliTestCase):
    def write(self, name: str, text: str = MAP) -> str:
        os.makedirs(os.path.join(self.home, "plans"), exist_ok=True)
        path = os.path.join(self.home, "plans", name)
        with open(path, "w") as f:
            f.write(text)
        return path


class FlowTest(FlowBase):
    def test_runs_a_saved_route_in_one_command_and_logs_each_step(self):
        self.write("app.plan")
        run = self.start()
        code, out, err = tnazar("flow", "login")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.adb.current, "home")
        self.assertIn("flow login", out)
        types = [e["type"] for e in run.events()]
        self.assertIn("say", types)  # the timeline heading
        self.assertLess(types.index("say"), types.index("type"))
        self.assertIn("tap", types)
        self.assertIsNotNone(get_active())  # the run stays open: the agent carries on from here

    def test_a_failed_step_stops_the_flow_with_its_exit_code_and_says_where(self):
        self.write("app.plan")
        self.start()
        code, out, err = tnazar("flow", "broken")
        self.assertEqual(code, 1, out + err)
        self.assertIn("line", out)
        self.assertIn("Nowhere", out)
        self.assertEqual(self.adb.current, "login")  # `tap Login` after the failure did not run

    def test_a_failed_action_does_not_fail_the_case_by_itself(self):
        self.write("app.plan")
        run = self.start()
        self.ok("case", "start", "TC-01", "x")
        tnazar("flow", "broken")
        self.assertFalse([e for e in run.events() if e["type"] == "check"])

    def test_a_failed_check_in_the_flow_exits_1(self):
        self.write("app.plan")
        self.start()
        self.ok("flow", "login")
        self.adb.app.on_key = {}  # Back no longer leaves Home, so `check Login` fails
        code, out, err = tnazar("flow", "logout")
        self.assertEqual(code, 1, out + err)

    def test_a_check_in_the_flow_is_recorded_in_the_current_case(self):
        self.write("app.plan")
        run = self.start()
        self.ok("case", "start", "TC-01", "x")
        self.ok("flow", "logout")
        checks = [e for e in run.events() if e["type"] == "check"]
        self.assertEqual([(c["case"], c["ok"]) for c in checks], [("TC-01", True)])

    def test_needs_an_active_run(self):
        self.write("app.plan")
        code, _, err = tnazar("flow", "login")
        self.assertEqual(code, 2)
        self.assertIn("start", err)
        self.assertEqual(self.adb.current, "login")

    def test_an_unknown_flow_names_the_ones_there_are(self):
        self.write("app.plan")
        self.start()
        code, _, err = tnazar("flow", "settings")
        self.assertEqual(code, 2)
        self.assertIn("login", err)

    def test_a_plan_file_can_be_named(self):
        path = self.write("elsewhere.plan")
        os.rename(path, os.path.join(self.home, "other.plan"))
        self.start()
        self.ok("flow", "login", "--plan", os.path.join(self.home, "other.plan"))
        self.assertEqual(self.adb.current, "home")

    def test_flows_of_another_app_are_ignored(self):
        self.write("app.plan")
        self.write("other.plan", MAP.replace(PACKAGE, "com.example.other"))
        self.start()
        self.ok("flow", "login")  # not ambiguous: only one map is for the run's app

    def test_the_same_flow_in_two_maps_of_the_app_is_ambiguous(self):
        self.write("a.plan")
        self.write("b.plan")
        self.start()
        code, _, err = tnazar("flow", "login")
        self.assertEqual(code, 2)
        self.assertIn("--plan", err)
        self.assertEqual(self.adb.current, "login")

    def test_a_flow_from_a_full_plan_works_too(self):
        self.write("smoke.plan", MAP + '\ncase TC-01 "x"\n  run login\n  check "Pay now"\n')
        self.start()
        self.ok("flow", "login")
        self.assertEqual(self.adb.current, "home")

    def test_a_broken_file_does_not_hide_the_others(self):
        self.write("app.plan")
        self.write("bad.plan", "app x.y\nflow f\n  teleport\nend\n")
        self.start()
        code, out, err = tnazar("flow", "login")
        self.assertEqual(code, 0, out + err)
        self.assertIn("bad.plan", err)

    def test_a_dashboard_message_mid_flow_hands_back_with_exit_3(self):
        self.write("app.plan")
        self.start()
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main

        def main(argv):
            if argv[:1] == ["tap"]:
                live.post("stop, open settings instead")
            return real_main(argv)
        with mock.patch("teesri_nazar.cli.main", main):
            code, out, _ = tnazar("flow", "login")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertIn("open settings instead", out)
        self.assertEqual(self.adb.current, "login")  # the tap was held back
        self.assertIsNotNone(get_active())

    def test_a_message_before_the_flow_holds_it_like_any_device_command(self):
        self.write("app.plan")
        self.start()
        live.post("wait")
        code, out, _ = tnazar("flow", "login")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertEqual(self.adb.current, "login")
        self.assertNotIn("type", [e["type"] for e in get_active()[0].events()])

    def test_a_long_pause_mid_flow_gives_the_turn_back_with_exit_5(self):
        from fakedevice import FakeClock
        self.write("app.plan")
        self.start()
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main

        def main(argv):
            if argv[:1] == ["tap"]:
                live.set_paused(True)
            return real_main(argv)
        with mock.patch.object(live, "time", FakeClock()), mock.patch("teesri_nazar.cli.main", main):
            code, out, _ = tnazar("flow", "login")
        self.assertEqual(code, live.EXIT_PAUSED, out)
        self.assertIn("wait-resume", out)
        self.assertEqual(self.adb.current, "login")

    def test_flows_is_not_a_plan_step(self):
        with self.assertRaises(plan.PlanError):
            plan.parse(f"app {PACKAGE}\ncase T1\n  flows\n  check X\n")


class FlowsTest(FlowBase):
    def test_lists_the_flows_of_every_map_with_where_they_are(self):
        self.write("app.plan")
        out = self.ok("flows")
        for name in ("login", "logout", "broken"):
            self.assertIn(name, out)
        self.assertIn("app.plan", out)

    def test_lists_only_the_active_apps_flows(self):
        self.write("app.plan")
        self.write("other.plan", MAP.replace("flow login", "flow other_login").replace(PACKAGE, "com.example.other"))
        self.start()
        out = self.ok("flows")
        self.assertIn("login", out)
        self.assertNotIn("other_login", out)

    def test_no_maps_says_how_to_make_one(self):
        out = self.ok("flows")
        self.assertIn("plans/", out)


if __name__ == "__main__":
    unittest.main()


class ListenerTest(FlowBase):
    def test_a_message_the_background_listener_delivered_still_stops_the_flow(self):
        self.write("app.plan")
        run = self.start()
        real = runner.call

        def call(argv):
            result = real(argv)
            if argv[0] == "type":  # what `wait-message` does in the background mid-flow
                live.post("stop and log out")
                live.deliver(run.log)
            return result
        with mock.patch.object(runner, "call", call):
            code, out, _ = tnazar("flow", "login")
        self.assertEqual(code, live.EXIT_HELD)
        self.assertIn("stop and log out", out)
        self.assertEqual([e for e in run.events() if e["type"] == "tap"], [])
