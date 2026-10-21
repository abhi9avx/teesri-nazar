"""Importing test cases from a sheet (CSV, TSV, Markdown) into a draft plan (docs/prd-coverage.md §11)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fakedevice import PACKAGE, tnazar  # noqa: E402
from test_cli import CliTestCase  # noqa: E402
from teesri_nazar import intake, plan  # noqa: E402

# Pasted from a sheet: tab-separated, no header row, a section row, cells with unquoted line breaks.
PASTED = (
    "Overdue user\t\t\t\t\t\t\t\t\n"
    "Overdue - asks due date\tVerify due date for an overdue user.\tUser has a card; overdue\t"
    "1. Log in.\n2. Open Help.\n3. Ask \"when is my due date\"\tTotal due: > 0; Fee: > 0\t"
    "Bot shares the total due and the due date.\tP0\tDONE\t9000000001\n"
    "Overdue - asks fee\tVerify fee answer.\tUser has a card; overdue\t1. Log in.\n2. Ask about the fee\t"
    "Total due: > 0\tBot says the fee applies 3 days after the due date.\n- It is slab-based, plus GST.\tP1\tDONE\t9000000002\n"
)
COLUMNS = "title,description,precondition,steps,data,expected,priority,status,account"

CSV = ('ID,Test case,Expected result,Priority\n'
       'LGN-1,Login works,"Home shows the user; Balance shown",P0\n'
       'LGN-2,"Wrong PIN, error","Shows ""Incorrect"" text",P1\n')

MARKDOWN = """| Case ID | Scenario | Expected |
|---|---|---|
| A-1 | Opens settings | Settings list shown |
| A-2 | Search works | 1. Results shown<br>2. First result opens |
"""


class ReadTableTest(unittest.TestCase):
    def test_tsv_without_a_header_keeps_line_breaks_inside_cells(self):
        cases = intake.read(PASTED, columns=COLUMNS)
        self.assertEqual([c.id for c in cases], ["TC-01", "TC-02"])
        self.assertEqual(cases[0].title, "Overdue - asks due date")
        self.assertEqual(cases[0].steps, ["1. Log in.", "2. Open Help.", '3. Ask "when is my due date"'])
        self.assertEqual(cases[0].section, "Overdue user")
        self.assertEqual(cases[0].fields["account"], "9000000001")
        self.assertEqual(cases[1].fields["priority"], "P1")

    def test_expected_results_split_only_on_explicit_separators(self):
        cases = intake.read(PASTED, columns=COLUMNS)
        self.assertEqual(cases[0].expects, ["Bot shares the total due and the due date."])
        self.assertEqual(cases[1].expects, ["Bot says the fee applies 3 days after the due date.",
                                            "It is slab-based, plus GST."])

    def test_csv_with_a_header_keeps_its_ids_and_quotes(self):
        cases = intake.read(CSV)
        self.assertEqual([(c.id, c.title) for c in cases], [("LGN-1", "Login works"), ("LGN-2", "Wrong PIN, error")])
        self.assertEqual(cases[0].expects, ["Home shows the user", "Balance shown"])
        self.assertEqual(cases[1].expects, ['Shows "Incorrect" text'])

    def test_markdown_table(self):
        cases = intake.read(MARKDOWN)
        self.assertEqual([c.id for c in cases], ["A-1", "A-2"])
        self.assertEqual(cases[1].expects, ["Results shown", "First result opens"])

    def test_no_header_and_no_columns_says_how_to_name_them(self):
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.read(PASTED)
        self.assertIn("--columns", str(ctx.exception))
        self.assertIn("Overdue - asks due date", str(ctx.exception))

    def test_duplicate_ids_name_both_rows(self):
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.read("ID,Title,Expected\nX-1,a,b\nX-2,c,d\nX-1,e,f\n")
        self.assertIn("X-1", str(ctx.exception))
        self.assertIn("rows 1 and 3", str(ctx.exception))

    def test_an_id_with_spaces_is_refused(self):
        with self.assertRaises(intake.IntakeError):
            intake.read("ID,Title,Expected\nX 1,a,b\n")

    def test_unknown_columns_are_kept_as_notes(self):
        [c] = intake.read("Title,Expected,Owner\nLogin,Home shown,QA team\n")
        self.assertEqual(c.fields["Owner"], "QA team")

    def test_a_row_without_expected_results_imports_with_none(self):
        [c] = intake.read("Title,Expected\nLogin,\n")
        self.assertEqual(c.expects, [])


class DraftPlanTest(unittest.TestCase):
    def draft(self, text=PASTED, **kw):
        return intake.to_plan(intake.read(text, columns=kw.pop("columns", COLUMNS)), app="com.example.app",
                              name="overdue", source="cases.tsv")

    def test_the_draft_parses_and_its_only_gaps_are_the_missing_steps(self):
        p = plan.parse(self.draft(), "overdue.plan", draft=True)
        self.assertEqual([c.id for c in p.cases], ["TC-01", "TC-02"])
        self.assertEqual(p.cases[1].expects, ["Bot says the fee applies 3 days after the due date.",
                                              "It is slab-based, plus GST."])
        self.assertEqual([g.split(": ", 1)[1] for g in p.gaps][:2], ["case TC-01 has no steps", "case TC-02 has no steps"])

    def test_the_draft_carries_every_field_as_a_comment(self):
        text = self.draft()
        for line in ("# == Overdue user ==", "# precondition: User has a card; overdue", "#   2. Open Help.",
                     "# test data: Total due: > 0; Fee: > 0", "# priority: P0", "# account: 9000000001"):
            self.assertIn(line, text)

    def test_quotes_and_backslashes_survive_the_plan_parser(self):
        cases = intake.read('Title,Expected\nT,"Says ""hi"" and C:\\\\path"\n')
        p = plan.parse(intake.to_plan(cases, app="a.b", name="q", source="x"), "q.plan", draft=True)
        self.assertEqual(p.cases[0].expects, ['Says "hi" and C:\\\\path'])

    def test_the_draft_never_holds_a_secret_column(self):
        text = intake.to_plan(intake.read("Title,Expected,OTP,MPIN\nLogin,Home,0000,1234\n"),
                              app="a.b", name="s", source="x")
        self.assertNotIn("0000", text)
        self.assertNotIn("1234", text)


class ImportCliTest(CliTestCase):
    def test_import_writes_a_draft_and_reports_what_is_missing(self):
        src = Path(self.home, "cases.tsv")
        src.write_text(PASTED)
        out = self.ok("cases", "import", str(src), "--app", PACKAGE, "--columns", COLUMNS)
        target = Path(self.home, "plans", "cases.plan")
        self.assertTrue(target.exists())
        self.assertIn("2 case(s), 3 expected result(s)", out)
        self.assertIn("accounts: 9000000001 (TC-01), 9000000002 (TC-02)", out)
        self.assertIn("plan review", out)

    def test_import_refuses_to_overwrite_without_force(self):
        src = Path(self.home, "cases.csv")
        src.write_text(CSV)
        self.ok("cases", "import", str(src), "--app", PACKAGE)
        code, _, err = tnazar("cases", "import", str(src), "--app", PACKAGE)
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.ok("cases", "import", str(src), "--app", PACKAGE, "--force")

    def test_import_reads_pasted_text_from_stdin(self):
        import io
        from unittest import mock
        with mock.patch("sys.stdin", io.StringIO(MARKDOWN)):
            out = self.ok("cases", "import", "-", "--app", PACKAGE, "--name", "settings")
        self.assertIn("plans", out)
        self.assertTrue(Path(self.home, "plans", "settings.plan").exists())

    def test_a_missing_file_is_a_plain_error(self):
        code, _, err = tnazar("cases", "import", "nope.csv", "--app", PACKAGE)
        self.assertEqual(code, 2)
        self.assertIn("nope.csv", err)
        self.assertNotIn("Traceback", err)

    def test_run_refuses_the_draft_until_it_is_written(self):
        src = Path(self.home, "cases.csv")
        src.write_text(CSV)
        self.ok("cases", "import", str(src), "--app", PACKAGE)
        code, _, err = tnazar("run", str(Path(self.home, "plans", "cases.plan")))
        self.assertEqual(code, 2)
        self.assertIn("has no steps", err)


if __name__ == "__main__":
    unittest.main()
