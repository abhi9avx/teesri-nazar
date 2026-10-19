"""`goto`: reach a screen in one command by a route learned in earlier runs; `map` shows what was learned."""

import json
import os
import unittest
from unittest import mock

from fakedevice import PACKAGE, FakeAdb, FakeApp, fake_device, node, tnazar
from teesri_nazar import live
from teesri_nazar.session import get_active

BACK = 4


def bar(title):
    return node(id="toolbar_title", text=title, clickable=False, bounds=(40, 100, 800, 200))


def bank_app() -> FakeApp:
    """Two ways into Passbook (Savings, and Profile > Statements), Loans, a Pay screen, a dialog."""
    nav = [node(id="btn_savings", text="Savings", bounds=(0, 2200, 270, 2400)),
           node(id="btn_loans", text="Loans", bounds=(270, 2200, 540, 2400)),
           node(id="btn_profile", desc="Profile", bounds=(540, 2200, 810, 2400)),
           node(id="btn_pay", text="Pay", bounds=(810, 2200, 1080, 2400))]
    return FakeApp(
        screens={
            "home": [bar("Home"), node(id="balance", text="₹1,000.00", clickable=False, bounds=(40, 300, 800, 380)),
                     node(text="Hi Asha", clickable=False, bounds=(40, 400, 800, 460)), *nav],
            "savings": [bar("Savings"), node(id="btn_passbook", text="Passbook", bounds=(40, 600, 1040, 700)),
                        node(id="btn_fd", text="Fixed deposits", bounds=(40, 720, 1040, 820))],
            "passbook": [bar("Passbook"), node(id="txn_list", cls="androidx.recyclerview.widget.RecyclerView",
                                               clickable=False, scrollable=True, bounds=(0, 300, 1080, 2000)),
                         node(id="txn", text="Coffee ₹120", bounds=(0, 300, 1080, 420))],
            "profile": [bar("Profile"), node(id="btn_statements", text="Statements", bounds=(40, 600, 1040, 700)),
                        node(id="btn_logout", text="Log out", bounds=(40, 720, 1040, 820))],
            "statements": [bar("Statements"), node(id="btn_open_passbook", text="Passbook", bounds=(40, 600, 1040, 700))],
            "loans": [bar("Loans"), node(id="btn_active", text="Active loan", bounds=(40, 600, 1040, 700))],
            "active_loan": [bar("Active loan"), node(id="btn_statement", text="Loan statement", bounds=(40, 600, 1040, 700))],
            "loan_statement": [bar("Loan statement"), node(id="amount", text="₹50,000", clickable=False,
                                                           bounds=(40, 300, 800, 380))],
            "pay": [bar("Pay"), node(id="btn_confirm", text="Confirm", bounds=(40, 600, 1040, 700))],
            "mpin": [bar("Enter MPIN"), node(id="mpin", cls="android.widget.EditText", password=True,
                                             bounds=(40, 600, 1040, 700))],
            "offers": [bar("Offers"), node(id="btn_close", text="Close", bounds=(40, 600, 1040, 700))],
        },
        start="home",
        on_tap={("home", "btn_savings"): "savings", ("home", "btn_loans"): "loans",
                ("home", "btn_profile"): "profile", ("home", "btn_pay"): "pay",
                ("savings", "btn_passbook"): "passbook", ("profile", "btn_statements"): "statements",
                ("statements", "btn_open_passbook"): "passbook", ("loans", "btn_active"): "active_loan",
                ("active_loan", "btn_statement"): "loan_statement", ("pay", "btn_confirm"): "mpin"},
        on_key={(s, BACK): "home" for s in ("savings", "passbook", "profile", "statements", "loans",
                                             "active_loan", "loan_statement", "pay", "offers", "mpin")},
    )


class GotoBase(unittest.TestCase):
    def setUp(self):
        self.adb = FakeAdb(bank_app())
        ctx = fake_device(self.adb)
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.runs = 0

    def t(self, *argv, code=0):
        got, out, err = tnazar(*argv)
        self.assertEqual(got, code, f"tnazar {' '.join(argv)} -> {got}\n{out}{err}")
        return out

    def start(self):
        self.runs += 1
        self.t("start", f"r{self.runs}", "--package", PACKAGE)
        self.adb.current = "home"
        return get_active()[0]

    def stop(self):
        return self.t("stop")

    def explore_passbook(self, phrase="passbook"):
        """What the agent does today: miss, then tap its way there and check it."""
        self.t("goto", phrase, code=2)
        self.t("tap", "Savings")
        self.t("tap", "Passbook")
        self.t("check", "Passbook")

    def reads(self, *argv, code=0):
        before = len(self.adb.calls)
        out = self.t(*argv, code=code)
        return sum(1 for c in self.adb.calls[before:] if "uiautomator dump" in " ".join(c)), out


