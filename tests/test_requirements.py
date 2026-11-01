"""Requirements traceability (docs/prd-requirements.md): an approved list of requirements above the
cases, each proven by linked expected results, so "every case passed" never reads as "the feature
is tested"."""

import json
import os
import tempfile
import unittest

from unittest import mock

from teesri_nazar import live, plan, report
from teesri_nazar.session import Run, runs_dir
from fakedevice import PACKAGE, tnazar
from test_plan import PlanRunBase

HEAD = '''
app a.b
source "Transfer PRD v3"
requirement R1 "Valid amount transfers" --source "§2.1"
requirement R2 "Second submit creates no second transfer" --source "§2.4"
requirement R5 "Daily limit resets at midnight" --out-of-scope "needs a clock change"
'''

CASE = '''
case TC-01 "Transfer"
  expect "Reference number shows" --req R1
  expect "One row in history" --req R1 --req R2
  check Ref --for E1
  check "1 row" --for E2
'''


def parse(text: str, draft: bool = False) -> plan.Plan:
    return plan.parse(text, "x.plan", draft=draft)


class RequirementSyntaxTest(unittest.TestCase):
    def error(self, text: str) -> str:
        with self.assertRaises(plan.PlanError) as ctx:
            parse(text)
        return str(ctx.exception)

    def test_requirements_their_sources_and_links_are_read(self):
        p = parse(HEAD + CASE)
        self.assertEqual(p.source, "Transfer PRD v3")
        self.assertEqual([(r.id, r.text, r.source, r.out_of_scope) for r in p.requirements], [
            ("R1", "Valid amount transfers", "§2.1", ""),
            ("R2", "Second submit creates no second transfer", "§2.4", ""),
            ("R5", "Daily limit resets at midnight", "", "needs a clock change"),
        ])
        c = p.cases[0]
        self.assertEqual(c.expects, ["Reference number shows", "One row in history"])
        self.assertEqual(c.expect_reqs, [["R1"], ["R1", "R2"]])

    def test_a_plan_without_requirements_parses_as_before(self):
        p = parse("app a.b\ncase T1\n  expect X\n  check X --for E1\n")
        self.assertEqual((p.requirements, p.source, p.approved), ([], "", ""))
        self.assertEqual(p.cases[0].expect_reqs, [[]])

    def test_an_unknown_requirement_names_the_line(self):
        msg = self.error(HEAD + 'case T1\n  expect X --req R9\n  check X --for E1\n')
        self.assertIn("line 8", msg)
        self.assertIn("R9", msg)

    def test_a_requirement_defined_twice(self):
        msg = self.error('app a.b\nrequirement R1 "a"\nrequirement R1 "b"\ncase T1\n  check X\n')
        self.assertIn("line 3", msg)
        self.assertIn("twice", msg)

    def test_requirements_belong_at_the_top(self):
        msg = self.error('app a.b\ncase T1\n  check X\nrequirement R1 "a"\n')
        self.assertIn("line 4", msg)
        self.assertIn("top", msg)
        msg = self.error('app a.b\ncase T1\n  check X\nflow f\n  key back\nend\nrequirement R1 "a"\n')
        self.assertIn("line 7", msg)

    def test_a_requirement_needs_an_id_and_one_text(self):
        self.assertIn("line 2", self.error('app a.b\nrequirement R1\ncase T1\n  check X\n'))
        self.assertIn("line 2", self.error('app a.b\nrequirement R1 "a" --colour red\ncase T1\n  check X\n'))

    def test_source_and_approved_are_given_once(self):
        self.assertIn("twice", self.error('app a.b\nsource "a"\nsource "b"\ncase T1\n  check X\n'))
        self.assertIn("twice", self.error('app a.b\napproved abc\napproved def\ncase T1\n  check X\n'))

    def test_req_needs_a_requirement_id(self):
        self.assertIn("line 8", self.error(HEAD + 'case T1\n  expect X --req\n  check X --for E1\n'))


REVIEWED = HEAD + CASE + '''
case TC-02 "Limit"
  expect "Limit message shows"
  check Limit --for E1
'''


