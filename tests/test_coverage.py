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
        self.assertEqual(md.replace(rpt.UNDECLARED + "\n\n", "", 1), (GOLDEN / "legacy_report.md").read_text())

    def test_ph0_2_html_report_is_unchanged(self):
        run = legacy_run()
        page = rpt.to_html(run, rpt.summarize(run))
        # The only allowed additions (PRD Q6): the completeness line and the style for not-run rows.
        page = page.replace(f"<p class=\"muted\">{rpt.UNDECLARED}</p>\n", "", 1).replace(rpt.CSS_COVERAGE, "", 1)
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

    def test_plan_5_an_abbreviated_for_is_validated_too(self):
        self.assertIn("E9", parse_err('app a.b\ncase T1\n  expect "One"\n  check X --for E1\n  check Y --fo E9\n'))

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


class NotRunTest(CaseCliBase):
    """Declared cases that never started are rows with a reason when the run ends (Q4, R3)."""

    def declare_and_run(self, declared=8, ran=5):
        run = self.start()
        for i in range(1, declared + 1):
            self.ok("case", "plan", f"TC-0{i}")
        for i in range(1, ran + 1):
            self.ok("case", "start", f"TC-0{i}")
            self.ok("check", "Login")
            self.ok("case", "end")
        return run

    def not_run(self, run):
        return [(e["case"], e["reason"]) for e in run.events() if e["type"] == "not_run"]

    def test_cli_11_stop_writes_a_not_run_row_per_unstarted_case(self):
        run = self.declare_and_run()
        out = self.ok("stop")
        self.assertEqual([c for c, _ in self.not_run(run)], ["TC-06", "TC-07", "TC-08"])
        self.assertTrue(all("tnazar stop" in r for _, r in self.not_run(run)))
        self.assertIn("○ 3 not run", out)
        self.assertIn("verified 5/8", out)

    def test_cli_12_a_device_error_while_stopping_still_closes_the_case_and_writes_not_run(self):
        from teesri_nazar import cli
        from teesri_nazar.adb import AdbError
        run = self.declare_and_run(declared=3, ran=1)
        self.ok("case", "start", "TC-02")
        with mock.patch.object(cli.dev, "release_device", side_effect=AdbError("device gone")):
            self.ok("stop")
        self.assertEqual(self.case_end_event(run, "TC-02")["status"], "blocked")
        self.assertEqual([c for c, _ in self.not_run(run)], ["TC-03"])

    def test_cli_15_a_new_start_closes_the_old_run_with_its_own_reason(self):
        run = self.declare_and_run(declared=2, ran=1)
        self.ok("start", "next", "--package", PACKAGE)
        self.assertEqual(self.not_run(run), [("TC-02", "run closed by a new tnazar start")])

    def test_stop_on_an_undeclared_run_writes_no_not_run_row(self):
        run = self.start()
        self.ok("case", "start", "TC-01")
        self.ok("check", "Login")
        self.ok("case", "end")
        out = self.ok("stop")
        self.assertEqual(self.not_run(run), [])
        self.assertIn("1 case(s): ✅ 1 pass  | checks 1/1 passed", out)


def declared_run(finished=True) -> Run:
    """A declared run: TC-01 pass (E1 proven), TC-02 fail, TC-03 blocked (E1 unproven), TC-04 never ran."""
    run = Run.create("declared", {"package": "com.example.app", "started_at": "2026-10-21T10:00:00.000Z"})
    for e in [ev_plan("TC-01", "Login", ["Home shown"]), ev_plan("TC-02", "Wrong MPIN"),
              ev_plan("TC-03", "Pay", ["Amount <b>shown</b>"]), ev_plan("TC-04", "Logout"),
              ev_start("TC-01"), ev_check(True, ["E1"], "TC-01", what="Hi Asha visible"),
              {**ev_end("TC-01", "pass"), "expects": [{"id": "E1", "text": "Home shown", "state": "verified",
                                                        "by": ["Hi Asha visible"]}]},
              ev_start("TC-02", "Wrong MPIN"), ev_check(False, None, "TC-02"), ev_end("TC-02", "fail", "failed: x"),
              ev_start("TC-03"),
              {**ev_end("TC-03", "blocked", "not verified: E1 Amount <b>shown</b>"),
               "expects": [{"id": "E1", "text": "Amount <b>shown</b>", "state": "unverified", "by": []}]}]:
        run.log(e)
    if finished:
        run.log({"type": "not_run", "case": "TC-04", "title": "Logout", "reason": "--stop-on-fail after TC-03"})
        run.update_meta(finished_at="2026-10-21T10:01:00.000Z")
    return run


