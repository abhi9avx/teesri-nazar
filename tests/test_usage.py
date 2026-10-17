"""Token cost: what each tnazar step adds to the agent's context, and real model usage per case."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from teesri_nazar import report as rpt
from teesri_nazar import usage
from teesri_nazar.session import Run

from test_cli import CliTestCase


def turn(t: str, req: str, fresh: int, cache_write: int, cache_read: int, out: int) -> str:
    return json.dumps({"type": "assistant", "timestamp": t, "requestId": req,
                       "message": {"usage": {"input_tokens": fresh, "cache_creation_input_tokens": cache_write,
                                             "cache_read_input_tokens": cache_read, "output_tokens": out}}})


class StepTokensTest(CliTestCase):
    def test_each_command_records_roughly_how_many_tokens_its_output_costs(self):
        run = self.start()
        out = self.ok("observe")
        io = [e for e in run.events() if e["type"] == "io"][-1]
        self.assertEqual(io["cmd"], "observe")
        self.assertEqual(io["tokens"], -(-len(out) // 4))

    def test_no_run_no_record(self):
        self.ok("observe")
        self.assertFalse(os.path.exists(os.path.join(self.home, "runs")))


class CaseUsageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"TNAZAR_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.run = Run.create("demo", {})
        self.run.update_meta(started_at="2026-01-01T10:00:30.000Z")
        self.transcript = Path(tmp.name) / "session.jsonl"
        lines = [
            turn("2026-01-01T10:00:05.000Z", "r0", 1, 0, 0, 10),          # before the run started
            turn("2026-01-01T10:00:50.000Z", "r00", 1, 0, 9000, 40),      # issues TC-01's command
            turn("2026-01-01T10:01:10.000Z", "r1", 2, 1000, 50000, 300),
            turn("2026-01-01T10:01:10.001Z", "r1", 2, 1000, 50000, 300),  # same request, second content block
            turn("2026-01-01T10:01:40.000Z", "r2", 3, 200, 51000, 120),
            turn("2026-01-01T10:02:10.000Z", "r20", 2, 0, 51500, 60),     # reads TC-01, issues TC-02
            turn("2026-01-01T10:03:00.000Z", "r3", 5, 0, 52000, 900),
            turn("2026-01-01T10:04:00.000Z", "r4", 5, 0, 53000, 700),     # after the last case
            '{"type": "user", "timestamp": "2026-01-01T10:01:20.000Z"}',
            "not json",
        ]
        self.transcript.write_text("\n".join(lines) + "\n")
        events = [
            {"t": "2026-01-01T10:01:00.000Z", "type": "case_start", "case": "TC-01", "title": "a"},
            {"t": "2026-01-01T10:02:00.000Z", "type": "case_end", "case": "TC-01", "status": "pass", "reason": ""},
            {"t": "2026-01-01T10:02:30.000Z", "type": "case_start", "case": "TC-02", "title": "b"},
            {"t": "2026-01-01T10:03:30.000Z", "type": "case_end", "case": "TC-02", "status": "pass", "reason": ""},
        ]
        self.run.events_path.write_text("".join(json.dumps(e) + "\n" for e in events))

    def test_each_case_owns_the_turns_since_the_previous_case_ended(self):
        # A case run as one shell command makes no model request inside its own window: the turn
        # that issued it came just before `case start`. So a case owns everything since the last one.
        per_case = usage.per_case(self.run, [self.transcript])
        self.assertEqual(per_case["TC-01"], {"turns": 3, "new": (1 + 40) + (2 + 1000 + 300) + (3 + 200 + 120),
                                             "cached": 9000 + 50000 + 51000, "output": 460})
        self.assertEqual(per_case["TC-02"]["turns"], 2)  # r20 and r3; r4 came after the run's last case

    def test_report_shows_tokens_per_case(self):
        with mock.patch.object(usage, "transcripts", return_value=[self.transcript]):
            md = rpt.to_markdown(self.run, rpt.summarize(self.run))
        self.assertIn("| Case | Title | Result | Checks | Tokens | Reason |", md)
        self.assertIn("1.7k new · 110k cached", md)

    def test_without_a_transcript_the_report_still_works(self):
        with mock.patch.object(usage, "transcripts", return_value=[]):
            md = rpt.to_markdown(self.run, rpt.summarize(self.run))
        self.assertIn("| Case | Title | Result | Checks | Reason |", md)

    def test_compact_numbers(self):
        self.assertEqual([usage.fmt(n) for n in (0, 420, 999, 1000, 1620, 101000)],
                         ["<1k", "<1k", "<1k", "1k", "1.6k", "101k"])


if __name__ == "__main__":
    unittest.main()
