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
    "Notes app\t\t\t\t\t\t\t\t\n"
    "Notes - create a note\tVerify a note can be created.\tUser is signed in; empty list\t"
    "1. Sign in.\n2. Open Notes.\n3. Tap \"New note\"\tTitle: Groceries; Body: milk\t"
    "The note shows in the list with its title.\tP0\tDONE\tuser-one\n"
    "Notes - delete a note\tVerify delete.\tUser is signed in; one note\t1. Sign in.\n2. Delete the note\t"
    "Title: Groceries\tThe note is gone after 3 seconds.\n- An undo bar shows, plus a count.\tP1\tDONE\tuser-two\n"
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
        self.assertEqual(cases[0].title, "Notes - create a note")
        self.assertEqual(cases[0].steps, ["1. Sign in.", "2. Open Notes.", '3. Tap "New note"'])
        self.assertEqual(cases[0].section, "Notes app")
        self.assertEqual(cases[0].fields["account"], "user-one")
        self.assertEqual(cases[1].fields["priority"], "P1")

    def test_expected_results_split_only_on_explicit_separators(self):
        cases = intake.read(PASTED, columns=COLUMNS)
        self.assertEqual(cases[0].expects, ["The note shows in the list with its title."])
        self.assertEqual(cases[1].expects, ["The note is gone after 3 seconds.", "An undo bar shows, plus a count."])

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
        self.assertIn("Notes - create a note", str(ctx.exception))

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


class ReviewFixesTest(unittest.TestCase):
    """Rows are never merged or lost; headings are never cases; secrets never copied."""

    def test_m1_a_short_tsv_row_is_its_own_case(self):
        cases = intake.read("Title\tExpected\tPriority\nT1\tA\tP1\nT2\tB\nT3\tC\tP2\n")
        self.assertEqual([(c.title, c.expects) for c in cases], [("T1", ["A"]), ("T2", ["B"]), ("T3", ["C"])])

    def test_m1_line_breaks_inside_a_cell_still_join(self):
        cases = intake.read("Title\tSteps\tExpected\nT1\t1. a\n2. b\tDone\nT2\tx\tOk\n")
        self.assertEqual([(c.title, c.steps, c.expects) for c in cases],
                         [("T1", ["1. a", "2. b"], ["Done"]), ("T2", ["x"], ["Ok"])])

    def test_m2_a_row_with_only_an_id_is_a_case_not_a_heading(self):
        cases = intake.read("ID,Title,Expected\nX-1,,\nX-2,Login,Home\n")
        self.assertEqual([c.id for c in cases], ["X-1", "X-2"])

    def test_m3_secret_columns_are_dropped_by_their_words(self):
        text = intake.to_plan(intake.read("Title,Expected,Login PIN,OTP code,Password (test)\nT,E,1234,5678,hunter2\n"),
                              app="a.b", name="s", source="x")
        for value in ("1234", "5678", "hunter2"):
            self.assertNotIn(value, text)

    def test_m3_secret_values_inside_other_cells_are_masked(self):
        text = intake.to_plan(intake.read("Title,Expected,Test data\nT,E,phone 9000; OTP: 0000; MPIN=2580\n"),
                              app="a.b", name="s", source="x")
        self.assertNotIn("0000", text)
        self.assertNotIn("2580", text)
        self.assertIn("phone 9000", text)

    def test_m4_an_id_the_plan_cannot_hold_is_refused(self):
        for bad in ("O'Brien-1", 'A"1', "X#1"):
            with self.assertRaises(intake.IntakeError):
                intake.read(f"ID,Title,Expected\n{bad},t,e\n".replace('A"1', '"A""1"'))

    def test_generated_ids_skip_ones_the_sheet_uses(self):
        cases = intake.read("ID,Title,Expected\n,first,e\nTC-01,second,e\n")
        self.assertEqual([c.id for c in cases], ["TC-02", "TC-01"])

    def test_markdown_escaped_pipes_and_any_br(self):
        [c] = intake.read("| ID | Title | Expected |\n|---|---|---|\n| A-1 | a \\| b | x<BR>y<br />z |\n")
        self.assertEqual((c.title, c.expects), ("a | b", ["x", "y", "z"]))


