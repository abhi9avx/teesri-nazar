"""The navigation map's pure parts: learning from a run's events, folding the store, routing."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from teesri_nazar import navmap
from teesri_nazar.ident import Ident

APP = "com.example.bank"


def T(sec: float, day: str = "2020-01-01") -> str:
    m, s = divmod(sec, 60)
    return f"{day}T10:{int(m):02d}:{s:06.3f}Z"


class Log:
    """Builds a run's events the way the CLI logs them."""

    def __init__(self, day: str = "2020-01-01"):
        self.events, self.t, self.day = [], 0.0, day

    def add(self, **e):
        self.t += 1
        e.setdefault("t", T(self.t, self.day))
        self.events.append(e)
        return self

    def seen(self, sid):
        return self.add(type="seen", sid=sid)

    def tap(self, sid, text=None, id=None, label=None, **kw):
        sel = {k: v for k, v in (("id", id), ("text", text)) if v} or None
        return self.add(type="tap", sid=sid, selector=sel, label=label or text or id, **kw)

    def wait(self, sid, ok=True):
        return self.add(type="wait", sid=sid, ok=ok, selector={"text": "x"})

    def check(self, sid, ok=True, **kw):
        return self.add(type="check", kind="ui", sid=sid, ok=ok, selector={"text": "x"}, **kw)


def scr(sid, title="t"):
    return {"k": "screen", "id": sid, "tokens": [sid + "1", sid + "2", sid + "3", sid + "4", sid + "5"],
            "title": title, "secure": False}


def learn(log, run="r1", ver="1.0", screens=None):
    return navmap.learn(log.events, run=run, version=ver, screens=screens or {})


def edges(obs):
    return [(o["from"], o["to"], o.get("kind")) for o in obs if o["k"] == "edge" and o["ok"]]


class LearnEdgesTest(unittest.TestCase):
    def test_tap_then_the_next_read_on_another_screen_is_an_edge(self):
        log = Log().seen("HOME").tap("HOME", text="Savings").wait("SAV")
        self.assertEqual(edges(learn(log)), [("HOME", "SAV", "tap")])

    def test_the_next_taps_read_closes_the_edge(self):
        log = Log().tap("HOME", text="Savings").tap("SAV", text="Passbook").check("PB")
        self.assertEqual(edges(learn(log)), [("HOME", "SAV", "tap"), ("SAV", "PB", "tap")])

    def test_a_tap_that_stays_on_the_same_screen_learns_nothing(self):
        log = Log().tap("HOME", text="Dark mode").wait("HOME").tap("HOME", text="Savings").wait("SAV")
        self.assertEqual(edges(learn(log)), [("HOME", "SAV", "tap")])

    def test_two_actions_before_the_next_read_learn_nothing(self):
        log = Log().tap("HOME", text="Savings").add(type="tap", selector=None, xy=[1, 2], label="1,2").wait("PB")
        self.assertEqual(edges(learn(log)), [])

    def test_typing_in_between_learns_nothing(self):
        log = Log().tap("LOGIN", text="Login").add(type="type", secret=True, by="dashboard").wait("HOME")
        self.assertEqual(edges(learn(log)), [])

    def test_a_pixel_tap_is_knowledge_only(self):
        log = Log().seen("HOME").add(type="tap", selector=None, xy=[5, 5], label="50%,90%").wait("SAV")
        self.assertEqual(edges(learn(log)), [("HOME", "SAV", "xy")])

    def test_back_is_knowledge_only(self):
        log = Log().seen("SAV").add(type="key", key="back").seen("HOME")
        self.assertEqual(edges(learn(log)), [("SAV", "HOME", "back")])

    def test_fresh_launch_starts_from_the_cold_node(self):
        log = Log().add(type="launch", fresh=True, restart=False).seen("HOME")
        self.assertEqual(edges(learn(log)), [("@cold", "HOME", "cold")])

    def test_plain_launch_and_deep_links_break_the_chain(self):
        for e in ({"type": "launch", "fresh": False, "restart": False}, {"type": "open_url", "url": "x://y"}):
            log = Log().tap("HOME", text="Savings").add(**e).seen("PB")
            self.assertEqual(edges(learn(log)), [], e)

    def test_more_than_a_minute_to_arrive_learns_nothing(self):
        log = Log().tap("HOME", text="Savings")
        log.t += 61
        log.wait("SAV")
        self.assertEqual(edges(learn(log)), [])

    def test_failed_waits_and_missed_taps_do_not_count_as_reads(self):
        log = Log().tap("HOME", text="Savings").wait("HALF", ok=False).add(type="tap", ok=False, target="x") \
            .wait("SAV")
        self.assertEqual(edges(learn(log)), [("HOME", "SAV", "tap")])

    def test_scroll_to_before_the_tap_is_remembered_on_the_edge(self):
        log = Log().seen("HOME").add(type="scroll", sid="HOME", direction="down", to={"text": "Passbook"}, found=True) \
            .tap("HOME", text="Passbook").wait("PB")
        e = [o for o in learn(log) if o["k"] == "edge"][0]
        self.assertEqual(e["scroll"], {"direction": "down", "to": {"text": "Passbook"}})

    def test_label_with_digits_or_secrets_is_never_stored_and_the_edge_is_knowledge_only(self):
        log = Log().tap("HOME", text="Pay ₹500").wait("PAY")
        e = [o for o in learn(log) if o["k"] == "edge"][0]
        self.assertEqual((e["kind"], e["sel"], e["label"]), ("xy", None, ""))