class LearnAndGoTest(GotoBase):
    def test_a_route_explored_once_runs_in_one_command_next_run(self):
        self.start()
        self.explore_passbook()
        out = self.stop()
        self.assertIn("map: learned", out)
        self.start()
        taps = len(self.adb.taps)
        out = self.t("goto", "open my passbook")
        self.assertEqual(self.adb.current, "passbook")
        self.assertEqual(len(self.adb.taps) - taps, 2)
        self.assertIn("arrived in 2 step(s)", out)
        self.assertIn('text="Passbook"', out)  # the screen it arrived on, so no `observe` is needed

    def test_a_route_learned_earlier_in_the_same_run_works_before_stop(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        self.t("goto", "passbook")
        self.assertEqual(self.adb.current, "passbook")

    def test_a_miss_costs_one_read_and_prints_the_screen_like_observe(self):
        self.start()
        reads, out = self.reads("goto", "passbook", code=2)
        self.assertEqual(reads, 1)
        self.assertIn("not in the map yet", out)
        self.assertIn("screen: com.example.app", out)
        self.assertEqual(self.adb.taps, [])

    def test_a_known_route_costs_one_start_read_plus_the_waits(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        reads, _ = self.reads("goto", "passbook")
        self.assertLessEqual(reads, 1 + 2 * 2)

    def test_already_there_taps_nothing(self):
        self.start()
        self.explore_passbook()
        taps = len(self.adb.taps)
        self.assertIn("already there", self.t("goto", "passbook"))
        self.assertEqual(len(self.adb.taps), taps)

    def test_goto_hops_are_ordinary_tap_events_and_the_outcome_is_logged(self):
        run = self.start()
        self.explore_passbook()
        self.t("key", "back")
        self.t("goto", "passbook")
        evs = run.events()
        nav = [e for e in evs if e["type"] == "nav"]
        self.assertEqual([n["outcome"] for n in nav], ["unknown", "arrived"])
        hops = [e for e in evs if e["type"] == "tap" and e.get("via") == "goto"]
        self.assertEqual([h["selector"] for h in hops], [{"id": "btn_savings"}, {"id": "btn_passbook"}])


class NoDuplicatesTest(GotoBase):
    def test_asking_again_and_again_adds_no_screens_steps_or_names(self):
        self.start()
        self.explore_passbook()
        self.stop()
        for _ in range(3):
            self.start()
            for _ in range(3):
                self.t("goto", "open passbook")
                self.t("key", "back")
            self.stop()
        out = self.t("map", "--edges", "--app", PACKAGE)
        self.assertEqual(out.count("names: passbook"), 1)
        self.assertEqual(out.count('tap "Passbook"'), 1)
        self.assertIn("worked in 4 run(s)", out)

    def test_phrase_variants_are_one_name(self):
        self.start()
        self.explore_passbook("Open my Passbook")
        self.stop()
        self.start()
        for phrase in ("passbook", "PASSBOOK", "go to the passbook"):
            self.t("goto", phrase)
            self.t("key", "back")


class WrongNamesTest(GotoBase):
    def test_a_misspelt_phrase_suggests_the_known_name_and_never_guesses(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        taps = len(self.adb.taps)
        out = self.t("goto", "pasbook", code=2)
        self.assertIn("Did you mean: passbook?", out)
        self.assertEqual(len(self.adb.taps), taps)

    def test_a_wrong_name_is_corrected_by_naming_the_right_screen(self):
        self.start()
        self.t("goto", "passbook", code=2)
        self.t("tap", "Profile")
        self.t("check", "Statements")  # the wrong screen checked for "passbook"
        self.t("tap", "Statements")
        self.t("tap", "Passbook")
        self.t("check", "Passbook")
        self.t("goto", "statements", code=2)  # a new request closes the "passbook" search
        self.t("key", "back")
        out = self.t("goto", "passbook")
        self.assertEqual(self.adb.current, "passbook")  # the last check of the search won, not the first
        self.assertIn("arrived", out)

    def test_a_name_bound_to_two_screens_is_a_conflict_until_fixed(self):
        self.start()
        self.explore_passbook()
        self.stop()
        self.start()
        self.t("goto", "passbook2", code=2)
        self.t("tap", "Profile")
        self.t("check", "Statements")
        self.stop()
        # Both runs learned a name; teach "passbook" to Statements too, by hand-editing a learned line.
        mapfile = os.path.join(self.home, "maps", PACKAGE, "map.jsonl")
        lines = [json.loads(x) for x in open(mapfile)]
        stmt = [d for d in lines if d.get("k") == "name" and d["phrase"] == "passbook2"][0]["screen"]
        with open(mapfile, "a") as f:
            f.write(json.dumps({"k": "name", "phrase": "passbook", "screen": stmt, "src": "learned"}) + "\n")
        self.start()
        out = self.t("goto", "passbook", code=2)
        self.assertIn("more than one screen", out)
        self.t("tap", "Savings")
        self.t("tap", "Passbook")
        self.t("map", "name", "here", "passbook")
        self.t("key", "back")
        self.t("goto", "passbook")
        self.assertEqual(self.adb.current, "passbook")

    def test_forget_a_name(self):
        self.start()
        self.explore_passbook()
        self.t("map", "forget", "passbook")
        self.t("key", "back")
        self.t("goto", "passbook", code=2)

    def test_a_phrase_with_no_screen_in_it_is_refused(self):
        self.start()
        self.t("goto", "open", code=2)


class EntryPointsTest(GotoBase):
    def test_the_route_starts_from_the_screen_the_user_is_on(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        self.t("tap", "Profile")
        self.t("tap", "Statements")
        self.t("tap", "Passbook")
        self.t("check", "Passbook")
        self.stop()
        self.start()
        self.t("tap", "Profile")
        taps = len(self.adb.taps)
        self.t("goto", "passbook")
        self.assertEqual(len(self.adb.taps) - taps, 2)  # Statements, Passbook: not back to Home first
        self.assertEqual(self.adb.current, "passbook")

    def test_an_unknown_start_screen_hands_back_without_pressing_back(self):
        self.start()
        self.explore_passbook()
        self.adb.current = "offers"
        keys = [c for c in self.adb.calls if "keyevent" in c]
        out = self.t("goto", "passbook", code=2)
        self.assertIn("no route starts here", out)
        self.assertEqual([c for c in self.adb.calls if "keyevent" in c], keys)

    def test_a_system_dialog_on_top_is_not_a_place_to_start(self):
        self.adb.app.packages["perm"] = "com.android.permissioncontroller"
        self.adb.app.screens["perm"] = [node(text="Allow"), node(text="Don't allow", bounds=(0, 200, 100, 300))]
        self.start()
        self.explore_passbook()
        self.adb.current = "perm"
        self.assertIn("foreign", self.t("goto", "passbook", code=2))


class SelfHealTest(GotoBase):
    def test_a_relabelled_button_with_the_same_id_still_works(self):
        self.start()
        self.explore_passbook()
        self.stop()
        self.adb.version = "2.0 (2)"
        self.adb.app.screens["home"][3] = node(id="btn_savings", text="Accounts", bounds=(0, 2200, 270, 2400))
        self.start()
        self.t("goto", "passbook")
        self.assertEqual(self.adb.current, "passbook")

    def test_a_replaced_button_stops_at_that_step_and_the_new_way_is_learned(self):
        self.start()
        self.explore_passbook()
        self.stop()
        # Build 2: "Savings" is now "Accounts", a new button.
        self.adb.version = "2.0 (2)"
        home = self.adb.app.screens["home"]
        home[3] = node(id="btn_accounts", text="Accounts", bounds=(0, 2200, 270, 2400))
        self.adb.app.on_tap[("home", "btn_accounts")] = "savings"
        self.start()
        out = self.t("goto", "passbook", code=1)
        self.assertIn("FAILED at step 1", out)
        self.assertIn("not on the screen", out)
        self.assertEqual(self.adb.current, "home")
        self.t("goto", "passbook", code=2)  # the broken step is not tried again in this run
        self.t("tap", "Accounts")
        self.t("tap", "Passbook")
        self.t("check", "Passbook")
        self.t("key", "back")
        self.t("goto", "passbook")
        self.assertEqual(self.adb.current, "passbook")

    def test_a_tap_that_lands_elsewhere_fails_fast_and_names_where(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        self.adb.app.on_tap[("home", "btn_savings")] = "offers"  # an offers screen now opens first
        self.adb.app.screens["offers"] = [bar("Offers"), node(id="btn_close", text="Close")]
        self.t("tap", "Profile")
        self.t("check", "Statements")
        self.t("key", "back")
        before = self.adb_clock()
        out = self.t("goto", "passbook", code=1)
        self.assertIn("it went to another screen", out)
        self.assertLess(self.adb_clock() - before, 5)  # well before the 10 s hop timeout

    def adb_clock(self):
        from teesri_nazar import appmap
        return appmap.time.monotonic()


class SafetyTest(GotoBase):
    def test_a_route_through_pay_or_an_mpin_screen_never_runs(self):
        self.start()
        self.t("goto", "mpin", code=2)
        self.t("tap", "Pay")
        self.t("tap", "Confirm")
        self.t("check", "Enter MPIN")
        self.t("key", "back")
        taps = len(self.adb.taps)
        out = self.t("goto", "mpin", code=2)
        self.assertIn("no route the map can run", out)
        self.assertEqual(len(self.adb.taps), taps)

    def test_dry_run_shows_the_route_and_taps_nothing(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        taps = len(self.adb.taps)
        out = self.t("goto", "passbook", "--dry-run")
        self.assertIn('tap "Savings" → tap "Passbook"', out)
        self.assertEqual(len(self.adb.taps), taps)


class FlowWinsTest(GotoBase):
    def test_a_saved_flow_with_that_name_runs_instead(self):
        os.makedirs(os.path.join(self.home, "plans"))
        with open(os.path.join(self.home, "plans", "bank.plan"), "w") as f:
            f.write(f"app {PACKAGE}\n\nflow open_loans\n  tap Loans\n  wait \"Active loan\"\nend\n")
        self.start()
        out = self.t("goto", "loans")
        self.assertIn("saved flow `open_loans`", out)
        self.assertEqual(self.adb.current, "loans")


class HoldTest(GotoBase):
    def test_a_dashboard_message_stops_the_route_between_steps(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        real_tap = self.adb._shell

        def tap_then_message(a):
            out = real_tap(a)
            if a[:2] == ["input", "tap"] and self.adb.current == "savings":
                live.post("stop, open loans instead")
            return out
        with mock.patch.object(self.adb, "_shell", tap_then_message):
            got, out, _ = tnazar("goto", "passbook")
        self.assertEqual(got, live.EXIT_HELD, out)
        self.assertEqual(self.adb.current, "savings")


class NeverWorseTest(GotoBase):
    SESSION = [("tap", "Savings"), ("tap", "Passbook"), ("check", "Passbook"), ("key", "back"), ("observe",),
               ("wait", "Loans"), ("scroll", "down", "--to", "Pay")]

    def session(self):
        self.start()
        before = len(self.adb.calls)
        outs = [self.t(*argv) for argv in self.SESSION]
        calls = self.adb.calls[before:]
        self.stop()
        return calls, outs

    def test_map_on_and_off_make_the_same_device_calls_and_print_the_same(self):
        on_calls, on_out = self.session()
        with mock.patch.dict(os.environ, {"TNAZAR_MAP": "off"}):
            off_calls, off_out = self.session()
        self.assertEqual(on_calls, off_calls)
        self.assertEqual(on_out, off_out)

    def test_map_off_learns_nothing_and_says_so(self):
        with mock.patch.dict(os.environ, {"TNAZAR_MAP": "off"}):
            out = self.t("start", "r1", "--package", PACKAGE)
            self.assertIn("map: off", out)
            self.explore_passbook.__func__  # noqa: B018
            self.t("goto", "passbook", code=2)
            self.t("tap", "Savings")
            self.t("stop")
        self.assertFalse(os.path.exists(os.path.join(self.home, "maps", PACKAGE)))

    def test_start_says_where_the_map_is_kept(self):
        out = self.t("start", "r1", "--package", PACKAGE)
        self.assertIn(f"map: learning routes into {os.path.join(self.home, 'maps', PACKAGE)}", out)

    def test_a_broken_map_file_never_breaks_a_step(self):
        os.makedirs(os.path.join(self.home, "maps", PACKAGE))
        with open(os.path.join(self.home, "maps", PACKAGE, "map.jsonl"), "w") as f:
            f.write("not json\n{\"k\": \"edge\"}\n")
        self.start()
        self.explore_passbook()
        self.stop()
        self.start()
        self.t("goto", "passbook")

    def test_an_unwritable_map_directory_never_breaks_stop(self):
        with open(os.path.join(self.home, "maps"), "w") as f:
            f.write("a file where the directory should be")
        self.start()
        self.explore_passbook()
        got, out, err = tnazar("stop")
        self.assertEqual(got, 0)
        self.assertIn("warning: map", err)


class PrivacyTest(GotoBase):
    def test_no_names_balances_or_typed_values_reach_the_map(self):
        self.start()
        self.t("observe")
        self.explore_passbook()
        self.stop()
        blob = open(os.path.join(self.home, "maps", PACKAGE, "map.jsonl")).read()
        for word in ("Asha", "1,000", "Coffee", "120"):
            self.assertNotIn(word, blob)

    def test_audit_lists_every_readable_string(self):
        self.start()
        self.explore_passbook()
        self.stop()
        out = self.t("map", "audit", "--app", PACKAGE)
        for s in ("Savings", "Passbook", "passbook"):
            self.assertIn(f"  {s}\n", out)


class ReplayTest(GotoBase):
    def test_a_run_with_goto_replays(self):
        self.start()
        self.explore_passbook()
        self.t("key", "back")
        self.t("goto", "passbook")
        self.t("check", "Passbook")
        self.stop()
        self.adb.current = "home"
        got, out, err = tnazar("replay", "r1")
        self.assertEqual(got, 0, out + err)


if __name__ == "__main__":
    unittest.main()