class SpecReviewFixesTest(unittest.TestCase):
    def test_a_quoted_multiline_cell_in_the_last_column(self):
        cases = intake.read('ID\tTitle\tExpected\nTC1\tLogin\t"line1\nline2"\nTC2\tPay\tDone\n')
        self.assertEqual([(c.id, c.expects) for c in cases], [("TC1", ["line1", "line2"]), ("TC2", ["Done"])])

    def test_two_columns_meaning_the_same_field_are_refused(self):
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.read("Title,Expected,Expected Result\nT,a,b\n")
        self.assertIn("Expected Result", str(ctx.exception))

    def test_cells_beyond_the_header_are_kept_as_notes(self):
        [c] = intake.read("ID,Title,Expected\nTC1,a,ok,lost?\n")
        self.assertEqual(c.fields.get("column 4"), "lost?")

    def test_markdown_without_outer_pipes(self):
        cases = intake.read("ID | Title | Expected\n--- | --- | ---\nA-1 | Opens | Shown\n")
        self.assertEqual([(c.id, c.expects) for c in cases], [("A-1", ["Shown"])])

    def test_old_mac_line_endings(self):
        self.assertEqual([c.title for c in intake.read("Title,Expected\rA,x\rB,y\r")], ["A", "B"])

    def test_an_empty_bullet_is_not_an_expected_result(self):
        [c] = intake.read('Title,Expected\nT,"- first\n-\n- second"\n')
        self.assertEqual(c.expects, ["first", "second"])


class DraftPlanTest(unittest.TestCase):
    def draft(self, text=PASTED, **kw):
        return intake.to_plan(intake.read(text, columns=kw.pop("columns", COLUMNS)), app="com.example.app",
                              name="notes", source="cases.tsv")

    def test_the_draft_parses_and_its_only_gaps_are_the_missing_steps(self):
        p = plan.parse(self.draft(), "notes.plan", draft=True)
        self.assertEqual([c.id for c in p.cases], ["TC-01", "TC-02"])
        self.assertEqual(p.cases[1].expects, ["The note is gone after 3 seconds.", "An undo bar shows, plus a count."])
        self.assertEqual([g.split(": ", 1)[1] for g in p.gaps][:2], ["case TC-01 has no steps", "case TC-02 has no steps"])

    def test_the_draft_carries_every_field_as_a_comment(self):
        text = self.draft()
        for line in ("# == Notes app ==", "# precondition: User is signed in; empty list", "#   2. Open Notes.",
                     "# test data: Title: Groceries; Body: milk", "# priority: P0", "# account: user-one"):
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
        self.assertIn("accounts: user-one (TC-01), user-two (TC-02)", out)
        self.assertIn("plan review", out)

    def test_import_refuses_to_overwrite_without_force(self):
        src = Path(self.home, "cases.csv")
        src.write_text(CSV)
        self.ok("cases", "import", str(src), "--app", PACKAGE)
        code, _, err = tnazar("cases", "import", str(src), "--app", PACKAGE)
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.ok("cases", "import", str(src), "--app", PACKAGE, "--force")

    def test_import_lists_the_section_headings_it_found(self):
        src = Path(self.home, "cases.tsv")
        src.write_text(PASTED)
        out = self.ok("cases", "import", str(src), "--app", PACKAGE, "--columns", COLUMNS)
        self.assertIn("section headings: Notes app", out)

    def test_stdin_with_a_byte_order_mark(self):
        import io
        from unittest import mock
        with mock.patch("sys.stdin", io.StringIO("\ufeff" + CSV)):
            self.ok("cases", "import", "-", "--app", PACKAGE, "--name", "bom")
        self.assertIn("case LGN-1", Path(self.home, "plans", "bom.plan").read_text())

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