class ReviewTest(unittest.TestCase):
    def review(self, text: str) -> tuple[str, bool]:
        return plan.review(parse(text, draft=True))

    def test_review_lists_every_requirement_its_links_and_the_omission(self):
        text, ready = self.review(REVIEWED)
        self.assertFalse(ready)
        self.assertIn("source: Transfer PRD v3", text)
        self.assertIn("R1 Valid amount transfers [§2.1]  <- TC-01 E1; TC-01 E2", text)
        self.assertIn("R2 Second submit creates no second transfer [§2.4]  <- TC-01 E2", text)
        self.assertIn("R5 Daily limit resets at midnight  OUT OF SCOPE: needs a clock change", text)
        self.assertIn("E2 One row in history [R1, R2]", text)
        self.assertLess(text.index("R1 Valid"), text.index("TC-01  Transfer"))
        self.assertIn("requirements not approved", text)

    def test_a_requirement_nothing_proves_is_a_gap_that_names_the_fix(self):
        text, ready = self.review(HEAD.replace("R5", "R3").replace(' --out-of-scope "needs a clock change"', "") + CASE)
        self.assertFalse(ready)
        self.assertIn("R3 Daily limit resets at midnight  <- NOT COVERED", text)
        self.assertIn('R3 "Daily limit resets at midnight" has no expected result: link one with --req R3, '
                      'or mark it --out-of-scope "<why>"', text)

    def test_an_approved_complete_scope_is_ready(self):
        p = parse(REVIEWED, draft=True)
        top = REVIEWED.replace("\ncase TC-01", f"\napproved {plan.fingerprint(p)}\ncase TC-01", 1)
        text, ready = self.review(top)
        self.assertTrue(ready, text)
        self.assertIn("3 requirement(s): 2 covered, 1 out of scope, approved", text)

    def test_a_legacy_plan_reviews_exactly_as_before(self):
        text, ready = self.review("app a.b\ncase T1 \"t\"\n  expect X\n  check X --for E1\n")
        self.assertEqual(text, "T1  t\n  E1 X  <- line 4: check X\n\n"
                               "ready to run: 1 case(s), 1 expected result(s), every one proven")
        self.assertTrue(ready)


class FingerprintTest(unittest.TestCase):
    """A13: re-approval for a change to what was approved, not for a change to how it is tested."""

    def fp(self, text: str) -> str:
        return plan.fingerprint(parse(text, draft=True))

    def test_what_was_approved_changes_it(self):
        base = self.fp(REVIEWED)
        for changed in (
            REVIEWED.replace('"§2.4"', '"§2.5"'),                                   # a source reference
            REVIEWED.replace("Second submit", "A second submit"),                    # a requirement's text
            REVIEWED.replace("needs a clock change", "later"),                       # an out-of-scope reason
            REVIEWED.replace("Transfer PRD v3", "Transfer PRD v4"),                  # the spec
            REVIEWED.replace('"One row in history" --req R1 --req R2', '"One row in history" --req R1'),  # a link
            REVIEWED.replace('"Limit message shows"', '"Limit message shows" --req R2'),               # a new link
            REVIEWED.replace('"One row in history" --req R1 --req R2', '"One row in history" --req R1')
                    .replace('"Limit message shows"', '"Limit message shows" --req R2'),                # a moved link
            REVIEWED.replace('requirement R5 "Daily limit resets at midnight" --out-of-scope "needs a clock change"\n',
                             ""),                                                   # a dropped requirement
        ):
            self.assertNotEqual(self.fp(changed), base, changed)

    def test_how_it_is_tested_does_not(self):
        base = self.fp(REVIEWED)
        moved = HEAD + REVIEWED[len(HEAD + CASE):] + CASE  # cases reordered
        self.assertEqual(self.fp(moved), base)
        self.assertEqual(self.fp(REVIEWED.replace("check Ref --for E1", "tap Go\n  check Ref2 --for E1")), base)
        self.assertRegex(base, r"^[0-9a-f]{12}$")


class ApproveCommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "transfer.plan")

    def write(self, text: str) -> None:
        with open(self.path, "w") as f:
            f.write(text)

    def text(self) -> str:
        with open(self.path) as f:
            return f.read()

    def test_approve_writes_the_fingerprint_at_the_top_and_review_turns_ready(self):
        self.write(REVIEWED)
        self.assertEqual(tnazar("plan", "review", self.path)[0], 1)
        code, out, _ = tnazar("plan", "approve", self.path)
        self.assertEqual(code, 0, out)
        fp = plan.fingerprint(parse(REVIEWED, draft=True))
        self.assertIn(fp, out)
        self.assertLess(self.text().index(f"approved {fp}"), self.text().index("case TC-01"))
        code, out, _ = tnazar("plan", "review", self.path)
        self.assertEqual(code, 0, out)

    def test_an_edit_after_approval_names_both_fingerprints_and_re_approval_replaces_the_line(self):
        self.write(REVIEWED)
        tnazar("plan", "approve", self.path)
        old = plan.load(self.path).approved
        self.write(self.text().replace("Second submit", "Double submit"))
        code, out, _ = tnazar("plan", "review", self.path)
        new = plan.fingerprint(plan.load(self.path, draft=True))
        self.assertEqual(code, 1)
        self.assertIn(f"requirements changed since approval (approved {old}, now {new})", out)
        tnazar("plan", "approve", self.path)
        self.assertEqual(self.text().count("approved "), 1)
        self.assertEqual(plan.load(self.path).approved, new)

    def test_a_tab_separated_approved_line_is_replaced_not_kept(self):
        self.write(REVIEWED.replace("\ncase TC-01", "\napproved\tstale0000000\ncase TC-01", 1))
        tnazar("plan", "approve", self.path)
        self.assertNotIn("stale0000000", self.text())
        self.assertEqual(self.text().count("approved "), 1)

    def test_a_plan_without_requirements_has_nothing_to_approve(self):
        self.write("app a.b\ncase T1\n  check X\n")
        code, _, err = tnazar("plan", "approve", self.path)
        self.assertEqual(code, 2)
        self.assertIn("no `requirement`", err)
        self.assertNotIn("approved", self.text())


# The acceptance fixture: five requirements, six cases, R2 (no duplicate transfer) linked by nothing.
TRANSFER_HEAD = f'''
app {PACKAGE}
name demo
source "Transfer PRD v3"
requirement R1 "Login reaches home" --source "§1"
requirement R2 "Second submit creates no second transfer" --source "§2.4"
requirement R3 "Back returns to login" --source "§3"
requirement R4 "Login screen shows" --source "§4"
requirement R5 "Login is the start screen" --source "§5"
'''

TRANSFER_CASES = '''
flow login
  type "9876543210" --into id=phone_input
  tap Login
end

case TC-01 "Login"
  expect "Home shows" --req R1
  run login
  check "Pay now" --for E1
case TC-02 "Back"
  expect "Login shows again" --req R3
  key back
  check Login --for E1
case TC-03 "Login screen"
  expect "Login button" --req R4
  check Login --for E1
case TC-04 "Start screen"
  expect "Login first" --req R5
  check Login --for E1
case TC-05 "Login again"
  expect "Home again" --req R1
  run login
  check "Pay now" --for E1
case TC-06 "Back again"
  expect "Login again"
  key back
  check Login --for E1
'''


class RequirementRunBase(PlanRunBase):
    def run_req(self, text: str, *flags: str, expect: int, approve: bool = True):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(text)
        if approve:
            self.assertEqual(tnazar("plan", "approve", path)[0], 0)
        code, out, err = tnazar("run", path, *flags)
        self.assertEqual(code, expect, out + err)
        run = Run.load(sorted(p.name for p in runs_dir().iterdir())[-1])
        s = report.summarize(run)
        return out, run, s, report.to_markdown(run, s)