class ReportCoverageTest(unittest.TestCase):
    def setUp(self):
        temp_home(self)
        no_transcripts = mock.patch.object(usage, "transcripts", return_value=[])
        no_transcripts.start()
        self.addCleanup(no_transcripts.stop)

    def test_rep_1_the_declared_summary_line_is_todays_line_plus_additions(self):
        line = rpt.summary_line(rpt.summarize(declared_run()))
        self.assertEqual(line, "4 case(s): ✅ 1 pass  ❌ 1 fail  ⛔ 1 blocked  ○ 1 not run  "
                               "| checks 1/2 passed  | verified 2/4")

    def test_rep_1_a_live_run_shows_unstarted_cases_as_pending(self):
        s = rpt.summarize(declared_run(finished=False))
        self.assertIn("◌ 1 pending", rpt.summary_line(s))
        self.assertEqual([c["status"] for c in s["cases"]][-1], "pending")

    def test_not_run_rows_come_with_their_reason_and_title(self):
        rows = {c["id"]: c for c in rpt.summarize(declared_run())["cases"]}
        self.assertEqual((rows["TC-04"]["status"], rows["TC-04"]["title"], rows["TC-04"]["reason"]),
                         ("not_run", "Logout", "--stop-on-fail after TC-03"))

    def test_cli_14_a_case_started_without_a_title_takes_the_planned_one(self):
        rows = {c["id"]: c for c in rpt.summarize(declared_run())["cases"]}
        self.assertEqual(rows["TC-01"]["title"], "Login")

    def test_rep_2_result_line(self):
        md = rpt.to_markdown(declared_run(), rpt.summarize(declared_run()))
        self.assertIn("**Result:** incomplete", md)

    def test_rep_2_result_failed_and_all_passed(self):
        run = Run.create("r", {"package": "p"})
        for e in [ev_plan("A"), ev_plan("B"), ev_start("A"), ev_check(True, None, "A"), ev_end("A", "pass"),
                  ev_start("B"), ev_check(False, None, "B"), ev_end("B", "fail")]:
            run.log(e)
        self.assertIn("**Result:** failed", rpt.to_markdown(run, rpt.summarize(run)))
        run2 = Run.create("r2", {"package": "p"})
        for e in [ev_plan("A"), ev_start("A"), ev_check(True, None, "A"), ev_end("A", "pass")]:
            run2.log(e)
        self.assertIn("**Result:** all passed", rpt.to_markdown(run2, rpt.summarize(run2)))

    def test_rep_7_a_failed_check_outside_any_case_is_not_all_passed(self):
        run = Run.create("r", {"package": "p"})
        for e in [ev_plan("A"), ev_start("A"), ev_check(True, None, "A"), ev_end("A", "pass"),
                  ev_check(False, None, None, what="visual match")]:
            run.log(e)
        self.assertIn("**Result:** failed", rpt.to_markdown(run, rpt.summarize(run)))

    def test_rep_3_each_case_lists_its_expected_results(self):
        md = rpt.to_markdown(declared_run(), rpt.summarize(declared_run()))
        self.assertIn("- ✅ E1 Home shown — proven by: Hi Asha visible", md)
        self.assertIn("- ○ E1 Amount <b>shown</b> — not verified", md)

    def test_rep_5_html_shows_the_same_and_escapes_it(self):
        run = declared_run()
        page = rpt.to_html(run, rpt.summarize(run))
        self.assertIn("Amount &lt;b&gt;shown&lt;/b&gt;", page)
        self.assertNotIn("Amount <b>shown</b>", page)
        self.assertIn("verified 2/4", page)
        self.assertIn("incomplete", page)
        self.assertIn("pill not_run", page)
        self.assertIn("--stop-on-fail after TC-03", page)

    def test_rep_4_an_undeclared_report_only_gains_the_completeness_line(self):
        run = legacy_run()
        md = rpt.to_markdown(run, rpt.summarize(run))
        self.assertIn(rpt.UNDECLARED, md)
        self.assertEqual(md.replace(rpt.UNDECLARED + "\n\n", "", 1), (GOLDEN / "legacy_report.md").read_text())


