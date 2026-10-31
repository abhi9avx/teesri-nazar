"""The judge's labelled cases (tests/fixtures/judgment/cases.json): each holds what a case recorded
and the verdict a senior tester gives it. A false fail (the app was right) and a false pass (a
planted bug waved through) both count as wrong. The baseline lists the cases judged right today:
a change may add to it, never take from it, so the judgment only ever improves."""

import json
import unittest
from pathlib import Path

from teesri_nazar.judge import verdict

FIXTURES = Path(__file__).parent / "fixtures" / "judgment"
CASES = json.loads((FIXTURES / "cases.json").read_text())
BASELINE = json.loads((FIXTURES / "baseline.json").read_text())


def judged(case: dict) -> dict:
    given = case.get("given", {})
    status, reason, cause = verdict(case["checks"], case["states"], case["crash"], given.get("status"),
                                    given.get("reason"))
    return {"status": status, "reason": reason, "cause": cause}


def mistakes(case: dict) -> list[str]:
    got, want = judged(case), case["label"]
    out = []
    if got["status"] != want["status"]:
        kind = "false pass" if got["status"] == "pass" else "false fail" if got["status"] == "fail" else "wrong status"
        out.append(f"{kind}: {got['status']} for {want['status']}")
    if "cause" in want and got["cause"] != want["cause"]:
        out.append(f"cause {got['cause']} for {want['cause']}")
    if "reason_has" in want and want["reason_has"] not in got["reason"]:
        out.append(f"reason {got['reason']!r} does not say {want['reason_has']!r}")
    return out


def scorecard() -> dict[str, list[str]]:
    return {c["id"]: mistakes(c) for c in CASES}


class JudgmentTest(unittest.TestCase):
    def test_every_case_judged_right_before_is_still_judged_right(self):
        card = scorecard()
        regressed = {i: card[i] for i in BASELINE["right"] if card.get(i)}
        self.assertEqual(regressed, {}, "the judgment got worse on these labelled cases")

    def test_an_improvement_is_locked_into_the_baseline(self):
        card = scorecard()
        newly_right = sorted(i for i, m in card.items() if not m and i not in BASELINE["right"])
        self.assertEqual(newly_right, [], "judged right now: add them to tests/fixtures/judgment/baseline.json")

    def test_the_cases_are_labelled_both_ways(self):
        statuses = {c["label"]["status"] for c in CASES}
        self.assertTrue({"pass", "fail", "blocked"} <= statuses)
        self.assertEqual(len({c["id"] for c in CASES}), len(CASES))


if __name__ == "__main__":
    card = scorecard()
    wrong = {i: m for i, m in card.items() if m}
    print(f"judged right: {len(card) - len(wrong)}/{len(card)}")
    for i, m in wrong.items():
        print(f"  {i}: {'; '.join(m)}")