class RunRequirementsTest(RequirementRunBase):
    def test_a1_six_passing_cases_never_read_as_the_feature_tested_when_r2_is_omitted(self):
        out, run, s, md = self.run_req(TRANSFER_HEAD + TRANSFER_CASES, expect=1)
        self.assertEqual(self.cases(run), {f"TC-0{i}": "pass" for i in range(1, 7)})
        self.assertTrue(report.summary_line(s).endswith("| verified 6/6  | requirements 4/5 verified"))
        self.assertIn("**Requirements:** incomplete — R2 not covered", md)
        self.assertIn("R2 not covered", out)
        self.assertIn("1 requirement has no expected result: R2", out)  # said at the start, too
        self.assertNotIn("all requirements verified", md + out)
        self.assertNotIn("in-scope requirements verified", md + out)

    def test_a1_the_requirements_table_names_what_proved_each_one(self):
        _, _, _, md = self.run_req(TRANSFER_HEAD + TRANSFER_CASES, expect=1)
        self.assertIn("| R1 | Login reaches home | §1 | ✅ verified | TC-01 E1 ← \"Pay now\" is visible; "
                      "TC-05 E1 ← \"Pay now\" is visible |", md)
        self.assertIn("| R2 | Second submit creates no second transfer | §2.4 | ○ not covered | "
                      "no expected result links to it |", md)

    def test_a2_a_failed_check_fails_its_requirement(self):
        text = TRANSFER_HEAD + TRANSFER_CASES.replace("  key back\n  check Login --for E1\ncase TC-03",
                                                      "  key back\n  check Nowhere --timeout 1 --for E1\ncase TC-03")
        _, _, s, md = self.run_req(text, expect=1)
        self.assertTrue(report.summary_line(s).endswith("requirements 3/5 verified"))
        self.assertIn("| R3 | Back returns to login | §3 | ❌ failed | TC-02 E1 failed: \"Nowhere\" is visible |", md)

    def test_a3_a_case_blocked_on_its_setup_leaves_its_requirement_not_verified_with_the_cause(self):
        text = TRANSFER_HEAD + TRANSFER_CASES.replace('  expect "Login button" --req R4\n',
                                                      '  expect "Login button" --req R4\n  require Nowhere --timeout 1\n')
        _, _, s, md = self.run_req(text, expect=1)
        self.assertTrue(report.summary_line(s).endswith("requirements 3/5 verified"))
        self.assertIn("| R4 | Login screen shows | §4 | ◐ not verified | TC-03 blocked · precondition not met |", md)

    def test_a5_out_of_scope_is_named_and_counted_and_the_rest_verified_exits_0(self):
        text = TRANSFER_HEAD.replace('--source "§2.4"', '--source "§2.4" --out-of-scope "needs backend fault injection"')
        out, _, s, md = self.run_req(text + TRANSFER_CASES, expect=0)
        self.assertTrue(report.summary_line(s).endswith("requirements 4/5 verified (1 excluded)"))
        self.assertIn("**Requirements:** all in-scope requirements verified; 1 excluded "
                      "(R2: needs backend fault injection)", md)
        self.assertNotIn("all requirements verified", md)

    def test_every_requirement_verified_says_so_and_exits_0(self):
        text = TRANSFER_HEAD + TRANSFER_CASES.replace('expect "Login again"', 'expect "Login again" --req R2')
        _, _, s, md = self.run_req(text, expect=0)
        self.assertTrue(report.summary_line(s).endswith("requirements 5/5 verified"))
        self.assertIn("**Requirements:** all requirements verified", md)

    def test_a6_an_unapproved_or_changed_scope_is_never_a_success(self):
        text = TRANSFER_HEAD + TRANSFER_CASES.replace('expect "Login again"', 'expect "Login again" --req R2')
        out, _, _, md = self.run_req(text, expect=1, approve=False)
        self.assertIn("**Requirements:** incomplete — scope not approved", md)
        self.assertIn("requirements not approved", out)
        path = os.path.join(self.home, "demo.plan")
        tnazar("plan", "approve", path)
        with open(path) as f:
            edited = f.read().replace("Back returns to login", "Back goes to login")
        _, _, _, md = self.run_req(edited, expect=1, approve=False)
        self.assertIn("**Requirements:** incomplete — approved fingerprint does not match", md)

    def test_a9_one_case_linking_two_requirements_cannot_prove_both_with_one_check(self):
        text = TRANSFER_HEAD + TRANSFER_CASES.replace('''  expect "Login again"
  key back
  check Login --for E1''', '''  expect "Login again" --req R3
  expect "No second row" --req R2
  key back
  check Login --for E1
  if Nowhere
    check Nowhere --for E2
  end''')
        _, _, _, md = self.run_req(text, expect=1)
        self.assertIn("| R2 | Second submit creates no second transfer | §2.4 | ◐ not verified | "
                      "TC-06 E2 not verified |", md)
        self.assertIn("| R3 | Back returns to login | §3 | ◐ not verified | TC-06 blocked · not verified |", md)

    def test_a10_all_requirements_excluded_claims_nothing(self):
        head = "\n".join(ln + (' --out-of-scope "later"' if ln.startswith("requirement") else "")
                         for ln in TRANSFER_HEAD.splitlines())
        cases = TRANSFER_CASES.replace(" --req R1", "").replace(" --req R3", "").replace(" --req R4", "").replace(" --req R5", "")
        _, _, _, md = self.run_req(head + "\n" + cases, expect=1)
        self.assertIn("**Requirements:** no in-scope requirement: feature verification not claimed", md)

    def test_a11_a_crash_after_the_check_leaves_the_requirement_not_verified(self):
        text = TRANSFER_HEAD + TRANSFER_CASES.replace('''  expect "Login button" --req R4
  check Login --for E1''', '''  expect "Login button" --req R4
  check Login --for E1
  say "crash now"''')
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main

        def main(argv):
            if argv[:2] == ["say", "crash now"]:
                self.adb.crash_log = f"FATAL EXCEPTION: main\nProcess: {PACKAGE}, PID: 4242\n"
            return real_main(argv)
        with mock.patch("teesri_nazar.cli.main", main):
            _, _, _, md = self.run_req(text, expect=1)
        self.assertIn("| R4 | Login screen shows | §4 | ◐ not verified | TC-03 failed: crash |", md)


