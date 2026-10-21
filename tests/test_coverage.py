"""Coverage ledger: every requested case and expected result is accounted for.

docs/prd-coverage.md, docs/trd-coverage.md, docs/test-plan-coverage.md (test IDs below).
Phase 0 pins today's behaviour for runs that declare nothing: it must never change.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from teesri_nazar import coverage, plan
from teesri_nazar import report as rpt
from teesri_nazar import ui, usage
from teesri_nazar.session import Run

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).with_name("fixtures") / "coverage"
HTML = Path(ui.__file__).with_name("ui.html").read_text()


def temp_home(test: unittest.TestCase) -> None:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    env = mock.patch.dict(os.environ, {"TNAZAR_HOME": tmp.name})
    env.start()
    test.addCleanup(env.stop)


def legacy_run() -> Run:
    """A finished hand-driven run as tnazar records it today: no declared cases, every status."""
    run = Run.create("legacy", {"package": "com.example.app", "prompt": "test login",
                                "started_at": "2026-10-21T10:00:00.000Z", "model": "Pixel", "device": "fake-1",
                                "android": "15", "app_version": "1.0"})
    run.update_meta(started_at="2026-10-21T10:00:00.000Z", finished_at="2026-10-21T10:05:00.000Z")
    events = [
        {"type": "case_start", "case": "TC-01", "title": "Login works"},
        {"type": "tap", "selector": {"text": "Login"}, "xy": [1, 1], "label": "Login", "dur_ms": 900},
        {"type": "check", "kind": "ui", "ok": True, "what": "Home visible", "case": "TC-01", "shot": "shots/a.png"},
        {"type": "case_end", "case": "TC-01", "status": "pass", "reason": ""},
        {"type": "case_start", "case": "TC-02", "title": "Wrong MPIN"},
        {"type": "check", "kind": "db", "ok": False, "what": "attempts incremented", "detail": "got 0", "case": "TC-02"},
        {"type": "note", "kind": "bug", "text": "no error shown", "case": "TC-02"},
        {"type": "case_end", "case": "TC-02", "status": "fail", "reason": "failed: attempts incremented",
         "evidence": ["shots/b.png", "logs/l.txt"]},
        {"type": "case_start", "case": "TC-03", "title": "Offline"},
        {"type": "case_end", "case": "TC-03", "status": "blocked", "reason": "network down"},
        {"type": "case_start", "case": "TC-04", "title": "Out of scope"},
        {"type": "case_end", "case": "TC-04", "status": "skipped", "reason": "not in this release"},
        {"type": "check", "kind": "ui", "ok": True, "what": "loose check", "case": None},
    ]
    with run.events_path.open("w") as f:
        for i, e in enumerate(events):
            f.write(json.dumps({"t": f"2026-10-21T10:0{i // 6}:{(i % 6) * 10:02d}.000Z", **e}) + "\n")
    return run


def node_eval(expr: str):
    a = HTML.index("const fmtDur")
    script = (HTML[a:HTML.index("\n", a)] + "\n" + HTML[HTML.index("// Ids are written"):
              HTML.index("// ------------------------------------------------------------------ timeline")]
              + f"\nconsole.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


class Phase0UndeclaredRunsTest(unittest.TestCase):
    """PH0: a run that declares nothing reports exactly as it did before this feature."""

    def setUp(self):
        temp_home(self)
        no_transcripts = mock.patch.object(usage, "transcripts", return_value=[])
        no_transcripts.start()
        self.addCleanup(no_transcripts.stop)

    def test_ph0_1_summary_line_is_unchanged(self):
        self.assertEqual(rpt.summary_line(rpt.summarize(legacy_run())),
                         "4 case(s): ✅ 1 pass  ❌ 1 fail  ⛔ 1 blocked  ⏭ 1 skipped  | checks 2/3 passed")

    def test_ph0_1_summary_line_with_no_cases(self):
        run = Run.create("empty", {"package": "com.example.app"})
        self.assertEqual(rpt.summary_line(rpt.summarize(run)), "0 case(s): none recorded  | checks 0/0 passed")

    def test_ph0_2_report_is_unchanged(self):
        run = legacy_run()
        md = rpt.to_markdown(run, rpt.summarize(run))
        self.assertEqual(md, (GOLDEN / "legacy_report.md").read_text())

    def test_ph0_2_html_report_is_unchanged(self):
        run = legacy_run()
        page = rpt.to_html(run, rpt.summarize(run))
        self.assertEqual(page, (GOLDEN / "legacy_report.html").read_text())

    def test_ph0_5_example_plans_parse_as_before(self):
        p = plan.load(str(ROOT / "examples" / "settings.plan"))
        self.assertEqual([(c.id, c.title) for c in p.cases],
                         [("TC-01", "Network & internet opens"), ("TC-02", "Search finds Wi-Fi settings"),
                          ("TC-03", "Back from search returns to Settings")])

    @unittest.skipUnless(shutil.which("node"), "needs node to run the dashboard's layout()")
    def test_ph0_7_dashboard_timeline_is_unchanged(self):
        events = [json.loads(line) for line in legacy_run().events_path.read_text().splitlines()]
        blocks = node_eval(f"layout({json.dumps(events)}).map((b) => [b.kind, b.e ? b.e.type : null])")
        self.assertEqual(blocks, [["case_start", "case_start"], ["step", "tap"], ["step", "check"],
                                  ["case_end", "case_end"], ["case_start", "case_start"], ["step", "check"],
                                  ["step", "note"], ["case_end", "case_end"], ["case_start", "case_start"],
                                  ["case_end", "case_end"], ["case_start", "case_start"], ["case_end", "case_end"],
                                  ["step", "check"]])


def ev_plan(case, title="", expects=(), added=False):
    return {"type": "case_plan", "case": case, "title": title, "expects": list(expects), "added": added}


def ev_start(case, title=""):
    return {"type": "case_start", "case": case, "title": title}


def ev_check(ok, for_=None, case=None, kind="ui", what="a check"):
    e = {"type": "check", "kind": kind, "ok": ok, "what": what, "case": case}
    if for_ is not None:
        e["for"] = list(for_)
    return e


def ev_end(case, status, reason=""):
    return {"type": "case_end", "case": case, "status": status, "reason": reason}


class RequestedTest(unittest.TestCase):
    def test_cov_1_a_run_without_case_plan_is_not_declared(self):
        events = [ev_start("TC-01"), ev_end("TC-01", "pass")]
        self.assertFalse(coverage.declared(events))
        self.assertEqual(coverage.requested(events), [])

    def test_cov_2_requested_keeps_first_order_and_the_latest_declaration_wins(self):
        events = [ev_plan("TC-01", "Old title", ["a"]), ev_plan("TC-02", "Second"),
                  ev_plan("TC-01", "New title", ["a", "b"])]
        self.assertTrue(coverage.declared(events))
        self.assertEqual(coverage.requested(events), [
            {"id": "TC-01", "title": "New title", "expects": ["a", "b"], "added": False},
            {"id": "TC-02", "title": "Second", "expects": [], "added": False}])
        self.assertEqual(coverage.expects_of(events, "TC-01"), ["a", "b"])
        self.assertEqual(coverage.expects_of(events, "TC-09"), [])


class ExpectationStatesTest(unittest.TestCase):
    EXPECTS = ["Amount shown", "Card listed"]

    def states(self, *checks):
        return [(x["id"], x["state"]) for x in coverage.expectation_states(self.EXPECTS, list(checks))]

    def test_cov_3_a_passing_linked_check_verifies_its_expected_result(self):
        [e1, _] = coverage.expectation_states(self.EXPECTS, [ev_check(True, ["E1"], what="₹500 shown")])
        self.assertEqual((e1["id"], e1["text"], e1["state"], e1["by"]), ("E1", "Amount shown", "verified", ["₹500 shown"]))

    def test_cov_4_one_failed_linked_check_fails_it_even_if_another_passed(self):
        self.assertEqual(self.states(ev_check(True, ["E1"]), ev_check(False, ["E1"]), ev_check(True, ["E2"])),
                         [("E1", "failed"), ("E2", "verified")])

    def test_cov_5_no_linked_check_leaves_it_unverified(self):
        self.assertEqual(self.states(ev_check(True, ["E1"]), ev_check(True)),
                         [("E1", "verified"), ("E2", "unverified")])

    def test_cov_6_a_check_naming_the_same_expected_result_twice_counts_once(self):
        [e1, _] = coverage.expectation_states(self.EXPECTS, [ev_check(True, ["E1", "E1"], what="x")])
        self.assertEqual(e1["by"], ["x"])


class LedgerTest(unittest.TestCase):
    def test_cov_7_not_started_lists_requested_cases_that_never_began_or_were_closed(self):
        events = [ev_plan("TC-01"), ev_plan("TC-02", "Two"), ev_plan("TC-03"), ev_start("TC-01"),
                  {"type": "not_run", "case": "TC-03", "title": "", "reason": "x"}]
        self.assertEqual([c["id"] for c in coverage.not_started(events)], ["TC-02"])

    def test_cov_8_verified_counts_definite_answers_and_green_needs_every_case_passed(self):
        t = coverage.tally(["pass", "fail", "blocked", "not_run"], checks_failed=1)
        self.assertEqual((t["requested"], t["verified"], t["complete"], t["green"]), (4, 2, False, False))
        t = coverage.tally(["pass", "fail"], checks_failed=1)
        self.assertEqual((t["verified"], t["complete"], t["green"]), (2, True, False))
        t = coverage.tally(["pass", "pass"], checks_failed=0)
        self.assertEqual((t["verified"], t["complete"], t["green"]), (2, True, True))

    def test_cov_8_skipped_or_pending_is_not_complete(self):
        self.assertFalse(coverage.tally(["pass", "skipped"], checks_failed=0)["green"])
        self.assertFalse(coverage.tally(["pass", "pending"], checks_failed=0)["complete"])

    def test_r6_a_failed_check_outside_any_case_is_never_green(self):
        self.assertFalse(coverage.tally(["pass", "pass"], checks_failed=1)["green"])


EXPECTS_PLAN = """app com.example.app
case TC-04 "Pay with saved card"
  expect "Confirmation screen shows the amount"
  expect "Card ending 4242 is listed"
  if "Saved cards"
    check "4242" --for E2
  end
  tap Pay
  check "500" --for E1
  check id=loader --gone