from test_plan import LOGIN, PlanRunBase  # noqa: E402
from teesri_nazar import live  # noqa: E402

TEN = "".join(f'case TC-{i:02d} "case {i}"\n  check Login\n' for i in range(1, 11))


class PlanRunCoverageTest(PlanRunBase):
    def statuses(self, run):
        return [(c["id"], c["status"]) for c in rpt.summarize(run)["cases"]]

    def not_run(self, run):
        return {e["case"]: e["reason"] for e in run.events() if e["type"] == "not_run"}

    def test_run_1_an_unexpected_error_leaves_every_later_case_as_not_run(self):
        from teesri_nazar import runner
        from teesri_nazar.adb import AdbError
        body = TEN.replace('case TC-03 "case 3"\n  check Login\n',
                           'case TC-03 "case 3"\n  if Login\n    key back\n  end\n  check Login\n')
        with mock.patch.object(runner.Executor, "visible", side_effect=AdbError("device offline")):
            out, run = self.run_plan(body, expect=2)
        st = dict(self.statuses(run))
        self.assertEqual((st["TC-01"], st["TC-02"], st["TC-03"]), ("pass", "pass", "blocked"))
        self.assertEqual(sorted(self.not_run(run)), [f"TC-{i:02d}" for i in range(4, 11)])
        self.assertTrue(all(r.startswith("run stopped at TC-03: error:") for r in self.not_run(run).values()))
        self.assertEqual(len(self.statuses(run)), 10)

    def test_run_2_stop_on_fail_leaves_the_rest_as_not_run(self):
        body = TEN.replace('case TC-02 "case 2"\n  check Login\n', 'case TC-02 "case 2"\n  check Nowhere --timeout 1\n')
        out, run = self.run_plan(body, "--stop-on-fail", expect=1)
        self.assertEqual(set(self.not_run(run).values()), {"--stop-on-fail after TC-02"})
        self.assertEqual(len(self.not_run(run)), 8)
        self.assertIn("○ 8 not run", out)  # run_8: the line survives the runner's output filter
        self.assertIn("verified 2/10", out)

    def _hand_back_at_tc04(self, path):
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main
        posted = []

        def main(argv):
            if argv[:2] == ["say", "four"] and not posted:
                posted.append(1)
                live.post("stop here")
            return real_main(argv)
        with mock.patch("teesri_nazar.cli.main", main):
            code, _, _ = tnazar("run", path)
        self.assertEqual(code, live.EXIT_HELD)

    def write_plan(self, body):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LOGIN + body)
        return path

    def test_run_3_a_handed_back_run_that_is_then_stopped_lists_the_rest_as_not_run(self):
        path = self.write_plan(TEN.replace('case TC-04 "case 4"\n', 'case TC-04 "case 4"\n  say four\n'))
        self._hand_back_at_tc04(path)
        run = get_active()[0]
        self.assertEqual(self.not_run(run), {})
        self.ok("stop")
        self.assertEqual(sorted(self.not_run(run)), [f"TC-{i:02d}" for i in range(5, 11)])
        self.assertIn("tnazar stop", self.not_run(run)["TC-05"])

    def test_run_4_continuing_with_from_runs_every_case_and_keeps_the_order(self):
        path = self.write_plan(TEN.replace('case TC-04 "case 4"\n', 'case TC-04 "case 4"\n  say four\n'))
        self._hand_back_at_tc04(path)
        code, out, err = tnazar("run", path, "--from", "TC-04")
        self.assertEqual(code, 0, out + err)
        run = Run.load("demo")
        self.assertEqual(self.not_run(run), {})
        self.assertEqual([c["id"] for c in coverage.requested(run.events())], [f"TC-{i:02d}" for i in range(1, 11)])
        self.assertTrue(rpt.summarize(run)["tally"]["green"])
        self.assertNotIn("selected", rpt.to_markdown(run, rpt.summarize(run)))  # run_9: all cases, no header

    def test_run_9_only_then_from_names_both_selections(self):
        path = self.write_plan(TEN.replace('case TC-05 "case 5"\n', 'case TC-05 "case 5"\n  say four\n'))
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main
        posted = []

        def main(argv):
            if argv[:2] == ["say", "four"] and not posted:
                posted.append(1)
                live.post("stop here")
            return real_main(argv)
        with mock.patch("teesri_nazar.cli.main", main):
            tnazar("run", path, "--only", "TC-05")
        tnazar("run", path, "--from", "TC-05")
        run = Run.load("demo")
        md = rpt.to_markdown(run, rpt.summarize(run))
        self.assertIn("6 of 10 plan cases selected (--only TC-05, then --from TC-05)", md)

    def test_run_5_only_requests_the_selected_case_and_says_so(self):
        out, run = self.run_plan(TEN, "--only", "TC-05")
        self.assertEqual([c["id"] for c in coverage.requested(run.events())], ["TC-05"])
        self.assertIn("1 of 10 plan cases selected (--only TC-05)", rpt.to_markdown(run, rpt.summarize(run)))
        self.assertIn("verified 1/1", out)

    def test_run_6_an_expected_result_whose_check_did_not_run_blocks_the_case(self):
        out, run = self.run_plan('''
case TC-01 "Login"
  expect "Home shows the user"
  expect "Pay button shown"
  run login
  check "Hi Asha" --for E1
  if "Nowhere"
    check "Pay now" --for E2
  end
''', expect=1)
        end = [e for e in run.events() if e["type"] == "case_end"][0]
        self.assertEqual(end["status"], "blocked")
        self.assertIn("not verified: E2 Pay button shown", end["reason"])

    def test_run_7_every_expected_result_proven_is_all_passed(self):
        out, run = self.run_plan('''
case TC-01 "Login"
  expect "Home shows the user"
  run login
  check "Hi Asha" --for E1
''')
        self.assertIn("**Result:** all passed", rpt.to_markdown(run, rpt.summarize(run)))