COMPLETE = TRANSFER_HEAD + TRANSFER_CASES.replace('expect "Login again"', 'expect "Login again" --req R2')


class PartialRunTest(RequirementRunBase):
    """A4, A12, A15: the scope covers every case, selected or not; case results stay selected-only."""

    def test_a4_only_one_of_two_linked_cases_leaves_the_requirement_not_verified(self):
        out, run, s, md = self.run_req(COMPLETE, "--only", "TC-01", expect=1)
        self.assertEqual([e["case"] for e in run.events() if e["type"] == "case_plan"], ["TC-01"])
        [scope] = [e for e in run.events() if e["type"] == "scope"]
        self.assertEqual([c["id"] for c in scope["cases"]], [f"TC-0{i}" for i in range(1, 7)])
        self.assertEqual(scope["selected"], ["TC-01"])
        self.assertIn("1 case(s): ✅ 1 pass", report.summary_line(s))  # case results as today
        self.assertIn("| R1 | Login reaches home | §1 | ◐ not verified | TC-05 not selected |", md)
        self.assertIn("R1 not verified (TC-05 not selected)", out)

    def test_a12_from_marks_the_earlier_cases_not_selected(self):
        _, _, _, md = self.run_req(COMPLETE, "--from", "TC-04", expect=1)
        self.assertIn("| R3 | Back returns to login | §3 | ◐ not verified | TC-02 not selected |", md)
        self.assertIn("| R5 | Login is the start screen | §5 | ✅ verified | TC-04 E1 ← \"Login\" is visible |", md)

    def test_a12_stop_on_fail_marks_the_rest_not_run(self):
        text = COMPLETE.replace("  key back\n  check Login --for E1\ncase TC-03",
                                "  key back\n  check Nowhere --timeout 1 --for E1\ncase TC-03")
        _, _, _, md = self.run_req(text, "--stop-on-fail", expect=1)
        self.assertIn("| R4 | Login screen shows | §4 | ◐ not verified | TC-03 not run |", md)

    def hand_back_at(self, case_say: str, path: str):
        """Run the plan; a dashboard message at `say <case_say>` hands the run back (exit 3)."""
        posted = []
        real_main = __import__("teesri_nazar.cli", fromlist=["main"]).main

        def main(argv):
            if argv[:2] == ["say", case_say] and not posted:
                posted.append(1)
                live.post("wait a moment")
            return real_main(argv)
        with mock.patch("teesri_nazar.cli.main", main):
            return tnazar("run", path)

    def test_a12_a_hand_back_then_from_in_the_same_run_adds_up_to_all_verified(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(COMPLETE.replace('  expect "Login first" --req R5\n',
                                     '  expect "Login first" --req R5\n  say "start screen"\n'))
        tnazar("plan", "approve", path)
        code, out, _ = self.hand_back_at("start screen", path)
        self.assertEqual(code, live.EXIT_HELD, out)
        code, out, _ = tnazar("run", path, "--from", "TC-04")
        self.assertEqual(code, 0, out)
        self.assertIn("requirements: all requirements verified", out)

    def test_a15_a_scope_edited_between_a_run_and_its_continuation_is_never_verified(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(COMPLETE.replace('  expect "Login first" --req R5\n',
                                     '  expect "Login first" --req R5\n  say "start screen"\n'))
        tnazar("plan", "approve", path)
        self.hand_back_at("start screen", path)
        with open(path) as f:
            edited = f.read().replace("Back returns to login", "Back goes to login")
        with open(path, "w") as f:
            f.write(edited)
        tnazar("plan", "approve", path)  # even re-approved: the run's own evidence spans two scopes
        code, out, _ = tnazar("run", path, "--from", "TC-04")
        self.assertEqual(code, 1, out)
        self.assertIn("requirements: incomplete — scope changed mid-run", out)


class ReplayRequirementsTest(RequirementRunBase):
    """A14: replay keeps the approved scope and judges requirements on its own new evidence."""

    def record(self, text: str, expect: int = 0):
        self.run_req(text, expect=expect)
        self.adb.current = self.adb.app.start

    def replay(self, expect: int):
        source = sorted(p.name for p in runs_dir().iterdir())[-1]
        code, out, err = tnazar("replay", source)
        self.assertEqual(code, expect, out + err)
        run = Run.load(sorted(p.name for p in runs_dir().iterdir() if "-replay-" in p.name)[-1])
        s = report.summarize(run)
        return out, run, s, report.to_markdown(run, s)

    def test_a14_a_faithful_replay_copies_the_scope_and_verifies_again(self):
        self.record(COMPLETE)
        source = Run.load(sorted(p.name for p in runs_dir().iterdir())[-1])
        out, run, s, md = self.replay(expect=0)
        [copied] = [e for e in run.events() if e["type"] == "scope"]
        [recorded] = [e for e in source.events() if e["type"] == "scope"]
        self.assertEqual({k: v for k, v in copied.items() if k != "t"}, {k: v for k, v in recorded.items() if k != "t"})
        self.assertIn("**Requirements:** all requirements verified", md)
        self.assertIn("requirements 5/5 verified", out)

    def test_a14_a_check_that_fails_on_replay_fails_its_requirement_there_only(self):
        self.record(COMPLETE)
        source = Run.load(sorted(p.name for p in runs_dir().iterdir())[-1])
        self.adb.app.screens["home"] = [n for n in self.adb.app.screens["home"] if n.get("text") != "Pay now"]
        _, _, _, md = self.replay(expect=1)
        self.assertIn("| R1 | Login reaches home | §1 | ❌ failed |", md)
        self.assertIn("all requirements verified", report.to_markdown(source, report.summarize(source)))

    def test_a14_replaying_a_continued_run_adds_up_both_selections(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(COMPLETE.replace('  expect "Login first" --req R5\n',
                                     '  expect "Login first" --req R5\n  say "start screen"\n'))
        tnazar("plan", "approve", path)
        PartialRunTest.hand_back_at(self, "start screen", path)
        self.assertEqual(tnazar("run", path, "--from", "TC-04")[0], 0)
        self.adb.current = self.adb.app.start
        _, run, _, md = self.replay(expect=0)
        self.assertEqual(len([e for e in run.events() if e["type"] == "scope"]), 2)
        self.assertIn("**Requirements:** all requirements verified", md)

    def test_a14_evidence_replay_cannot_collect_again_is_not_verified(self):
        text = COMPLETE.replace('''  expect "Login first" --req R5
  check Login --for E1''', '''  expect "Login first" --req R5
  check --result pass --kind db --what "start screen flag = login" --for E1''')
        self.record(text)
        _, _, _, md = self.replay(expect=1)
        self.assertIn("| R5 | Login is the start screen | §5 | ◐ not verified | TC-04 E1 not verified |", md)


class DashboardRequirementsTest(RequirementRunBase):
    def test_the_dashboard_summary_counts_verified_requirements_while_the_run_is_open(self):
        from teesri_nazar import ui
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(COMPLETE.replace('  expect "Login first" --req R5\n',
                                     '  expect "Login first" --req R5\n  say "start screen"\n'))
        tnazar("plan", "approve", path)
        PartialRunTest.hand_back_at(self, "start screen", path)  # the run stays open
        summary = ui.Dashboard(0).state()["active"]["summary"]
        self.assertTrue(summary.endswith("| requirements 2/5 verified"), summary)  # R1 also needs TC-05, still pending


class LegacyRunTest(PlanRunBase):
    """C4: a plan with no requirements runs, logs and exits exactly as before."""

    def test_no_scope_event_no_requirements_count_and_one_honest_line(self):
        out, run = self.run_plan('''
case TC-01 "Login"
  expect "Home"
  run login
  check "Pay now" --for E1
''')
        self.assertFalse([e for e in run.events() if e["type"] == "scope"])
        s = report.summarize(run)
        self.assertNotIn("requirements", report.summary_line(s) + out)
        self.assertIn("requirements not declared: feature completeness not claimed", report.to_markdown(run, s))


LEGACY_PLAN = f"""app {PACKAGE}
name demo
flow login
  type "9876543210" --into id=phone_input
  tap Login
end
case TC-01 "Login"
  expect "Home"
  run login
  check "Pay now" --for E1
case TC-02 "Back"
  key back
  check Login
case TC-03 "Missing"
  expect "Nope"
  check Nowhere --timeout 1 --for E1
case TC-04 "Setup"
  require Nowhere --timeout 1
  check Login
"""
VOLATILE = {"t", "dur_ms", "shot", "file", "sid", "screen_sig", "polls", "evidence", "crash", "took", "ms", "dump"}


class LegacyGoldenTest(PlanRunBase):
    """C4, C5: a plan without requirements, and its replay, log the same events and exit the same
    as main (b39bf69) did; the golden file was recorded there."""

    maxDiff = None

    def test_events_and_exit_codes_equal_mains(self):
        path = os.path.join(self.home, "demo.plan")
        with open(path, "w") as f:
            f.write(LEGACY_PLAN)
        run_code = tnazar("run", path)[0]
        source = sorted(p.name for p in runs_dir().iterdir())[-1]
        self.adb.current = self.adb.app.start
        replay_code = tnazar("replay", source)[0]
        replayed = sorted(p.name for p in runs_dir().iterdir() if "-replay-" in p.name)[-1]

        def norm(events):
            return [{k: v for k, v in sorted(e.items()) if k not in VOLATILE} for e in events]
        got = {"run_code": run_code, "replay_code": replay_code,
               "run": norm(Run.load(source).events()), "replay": norm(Run.load(replayed).events())}
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "requirements",
                               "legacy-run-and-replay.json")) as f:
            self.assertEqual(got, json.load(f))


if __name__ == "__main__":
    unittest.main()