class ReviewFixesTest(unittest.TestCase):
    """Found by the spec review: each one a way to run a money step, leak text, or bind a wrong name."""

    def test_a_money_button_with_an_amount_is_never_runnable_even_by_its_id(self):
        log = Log().add(type="tap", sid="CART", selector={"text": "Place order ₹1,299"},
                        stable={"id": "btn_primary"}, label="Place order ₹1,299").wait("DONE")
        e = [o for o in learn(log) if o["k"] == "edge"][0]
        self.assertNotEqual(e["kind"], "tap")

    def test_scroll_hints_are_scrubbed(self):
        log = Log().seen("HOME").add(type="scroll", sid="HOME", direction="down",
                                     to={"text": "John Smith 9876543210"}, found=True) \
            .tap("HOME", text="Statements").wait("STMT")
        e = [o for o in learn(log) if o["k"] == "edge"][0]
        self.assertIsNone(e["scroll"])
        self.assertNotIn("9876543210", json.dumps(learn(log)))

    def test_navigate_up_and_close_are_knowledge_only(self):
        for sel in ({"desc": "Navigate up"}, {"desc": "Close"}, {"text": "Cancel"}, {"id": "btn_back"}):
            log = Log().add(type="tap", sid="SAV", selector=sel, label="x").wait("HOME")
            self.assertEqual(edges(learn(log)), [("SAV", "HOME", "back")], sel)

    def test_long_labels_are_not_stored(self):
        log = Log().tap("HOME", text="Asha Ramachandran Krishnan").wait("PROF")
        e = [o for o in learn(log) if o["k"] == "edge"][0]
        self.assertEqual((e["sel"], e["label"]), (None, ""))

    def test_a_name_repeated_in_one_run_is_one_line(self):
        log = Log()
        for _ in range(3):
            log.add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True).check("PB")
        self.assertEqual(sum(1 for o in learn(log) if o["k"] == "name"), 1)

    def test_a_goto_answered_by_a_flow_ends_the_search(self):
        log = Log().add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True) \
            .add(type="nav", phrase="settings", outcome="flow").check("SET")
        self.assertEqual([o for o in learn(log) if o["k"] == "name"], [])