class PreconditionBlockTest(PlanRunBase):
    """S7: a shared flow that fails twice blocks the rest of the cases that need it, at once."""

    BROKEN = """
flow enter
  tap "Nowhere"
end
""" + "".join(f'case TC-{i:02d} "uses the broken flow"\n  run enter\n  check Login\n' for i in range(1, 5)) + \
        'case TC-05 "does not need it"\n  check Login\n'

    def case_events(self, run, case):
        return [e["type"] for e in run.events() if e.get("case") == case and e["type"] != "case_plan"]

    def test_two_failures_block_the_remaining_cases_without_touching_the_device(self):
        out, run = self.run_plan(self.BROKEN, expect=1)
        st = {c["id"]: (c["status"], c["reason"]) for c in rpt.summarize(run)["cases"]}
        self.assertEqual((st["TC-01"][0], st["TC-02"][0]), ("fail", "fail"))
        for cid in ("TC-03", "TC-04"):
            self.assertEqual(st[cid], ("blocked", "precondition failed: flow enter (failed in TC-01, TC-02)"))
            self.assertEqual(self.case_events(run, cid), ["case_start", "case_end"])
        self.assertEqual(st["TC-05"][0], "pass")
        self.assertIn("TC-03    BLOCKED  precondition failed: flow enter", out)
        self.assertIn("verified 3/5", out)

    def test_a_flow_that_fails_once_then_works_blocks_nothing(self):
        self.adb.drop_taps = 1
        out, run = self.run_plan('''
flow enter
  tap Login
  wait "Pay now" --timeout 1
end
case TC-01 "first try loses the tap"
  run enter
  check "Pay now"
case TC-02 "works"
  key back
  run enter
  check "Pay now"
case TC-03 "works"
  key back
  run enter
  check "Pay now"
''', expect=1)
        st = {c["id"]: c["status"] for c in rpt.summarize(run)["cases"]}
        self.assertEqual(st, {"TC-01": "fail", "TC-02": "pass", "TC-03": "pass"})

    def test_a_blocked_case_keeps_its_expected_results_unverified(self):
        body = self.BROKEN.replace('case TC-03 "uses the broken flow"\n',
                                   'case TC-03 "uses the broken flow"\n  expect "Login shown"\n') \
                          .replace('case TC-03 "uses the broken flow"\n  expect "Login shown"\n  run enter\n  check Login\n',
                                   'case TC-03 "uses the broken flow"\n  expect "Login shown"\n  run enter\n  check Login --for E1\n')
        _, run = self.run_plan(body, expect=1)
        end = [e for e in run.events() if e["type"] == "case_end" and e["case"] == "TC-03"][0]
        self.assertEqual([x["state"] for x in end["expects"]], ["unverified"])