"""


def parse_err(text: str) -> str:
    try:
        plan.parse(text, "x.plan")
    except plan.PlanError as exc:
        return str(exc)
    raise AssertionError("expected a PlanError")


class PlanExpectTest(unittest.TestCase):
    def test_plan_1_expected_results_are_read_in_order(self):
        p = plan.parse(EXPECTS_PLAN, "x.plan")
        self.assertEqual(p.cases[0].expects, ["Confirmation screen shows the amount", "Card ending 4242 is listed"])

    def test_plan_1_a_case_without_expect_has_none(self):
        self.assertEqual(plan.parse("app a.b\ncase T1\n  check X\n", "x.plan").cases[0].expects, [])

    def test_plan_2_expect_after_a_step_is_refused(self):
        msg = parse_err('app a.b\ncase T1\n  tap X\n  expect "late"\n  check X --for E1\n')
        self.assertIn("line 4", msg)
        self.assertIn("expect", msg)

    def test_plan_3_expect_only_belongs_to_a_case(self):
        for text in ('app a.b\nflow f\n  expect "x"\nend\ncase T1\n  check X\n',
                     'app a.b\ncase T1\n  if X\n    expect "x"\n  end\n  check X\n',
                     'app a.b\nexpect "x"\ncase T1\n  check X\n'):
            self.assertIn("expect", parse_err(text))

    def test_plan_4_an_expected_result_with_no_linked_check_is_refused(self):
        msg = parse_err('app a.b\ncase T1\n  expect "One"\n  expect "Two things"\n  check X --for E1\n')
        self.assertIn("E2", msg)
        self.assertIn("Two things", msg)
        self.assertIn("line 4", msg)

    def test_plan_5_for_must_name_an_expected_result_of_its_case(self):
        self.assertIn("E9", parse_err('app a.b\ncase T1\n  expect "One"\n  check X --for E1\n  check Y --for E9\n'))
        self.assertIn("E1", parse_err('app a.b\ncase T1\n  check X --for E1\n'))

    def test_plan_6_a_flow_cannot_link_expected_results(self):
        msg = parse_err('app a.b\nflow f\n  check X --for E1\nend\ncase T1\n  expect "One"\n  run f\n  check X --for E1\n')
        self.assertIn("line 3", msg)
        self.assertIn("flow", msg)

    def test_plan_7_a_linked_check_inside_if_or_else_counts(self):
        p = plan.parse('app a.b\ncase T1\n  expect "One"\n  expect "Two"\n  if X\n    check X --for E1\n'
                       '  else\n    check Y --for E2\n  end\n', "x.plan")
        self.assertEqual(len(p.cases[0].expects), 2)

    def test_plan_8_a_malformed_for_is_refused(self):
        for bad in ("X1", "E0", "E1,", "e1"):
            self.assertIn("line 4", parse_err(f'app a.b\ncase T1\n  expect "One"\n  check X --for {bad}\n'))

    def test_plan_9_expect_needs_exactly_one_non_empty_text(self):
        self.assertIn("expect", parse_err('app a.b\ncase T1\n  expect ""\n  check X\n'))
        self.assertIn("expect", parse_err('app a.b\ncase T1\n  expect "a" "b"\n  check X\n'))


import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from fakedevice import PACKAGE, tnazar  # noqa: E402,F401
from test_cli import CliTestCase  # noqa: E402
from teesri_nazar.session import get_active  # noqa: E402


class CaseCliBase(CliTestCase):
    def events(self, type_=None):
        evs = get_active()[0].events() if get_active() else self.finished.events()
        return [e for e in evs if type_ is None or e["type"] == type_]

    def fails(self, *argv, code=2):
        c, out, err = tnazar(*argv)
        self.assertEqual(c, code, out + err)
        return out + err


class CasePlanTest(CaseCliBase):
    def test_cli_1_case_plan_declares_a_case_with_numbered_expected_results(self):
        self.start()
        out = self.ok("case", "plan", "TC-02", "Wrong MPIN", "--expect", "Error text shown", "--expect", "Stays on MPIN")
        self.assertIn("E1 Error text shown", out)
        self.assertIn("E2 Stays on MPIN", out)
        [e] = self.events("case_plan")
        self.assertEqual((e["case"], e["title"], e["expects"], e["added"]),
                         ("TC-02", "Wrong MPIN", ["Error text shown", "Stays on MPIN"], False))

    def test_cli_2_case_plan_needs_an_active_run(self):
        self.assertIn("active run", self.fails("case", "plan", "TC-01"))

    def test_cli_7_case_plan_does_not_end_the_case_in_progress(self):
        self.start()
        self.ok("case", "start", "TC-01")
        self.ok("case", "plan", "TC-02")
        self.assertEqual(get_active()[0].current_case(), "TC-01")
        self.assertEqual(self.events("case_end"), [])


class CheckForTest(CaseCliBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.ok("case", "plan", "TC-02", "Wrong MPIN", "--expect", "Login shown", "--expect", "Phone field shown")
        self.ok("case", "start", "TC-02")

    def test_cli_3_for_an_expected_result_the_case_does_not_have_records_nothing(self):
        self.assertIn("E3", self.fails("check", "Login", "--for", "E3"))
        self.assertEqual(self.events("check"), [])

    def test_cli_4_for_needs_a_case_in_progress(self):
        self.ok("case", "end", "skipped")
        self.assertIn("case", self.fails("check", "Login", "--for", "E1"))
        self.assertEqual(self.events("check"), [])

    def test_cli_5_ui_and_agent_verified_checks_record_what_they_prove(self):
        self.ok("check", "Login", "--for", "E1")
        self.ok("check", "--result", "pass", "--kind", "db", "--what", "attempts = 1", "--for", "E2,E1")
        self.assertEqual([e["for"] for e in self.events("check")], [["E1"], ["E2", "E1"]])

    def test_a_check_without_for_records_no_link(self):
        self.ok("check", "Login")
        self.assertNotIn("for", self.events("check")[0])


class UndeclaredStartTest(CaseCliBase):
    def test_cli_6_starting_an_unplanned_case_in_a_declared_run_adds_it(self):
        self.start()
        self.ok("case", "plan", "TC-01")
        self.ok("case", "start", "TC-09", "Found while testing")
        plans = self.events("case_plan")
        self.assertEqual([(e["case"], e["added"]) for e in plans], [("TC-01", False), ("TC-09", True)])
        self.assertEqual(plans[1]["title"], "Found while testing")
        types = [e["type"] for e in self.events()]
        last_plan = max(i for i, t in enumerate(types) if t == "case_plan")
        self.assertLess(last_plan, types.index("case_start"))

    def test_an_undeclared_run_records_no_case_plan(self):
        self.start()
        self.ok("case", "start", "TC-01")
        self.assertEqual(self.events("case_plan"), [])

    def test_restarting_a_planned_case_adds_nothing(self):
        self.start()
        self.ok("case", "plan", "TC-01")
        self.ok("case", "start", "TC-01")
        self.assertEqual(len(self.events("case_plan")), 1)


class CaseResultTest(CaseCliBase):
    """The result of a case with expected results (Q9, R4)."""

    def setUp(self):
        super().setUp()
        self.run = self.start()
        self.ok("case", "plan", "TC-02", "Login screen", "--expect", "Login shown", "--expect", "Card 4242 listed")
        self.ok("case", "start", "TC-02")

    def test_cli_8_an_unproven_expected_result_blocks_the_case_and_names_it(self):
        self.ok("check", "Login", "--for", "E1")
        self.ok("case", "end")
        e = self.case_end_event(self.run, "TC-02")
        self.assertEqual(e["status"], "blocked")
        self.assertIn("not verified: E2 Card 4242 listed", e["reason"])
        self.assertEqual([(x["id"], x["state"]) for x in e["expects"]], [("E1", "verified"), ("E2", "unverified")])

    def test_every_expected_result_proven_passes(self):
        self.ok("check", "Login", "--for", "E1,E2")
        self.ok("case", "end")
        e = self.case_end_event(self.run, "TC-02")
        self.assertEqual((e["status"], e["reason"]), ("pass", ""))

    def test_cli_9_a_tester_pass_with_an_unproven_expected_result_is_blocked(self):
        self.ok("check", "Login", "--for", "E1")
        self.ok("case", "end", "pass")
        e = self.case_end_event(self.run, "TC-02")
        self.assertEqual(e["status"], "blocked")
        self.assertIn("marked pass by the tester", e["reason"])
        self.assertIn("not verified: E2", e["reason"])

    def test_cli_10_a_failed_linked_check_fails_the_case(self):
        self.ok("check", "Login", "--for", "E1")
        tnazar("check", "Nowhere", "--for", "E2", "--timeout", "1")
        self.ok("case", "end")
        e = self.case_end_event(self.run, "TC-02")
        self.assertEqual(e["status"], "fail")
        self.assertEqual([x["state"] for x in e["expects"]], ["verified", "failed"])

    def test_cli_10_a_crash_fails_even_when_every_expected_result_is_proven(self):
        self.ok("check", "Login", "--for", "E1,E2")
        self.adb.crash_log = f"FATAL EXCEPTION: main\nProcess: {PACKAGE}, PID: 4242\n"
        self.ok("case", "end")
        self.assertEqual(self.case_end_event(self.run, "TC-02")["status"], "fail")

    def test_cli_13_an_auto_ended_case_still_names_the_unproven_expected_result(self):
        self.ok("check", "Login", "--for", "E1")
        self.ok("case", "start", "TC-03")
        e = self.case_end_event(self.run, "TC-02")
        self.assertEqual(e["status"], "blocked")
        self.assertIn("next case started", e["reason"])
        self.assertIn("not verified: E2", e["reason"])

    def test_explicit_fail_or_skip_by_the_tester_is_kept(self):
        self.ok("case", "end", "skipped", "--reason", "out of scope")
        e = self.case_end_event(self.run, "TC-02")
        self.assertEqual(e["status"], "skipped")
        self.assertEqual(len(e["expects"]), 2)


if __name__ == "__main__":
    unittest.main()