class EpisodeTest(unittest.TestCase):
    def miss(self, log, phrase, sid):
        return log.add(type="nav", phrase=phrase, outcome="unknown", sid=sid, episode=True)

    def names(self, obs):
        return [(o["phrase"], o["screen"]) for o in obs if o["k"] == "name"]

    def test_the_last_passing_check_of_the_episode_names_the_screen(self):
        log = self.miss(Log(), "loan statement", "HOME").tap("HOME", text="Loans").check("LOANS") \
            .tap("LOANS", text="Statement").check("STMT")
        self.assertEqual(self.names(learn(log)), [("loan statement", "STMT")])

    def test_no_check_no_name(self):
        log = self.miss(Log(), "passbook", "HOME").tap("HOME", text="Savings").wait("SAV")
        self.assertEqual(self.names(learn(log)), [])

    def test_gone_manual_and_start_screen_checks_never_name(self):
        log = self.miss(Log(), "passbook", "HOME").check("HOME").check("SAV", gone=True) \
            .add(type="check", kind="db", ok=True, what="x")
        self.assertEqual(self.names(learn(log)), [])

    def test_episode_closes_at_case_end_so_a_later_check_does_not_name(self):
        log = self.miss(Log(), "passbook", "HOME").add(type="case_end", case="TC-01", status="pass") \
            .tap("HOME", text="Savings").check("SAV")
        self.assertEqual(self.names(learn(log)), [])

    def test_a_check_that_says_the_phrase_names_the_screen_and_ends_the_search(self):
        log = self.miss(Log(), "passbook", "HOME").tap("HOME", text="Savings").wait("SAV") \
            .tap("SAV", text="Passbook").add(type="check", kind="ui", sid="PB", ok=True, selector={"text": "Passbook"}) \
            .add(type="key", key="back").seen("HOME").tap("HOME", text="Profile") \
            .add(type="check", kind="ui", sid="PROF", ok=True, selector={"text": "Statements"})
        self.assertEqual(self.names(learn(log)), [("passbook", "PB")])

    def test_the_closest_check_wins_over_a_later_unrelated_one(self):
        log = self.miss(Log(), "loan statement", "HOME") \
            .add(type="check", kind="ui", sid="LOANS", ok=True, selector={"text": "Active loan"}) \
            .add(type="check", kind="ui", sid="OTHER", ok=True, selector={"id": "banner"})
        self.assertEqual(self.names(learn(log)), [("loan statement", "LOANS")])

    def test_an_unrelated_check_left_with_back_does_not_name(self):
        log = self.miss(Log(), "my money", "HOME") \
            .add(type="check", kind="ui", sid="PROF", ok=True, selector={"text": "Statements"}) \
            .add(type="key", key="back").seen("HOME")
        self.assertEqual(self.names(learn(log)), [])

    def test_an_unrelated_last_check_still_teaches_a_synonym(self):
        log = self.miss(Log(), "my money", "HOME").tap("HOME", text="Savings") \
            .add(type="check", kind="ui", sid="SAV", ok=True, selector={"text": "Savings"})
        self.assertEqual(self.names(learn(log)), [("money", "SAV")])

    def test_episode_closes_after_too_many_actions(self):
        log = self.miss(Log(), "passbook", "HOME")
        for _ in range(navmap.EPISODE_ACTIONS + 1):
            log.add(type="key", key="enter")
        log.check("SAV")
        self.assertEqual(self.names(learn(log)), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.dict(os.environ, {"TNAZAR_MAP_DIR": d.name})
        p.start()
        self.addCleanup(p.stop)
        self.dir = Path(d.name)

    def save(self, log, run, ver="1.0", sids=("HOME", "SAV", "PB", "PROF", "STMT", "LOANS", "PAY")):
        obs = navmap.learn(log.events, run=run, version=ver, screens={s: scr(s) for s in sids})
        navmap.Store(APP).append(obs, run)

    def route(self, start, phrase, ver="1.0", **kw):
        m = navmap.NavMap.load(APP, version=ver, **kw)
        return m.route(start, m.resolve(phrase))

    def test_learned_route_and_name_work_next_time(self):
        log = Log().add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True) \
            .tap("HOME", text="Savings").wait("SAV").tap("SAV", text="Passbook").check("PB")
        self.save(log, "r1")
        hops = self.route("HOME", "Open my Passbook")
        self.assertEqual([(h.frm, h.to) for h in hops], [("HOME", "SAV"), ("SAV", "PB")])

    def test_repeats_add_no_rows(self):
        for run in ("r1", "r2", "r3"):
            log = Log()
            for _ in range(10):
                log.tap("HOME", text="Savings").wait("SAV").add(type="key", key="back").seen("HOME")
            self.save(log, run)
        m = navmap.NavMap.load(APP, version="1.0")
        taps = [e for e in m.edges.values() if e.kind == "tap"]
        self.assertEqual(len(taps), 1)
        self.assertEqual(taps[0].ok_runs, {"r1", "r2", "r3"})

    def test_a_run_is_learned_once(self):
        log = Log().tap("HOME", text="Savings").wait("SAV")
        self.save(log, "r1")
        self.save(log, "r1")
        lines = (self.dir / APP / "map.jsonl").read_text().splitlines()
        self.assertEqual(sum(1 for ln in lines if '"edge"' in ln), 1)

    def test_shortest_route_from_where_the_user_is(self):
        log = Log().tap("HOME", text="Savings").wait("SAV").tap("SAV", text="Passbook").check("PB") \
            .seen("HOME").tap("HOME", text="Profile").wait("PROF").tap("PROF", text="Statements").wait("STMT") \
            .tap("STMT", text="Passbook").wait("PB")
        self.save(log, "r1")
        m = navmap.NavMap.load(APP, version="1.0")
        m.add_name("passbook", "PB", explicit=True)
        self.assertEqual(len(m.route("HOME", m.resolve("passbook"))), 2)
        self.assertEqual([h.frm for h in m.route("PROF", m.resolve("passbook"))], ["PROF", "STMT"])
        self.assertEqual(m.route("PB", m.resolve("passbook")), [])

    def test_unsafe_and_knowledge_edges_never_route(self):
        log = Log().tap("HOME", text="Pay").wait("PAY").seen("HOME").add(type="tap", selector=None, xy=[1, 1],
                                                                          label="1,1").wait("SAV")
        self.save(log, "r1")
        m = navmap.NavMap.load(APP, version="1.0")
        self.assertIsNone(m.route("HOME", {"PAY"}))
        self.assertIsNone(m.route("HOME", {"SAV"}))

    def test_password_target_is_unsafe(self):
        log = Log().tap("HOME", text="Login").wait("MPIN")
        obs = navmap.learn(log.events, run="r1", version="1", screens={
            "HOME": scr("HOME"), "MPIN": dict(scr("MPIN"), secure=True)})
        navmap.Store(APP).append(obs, "r1")
        self.assertIsNone(navmap.NavMap.load(APP, version="1").route("HOME", {"MPIN"}))

    def test_editing_the_file_cannot_make_an_unsafe_edge_safe(self):
        log = Log().tap("HOME", text="Transfer").wait("PAY")
        self.save(log, "r1")
        f = self.dir / APP / "map.jsonl"
        f.write_text(f.read_text().replace('"unsafe": true', '"unsafe": false'))
        self.assertIsNone(navmap.NavMap.load(APP, version="1.0").route("HOME", {"PAY"}))

    def test_two_failures_on_this_version_retire_an_edge_and_a_success_restores_it(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1", ver="2.0")
        fail = Log().add(type="nav", phrase="x", outcome="failed",
                         edge={"from": "HOME", "to": "SAV", "kind": "tap", "sel": {"text": "Savings"}})
        self.save(fail, "r2", ver="2.0")
        self.assertTrue(self.route("HOME", "SAV", ver="2.0"))
        self.save(fail, "r3", ver="2.0")
        self.assertIsNone(self.route("HOME", "SAV", ver="2.0"))
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r4", ver="2.0")
        self.assertTrue(self.route("HOME", "SAV", ver="2.0"))

    def test_failures_on_an_old_version_do_not_retire_the_step_on_a_new_one(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1", ver="1.0")
        fail = Log().add(type="nav", phrase="x", outcome="failed",
                         edge={"from": "HOME", "to": "SAV", "kind": "tap", "sel": {"text": "Savings"}})
        self.save(fail, "r2", ver="1.0")
        self.save(fail, "r3", ver="1.0")
        self.assertIsNone(self.route("HOME", "SAV", ver="1.0"))
        self.assertTrue(self.route("HOME", "SAV", ver="2.0"))

    def test_a_tap_that_stays_put_never_becomes_a_step_to_itself(self):
        near = dict(scr("HOME"), id="HOME2", tokens=scr("HOME")["tokens"][:4] + ["HOMEx"])
        obs = navmap.learn(Log().tap("HOME", text="Dark mode").wait("HOME2").events, run="r1", version="1",
                           screens={"HOME": scr("HOME"), "HOME2": near})
        navmap.Store(APP).append(obs, "r1")
        self.assertEqual(navmap.NavMap.load(APP, version="1").edges, {})

    def test_a_screen_with_a_pay_title_is_never_a_target(self):
        log = Log().tap("HOME", text="Bills").wait("PAYSCR")
        obs = navmap.learn(log.events, run="r1", version="1",
                           screens={"HOME": scr("HOME"), "PAYSCR": dict(scr("PAYSCR"), secure=True)})
        navmap.Store(APP).append(obs, "r1")
        self.assertIsNone(navmap.NavMap.load(APP, version="1").route("HOME", {"PAYSCR"}))

    def test_a_map_written_by_another_schema_is_left_alone(self):
        d = self.dir / APP
        d.mkdir(parents=True)
        (d / "map.jsonl").write_text('{"k": "header", "schema": 99, "app": "%s"}\n' % APP)
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1")
        self.assertEqual((d / "map.jsonl").read_text().count("\n"), 1)

    def test_compact_keeps_what_the_map_means(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV").tap("SAV", text="Passbook").check("PB"), "r1")
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r2")
        navmap.Store(APP).append([navmap.name_line("passbook", "PB", explicit=True)], None)
        before = navmap.NavMap.load(APP, version="1.0")
        navmap.compact(APP, "1.0")
        after = navmap.NavMap.load(APP, version="1.0")
        self.assertEqual({k: (e.ok_runs, e.results) for k, e in before.edges.items()},
                         {k: (e.ok_runs, e.results) for k, e in after.edges.items()})
        self.assertEqual(after.resolve("passbook"), {"PB"})
        self.assertTrue(navmap.Store(APP).learned("r1"))

    def test_an_edge_that_failed_in_this_run_is_not_retried(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1")
        m = navmap.NavMap.load(APP, version="1.0", failed={("HOME", "SAV", "tap", '{"text": "Savings"}')})
        self.assertIsNone(m.route("HOME", {"SAV"}))

    def test_explicit_name_overrides_learned_ones_and_conflicts_are_reported(self):
        a = Log().add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True).check("PB")
        b = Log().add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True).check("STMT")
        self.save(a, "r1")
        self.save(b, "r2")
        m = navmap.NavMap.load(APP, version="1.0")
        self.assertEqual(m.resolve("passbook"), {"PB", "STMT"})  # a conflict: the caller lists both
        navmap.Store(APP).append([navmap.name_line("passbook", "PB", explicit=True)], None)
        self.assertEqual(navmap.NavMap.load(APP, version="1.0").resolve("passbook"), {"PB"})

    def test_forget_beats_evidence_from_before_it_even_from_the_running_run(self):
        log = Log().add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True).check("PB")
        extra = navmap.learn(log.events, run="r1", version="1.0", screens={"PB": scr("PB")})
        navmap.Store(APP).append([navmap.forget_line("name", "passbook")], None)
        self.assertEqual(navmap.NavMap.load(APP, version="1.0", extra=extra).resolve("passbook"), set())

    def test_forget_removes_a_name_and_later_evidence_relearns_it(self):
        self.save(Log().add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True)
                  .check("PB"), "r1")
        navmap.Store(APP).append([navmap.forget_line("name", "passbook")], None)
        self.assertEqual(navmap.NavMap.load(APP, version="1.0").resolve("passbook"), set())
        later = Log(day="2099-01-01")  # a run after the forget
        self.save(later.add(type="nav", phrase="passbook", outcome="unknown", sid="HOME", episode=True)
                  .check("PB"), "r2")
        self.assertEqual(navmap.NavMap.load(APP, version="1.0").resolve("passbook"), {"PB"})

    def test_screens_seen_again_under_a_new_id_are_the_same_screen(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1")
        # Run 2 read Savings with one badge more: a new id, the same screen.
        near = dict(scr("SAV"), id="SAV2", tokens=scr("SAV")["tokens"][:4] + ["SAVx", "SAV5"])
        obs = navmap.learn(Log().tap("SAV2", text="Passbook").wait("PB").events, run="r2", version="1.0",
                           screens={"SAV2": near, "PB": scr("PB")})
        navmap.Store(APP).append(obs, "r2")
        m = navmap.NavMap.load(APP, version="1.0")
        self.assertEqual(len(m.route("HOME", {"PB"})), 2)

    def test_apps_are_isolated(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1")
        self.assertEqual(navmap.NavMap.load("com.other", version="1").edges, {})

    def test_corrupt_lines_are_skipped(self):
        self.save(Log().tap("HOME", text="Savings").wait("SAV"), "r1")
        with open(self.dir / APP / "map.jsonl", "a") as f:
            f.write('{"k": "edge", "from": \n')
        self.save(Log().tap("SAV", text="Passbook").wait("PB"), "r2")
        m = navmap.NavMap.load(APP, version="1.0")
        self.assertEqual(len(m.route("HOME", {"PB"})), 2)
        self.assertTrue(m.warnings)

    def test_nothing_with_digits_names_or_secret_values_is_stored(self):
        log = Log().tap("HOME", text="Hi Asha 9876543210").wait("SAV").tap("SAV", text="₹1,000").wait("PB")
        self.save(log, "r1")
        blob = (self.dir / APP / "map.jsonl").read_text()
        for bad in ("9876543210", "1,000", "Asha"):
            self.assertNotIn(bad, blob)


class PhraseTest(unittest.TestCase):
    def test_normalise(self):
        for raw in ("Open my Passbook", "go to passbook", "PASSBOOK", "  the  passbook ", "show me the passbook"):
            self.assertEqual(navmap.normalise(raw), "passbook", raw)
        self.assertEqual(navmap.normalise("open"), "")
        self.assertEqual(navmap.normalise("open_settings"), "settings")

    def test_close_names_are_suggested_never_used(self):
        self.assertEqual(navmap.suggest("pasbook", ["passbook", "loans", "profile"]), ["passbook"])
        self.assertEqual(navmap.suggest("xyz", ["passbook"]), [])


if __name__ == "__main__":
    unittest.main()