from test_replay import ReplayTestCase  # noqa: E402


class ReplayCoverageTest(ReplayTestCase):
    def record_declared(self):
        """`demo`: TC-01 proves E1 on screen and E2 only in the DB; TC-02 checks home; TC-03 never ran."""
        self.ok("start", "demo", "--package", PACKAGE)
        self.ok("case", "plan", "TC-01", "Login", "--expect", "Home shown", "--expect", "Login audited")
        self.ok("case", "plan", "TC-02", "Balance")
        self.ok("case", "plan", "TC-03", "Logout")
        self.ok("case", "start", "TC-01")
        self.ok("type", "9876543210", "--into", "id=phone_input")
        self.ok("tap", "Login")
        self.ok("check", "Hi Asha", "--for", "E1")
        self.ok("check", "--result", "pass", "--kind", "db", "--what", "audit row written", "--for", "E2")
        self.ok("case", "end")
        self.ok("case", "start", "TC-02")
        self.ok("check", "id=balance")
        self.ok("case", "end")
        self.ok("stop")
        self.fresh_device()

    def test_rpl_1_the_replay_requests_the_same_cases(self):
        self.record_declared()
        _, _, run = self.replay()
        self.assertEqual([c["id"] for c in coverage.requested(run.events())], ["TC-01", "TC-02", "TC-03"])

    def test_rpl_2_and_3_an_expected_result_proven_only_in_the_db_is_not_green_on_replay(self):
        self.record_declared()
        code, _, run = self.replay()
        self.assertEqual(code, 1)
        end = [e for e in run.events() if e["type"] == "case_end" and e["case"] == "TC-01"][0]
        self.assertEqual(end["status"], "blocked")
        self.assertIn("not verified: E2 Login audited", end["reason"])
        self.assertEqual([x["state"] for x in end["expects"]], ["verified", "unverified"])
        notes = [e["text"] for e in run.events() if e["type"] == "note"]
        self.assertIn("[db] not re-verified on replay: audit row written", notes)

    def test_rpl_4_a_case_that_never_ran_is_not_run_in_the_replay_too(self):
        self.record_declared()
        _, out, run = self.replay()
        rows = {c["id"]: c for c in rpt.summarize(run)["cases"]}
        self.assertEqual((rows["TC-03"]["status"], rows["TC-03"]["reason"]), ("not_run", "not run in the recorded run"))
        self.assertIn("verified 1/3", out)

    def test_rpl_5_an_undeclared_run_replays_exactly_as_before(self):
        self.record_login()
        self.fresh_device()
        code, out, run = self.replay()
        self.assertEqual(code, 0, out)
        types = {e["type"] for e in run.events()}
        self.assertFalse(types & {"case_plan", "not_run"})
        self.assertIn("2 case(s): ✅ 2 pass  | checks 2/2 passed", out)
        self.assertNotIn("verified", out)

    def test_rpl_6_new_events_add_no_steps_and_no_waits(self):
        self.record_declared()
        sleeps = []
        from teesri_nazar import replay
        real = replay.time.sleep
        with mock.patch.object(replay.time, "sleep", side_effect=lambda s: (sleeps.append(s), real(s))):
            _, out, _ = self.replay()
        self.assertEqual(len(sleeps), 2)  # one settle per recorded action: the type and the tap
        self.assertNotIn("step 3", out)

    def test_rpl_7_a_step_that_raises_still_writes_not_run(self):
        self.record_declared()
        from teesri_nazar import replay
        with mock.patch.object(replay, "_do", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                tnazar("replay", "demo")
        runs = sorted(p.name for p in Run.load("demo").path.parent.iterdir() if "-replay-" in p.name)
        run = Run.load(runs[-1])
        self.assertEqual({e["case"] for e in run.events() if e["type"] == "not_run"}, {"TC-02", "TC-03"})
        end = [e for e in run.events() if e["type"] == "case_end" and e["case"] == "TC-01"][0]
        self.assertEqual([x["state"] for x in end["expects"]], ["unverified", "unverified"])


class OtherViewsTest(CaseCliBase):
    """REP-6 / UI-4: every place that prints the summary line shows the declared one."""

    def declared(self):
        run = self.start()
        self.ok("case", "plan", "TC-01")
        self.ok("case", "plan", "TC-02")
        self.ok("case", "start", "TC-01")
        self.ok("check", "Login")
        self.ok("case", "end")
        return run

    def test_ui_4_the_dashboard_summary_shows_pending_cases_live(self):
        self.declared()
        summary = ui.Dashboard(0).state()["active"]["summary"]
        self.assertIn("◌ 1 pending", summary)
        self.assertIn("verified 1/2", summary)

    def test_rep_6_runs_and_report_print_the_declared_line(self):
        run = self.declared()
        self.ok("stop")
        self.assertIn("○ 1 not run", self.ok("runs"))
        self.assertIn("verified 1/2", self.ok("report", run.name))


DRAFT = """app com.example.app
case TC-01 "Login works"
  expect "Home shows the user"
  expect "Balance shown"
  run login
  check "Hi Asha" --for E1
case TC-02 "Not written yet"
  expect "Error shown"
case TC-03 "Has steps, verifies nothing"
  tap Login
flow login
  tap Login
end
"""


class PlanReviewTest(CliTestCase):
    """S3: every case, its expected results and their proving checks, and all gaps at once."""

    def write(self, text):
        path = os.path.join(self.home, "draft.plan")
        Path(path).write_text(text)
        return path

    def test_a_draft_lists_every_gap_at_once(self):
        p = plan.parse(DRAFT, "draft.plan", draft=True)
        text = "\n".join(p.gaps)
        self.assertIn("draft.plan line 7: case TC-02 has no steps", text)
        self.assertIn("draft.plan line 4: case TC-01: E2", text)
        self.assertIn("draft.plan line 9: case TC-03 has no `check`", text)
        self.assertIn('draft.plan line 8: case TC-02: E1 "Error shown"', text)
        self.assertEqual(len(p.gaps), 4)

    def test_run_still_stops_at_the_first_gap_with_todays_message(self):
        with self.assertRaises(plan.PlanError) as ctx:
            plan.parse(DRAFT, "draft.plan")
        self.assertEqual(str(ctx.exception), "draft.plan line 7: case TC-02 has no steps")
        code, _, err = tnazar("run", self.write(DRAFT))
        self.assertEqual(code, 2)
        self.assertIn("case TC-02 has no steps", err)

    def test_review_shows_each_expected_result_and_what_proves_it(self):
        code, out, _ = tnazar("plan", "review", self.write(DRAFT))
        self.assertEqual(code, 1)
        self.assertIn("TC-01  Login works", out)
        self.assertIn('E1 Home shows the user  <- line 6: check "Hi Asha"', out)
        self.assertIn("E2 Balance shown  <- NOT PROVEN", out)
        self.assertIn("4 gap(s): not ready to run", out)

    def test_review_of_a_ready_plan_exits_0(self):
        ready = (DRAFT.split('case TC-02')[0].replace('  expect "Balance shown"\n', "")
                 + "flow login\n  tap Login\nend\n")
        code, out, _ = tnazar("plan", "review", self.write(ready))
        self.assertEqual(code, 0, out)
        self.assertIn("ready to run: 1 case(s), 1 expected result(s), every one proven", out)

    def test_review_of_a_broken_plan_is_a_plan_error(self):
        code, _, err = tnazar("plan", "review", self.write("app a.b\ncase T1\n  teleport\n"))
        self.assertEqual(code, 2)
        self.assertIn("line 3", err)


LONG = ("Your repayment due date is the 7th of every month. A late fee applies 3 days after the due date.\n\n"
        "It is slab-based on the Total Amount Due, plus GST.")


def chat_app():
    from fakedevice import FakeApp, node
    return FakeApp(screens={"chat": [node(id="text", text=LONG, clickable=False, bounds=(60, 400, 1020, 900)),
                                     node(id="otp", text="4321", clickable=False, bounds=(60, 950, 1020, 1000))]},
                   start="chat")


class ObserveFullTest(CliTestCase):
    """S9: the whole text of an element, never cut; checks already match it."""
    app = staticmethod(chat_app)

    def test_observe_cuts_long_text_as_before(self):
        out = self.ok("observe")
        self.assertIn("…", out)
        self.assertNotIn("plus GST", out)

    def test_observe_full_prints_every_word_and_keeps_line_breaks(self):
        out = self.ok("observe", "--full")
        self.assertIn("A late fee applies 3 days after the due date.", out)
        self.assertIn("It is slab-based on the Total Amount Due, plus GST.", out)
        self.assertNotIn("…", out)
        self.assertGreater(out.count("\n"), 3)

    def test_observe_full_still_hides_secrets(self):
        Path(self.home, ".env").write_text("APP_OTP=4321\n")
        with mock.patch.dict(os.environ, {}):
            out = self.ok("observe", "--full")
        self.assertNotIn("4321", out)

    def test_a_partial_check_matches_words_beyond_the_cut(self):
        self.start()
        self.ok("check", "~slab-based on the Total Amount Due, plus GST")


class AddedDuringRunReportTest(CaseCliBase):
    def test_q12_a_case_added_during_the_run_is_marked_in_the_report(self):
        run = self.start()
        self.ok("case", "plan", "TC-01")
        self.ok("case", "start", "TC-09", "Found while testing")
        self.ok("check", "Login")
        self.ok("case", "end")
        self.ok("stop")
        md = (run.path / "report.md").read_text()
        self.assertIn("| TC-09 | Found while testing (added during run) |", md)


@unittest.skipUnless(shutil.which("node"), "needs node to run the dashboard's describe() and layout()")
class DashboardCoverageTest(unittest.TestCase):
    """DA-14: every requested case shows once the run ends; not-run ones as grey rows with their reason."""

    def test_not_run_cases_are_rows_with_their_reason(self):
        line = node_eval('describe({type: "not_run", case: "TC-07", title: "Logout", reason: "--stop-on-fail after TC-03"})')
        self.assertEqual(line, ["○", "TC-07 Logout: not run", "--stop-on-fail after TC-03"])

    def test_layout_gives_not_run_its_own_block(self):
        events = [{"type": "say", "text": "last step"}, {"type": "tap", "label": "x"},
                  {"type": "not_run", "case": "TC-07", "reason": "r"}, {"type": "not_run", "case": "TC-08", "reason": "r"}]
        blocks = node_eval(f"layout({json.dumps(events)}).map((b) => [b.kind, b.e.type])")
        self.assertEqual(blocks, [["group", "say"], ["not_run", "not_run"], ["not_run", "not_run"]])

    def test_layout_skips_case_plan(self):
        events = [ev_plan("TC-01"), ev_plan("TC-02"), ev_start("TC-01")]
        self.assertEqual(node_eval(f"layout({json.dumps(events)}).map((b) => b.kind)"), ["case_start"])


if __name__ == "__main__":
    unittest.main()
