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
    "Notes - delete a note\tVerify delete.\tUser is signed in; one note\t1. Sign in.\n2. Open it.\n3. Delete the note\t"
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
        cases = intake.read("Title\tSteps\tExpected\nT1\t1. a\n2. b\n\tDone\nT2\tx\tOk\n")
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
        self.assertIn("phone ", text)  # one cell, one scope: a number beside an OTP is hidden too

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


class ThirdReviewFixesTest(unittest.TestCase):
    def test_a_row_without_its_trailing_tabs_never_swallows_the_next(self):
        cases = intake.read("ID\tTitle\tExpected\tNotes\nTC-1\tLogin\tok\nTC-2\tLogout\n")
        self.assertEqual([(c.id, c.title, c.expects) for c in cases], [("TC-1", "Login", ["ok"]), ("TC-2", "Logout", [])])

    def test_short_rows_are_reported(self):
        report: dict = {}
        intake.read("ID\tTitle\tExpected\tNotes\nTC-1\tLogin\tok\nTC-2\tLogout\tfine\tn\n", report=report)
        self.assertEqual(report["short"], ["TC-1 (3 of 4 cells)"])

    def test_a_multiline_cell_closed_by_a_line_with_tabs_still_joins(self):
        [c] = intake.read("Title\tSteps\tExpected\tPriority\nT\t1. a\n2. b\n3. c\tShown\tP1\n")
        self.assertEqual((c.steps, c.expects, c.fields["priority"]), (["1. a", "2. b", "3. c"], ["Shown"], "P1"))

    def test_a_stray_quote_is_just_a_character(self):
        cases = intake.read('ID\tTitle\tExpected\nTC-1\t"open quote never closed\tok\nTC-2\tb\tc\n')
        self.assertEqual([(c.id, c.title) for c in cases], [("TC-1", '"open quote never closed'), ("TC-2", "b")])

    def test_a_quote_in_the_middle_of_a_cell_is_kept(self):
        [c] = intake.read('ID\tTitle\tExpected\nTC-1\t"quoted" start\tok\n')
        self.assertEqual(c.title, '"quoted" start')

    def test_secret_values_in_any_phrasing_are_hidden(self):
        for cell, gone, kept in (("Enter OTP 123456 then wait", "123456", "then wait"),
                                 ("password is hunter2", "hunter2", "password is"),
                                 ("Token = abc123; next", "abc123", "next"),
                                 ("pin code: 560001", None, "560001"),
                                 ("spinner shows", None, "spinner shows")):
            text = intake.to_plan(intake.read(f"Title,Expected,Test data\nT,E,{cell}\n"), app="a.b", name="s", source="x")
            if gone:
                self.assertNotIn(gone, text, cell)
            self.assertIn(kept, text, cell)

    def test_columns_not_copied_are_reported(self):
        report: dict = {}
        intake.read("Title,Expected,PIN code,OTP\nT,E,560001,1234\n", report=report)
        self.assertEqual(report["not_copied"], ["PIN code", "OTP"])


class FourthReviewFixesTest(unittest.TestCase):
    def test_a_one_cell_row_never_absorbs_the_next_full_row(self):
        report: dict = {}
        cases = intake.read("Title\tSteps\tExpected\nA\nB\ts\tx\n", report=report)
        self.assertEqual([(c.title, c.section, c.steps) for c in cases], [("B", "A", ["s"])])
        self.assertEqual(report["headings"], ["A"])

    def test_an_id_alone_in_tsv_is_a_case(self):
        cases = intake.read("ID\tTitle\tExpected\nT1\nT2\tB\tx\n")
        self.assertEqual([c.id for c in cases], ["T1", "T2"])

    def test_an_unquoted_line_after_a_complete_row_is_never_merged_into_it(self):
        report: dict = {}
        cases = intake.read("Title\tExpected\nA\tline1\nline2\nB\tx\n", report=report)
        self.assertEqual([(c.title, c.expects) for c in cases], [("A", ["line1"]), ("line2", []), ("B", ["x"])])
        self.assertEqual(report["joined"], [])

    def test_titles_are_masked_too_and_punctuation_kept(self):
        text = intake.to_plan(intake.read("Title,Expected,Test data\n"
                                          "Login with password: hunter2,E,\"MPIN: 1 2 3 4; pin=5678, otp 9999\"\n"),
                              app="a.b", name="s", source="x")
        for value in ("hunter2", "1 2 3 4", "5678", "9999"):
            self.assertNotIn(value, text)
        self.assertIn("pin=••••,", text)

    def test_a_camel_case_secret_header_is_not_copied(self):
        report: dict = {}
        intake.read("Title,Expected,PINCode\nT,E,4321\n", report=report)
        self.assertEqual(report["not_copied"], ["PINCode"])

    def test_id_first_tables_keep_headings_that_cannot_be_ids(self):
        report: dict = {}
        cases = intake.read("ID,Title,Expected\nOverdue users,,\nX-1,a,b\n", report=report)
        self.assertEqual([(c.id, c.section) for c in cases], [("X-1", "Overdue users")])
        self.assertEqual(report["headings"], ["Overdue users"])


class FinalReviewFixesTest(unittest.TestCase):
    def test_h1_a_one_cell_line_after_a_complete_row_is_its_own_row(self):
        report: dict = {}
        cases = intake.read("ID\tTitle\tSteps\tExpected\nTC-1\tA\ts\te\nNotes app\nTC-2\tB\ts\te\nTC-3\n", report=report)
        self.assertEqual([(c.id, c.expects, c.section) for c in cases],
                         [("TC-1", ["e"], ""), ("TC-2", ["e"], "Notes app"), ("TC-3", [], "Notes app")])
        self.assertEqual(report["headings"], ["Notes app"])
        self.assertEqual(report["joined"], [])

    def test_h2_a_word_without_a_digit_under_an_id_column_is_a_heading(self):
        cases = intake.read("ID\tTitle\tSteps\tExpected\nLogin\nTC-1\tA\ts\te\n")
        self.assertEqual([(c.id, c.section) for c in cases], [("TC-1", "Login")])

    def test_m1_masking_hides_only_the_value(self):
        for cell, gone, kept in (("Verify OTP-based login", None, "OTP-based login"),
                                 ("PIN is masked", None, "PIN is masked"),
                                 ("Password is required", None, "Password is required"),
                                 ("Enter OTP: 123456 and tap Verify", "123456", "and tap Verify"),
                                 ("pin-code 560001", None, "560001"),
                                 ("otp 12 34 then go", "12 34", "then go"),
                                 ("OTP code 123456", "123456", "OTP code"),
                                 ("password: hunter2; now", "hunter2", "now")):
            text = intake.to_plan(intake.read(f"Title,Expected,Test data\nT,E,{cell}\n"), app="a.b", name="s", source="x")
            if gone:
                self.assertNotIn(gone, text, cell)
            self.assertIn(kept, text, cell)

    def test_m2_a_quoted_cell_across_lines_and_tabs_is_reported(self):
        report: dict = {}
        intake.read('ID\tTitle\tExpected\nTC-1\t"Hello\tstep\nok"\tdone\nTC-2\tPay\tok\n', report=report)
        self.assertEqual(report["joined"], ["TC-1"])


class LastReviewFixesTest(unittest.TestCase):
    def masked(self, cell):
        return intake.to_plan(intake.read(f"Title,Expected,Test data\nT,E,\"{cell}\"\n"), app="a.b", name="s", source="x")

    def test_numbers_with_separators_and_dash_phrasing_are_hidden(self):
        for cell, gone in (("otp - 1234", "1234"), ("OTP - 123456", "123456"), ("password - hunter2", "hunter2"),
                           ("otp 1234-5678", "5678"), ("otp 1234abcd", "abcd"), ("pin 12a4", "12a4"),
                           ("OTP 1234_5678", "5678"), ("OTP 12.34", "34"), ("Use OTP 0000/1111", "1111")):
            self.assertNotIn(gone, self.masked(cell), cell)

    def test_a_password_after_a_colon_is_hidden_up_to_the_next_separator(self):
        for cell, gone in (("password: hunter 2", " 2"), ("password: 'p@ss w0rd'", "w0rd"),
                           ("secret: my secret phrase", "phrase")):
            self.assertNotIn(gone, self.masked(cell), cell)
        self.assertIn("then go", self.masked("password: x y; then go"))

    def test_a_lone_id_row_never_takes_the_lines_after_it(self):
        report: dict = {}
        cases = intake.read("ID\tTitle\tSteps\tExpected\nTC1\tA\ts\te\nTC2\nNotes\nTC-3\tPay\tx\ty\n", report=report)
        self.assertEqual([(c.id, c.section) for c in cases], [("TC1", ""), ("TC2", ""), ("TC-3", "Notes")])


class HeadingSecretTest(unittest.TestCase):
    def test_a_heading_with_a_secret_is_masked_in_the_plan_and_the_report(self):
        report: dict = {}
        cases = intake.read("Title,Steps,Expected\nLogin OTP 123456 section,,\nA,s,e\n", report=report)
        text = intake.to_plan(cases, app="a.b", name="s", source="x")
        self.assertNotIn("123456", text)
        self.assertNotIn("123456", " ".join(report["headings"]))

    def test_a_header_with_a_line_break_imports(self):
        [c] = intake.read('Title,Expected,"Owner\nteam"\nT,E,QA\n')
        text = intake.to_plan([c], app="a.b", name="s", source="x")
        from teesri_nazar import plan
        plan.parse(text, "x.plan", draft=True)


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

    def test_import_prints_every_decision_it_made(self):
        src = Path(self.home, "cases.tsv")
        src.write_text("Title\tSteps\tExpected\tOTP\nA\t1. a\n2. b\n3. c\tline1\t1234\nB\tx\ty\t5678\n")
        out = self.ok("cases", "import", str(src), "--app", PACKAGE)
        self.assertIn("lines joined into a cell above (a line break without quotes): TC-01", out)
        self.assertIn("columns not copied (they look like secrets): OTP", out)
        self.assertNotIn("1234", Path(self.home, "plans", "cases.plan").read_text())

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


class BranchReviewFixesTest(unittest.TestCase):
    def test_secrets_glued_spelled_out_or_after_the_number_are_hidden(self):
        for cell, gone, kept in (("MPIN-1234", "1234", "MPIN-"),
                                 ("OTP as 123456", "123456", "OTP as"),
                                 ("otp1234", "1234", "otp"),
                                 ("Password hunter2", "hunter2", "Password"),
                                 ("pwd: abc123", "abc123", "pwd:"),
                                 ("Credentials: user1/Pass@123", "Pass@123", "Credentials:"),
                                 ("Enter 482913 as the OTP", "482913", "as the OTP"),
                                 ("Password is required", None, "Password is required"),
                                 ("Password is required.", None, "Password is required."),
                                 ("Verify OTP-based login", None, "OTP-based login"),
                                 ("pin code: 560001", None, "560001"),
                                 ('OTP: "482913"', "482913", "OTP:"),
                                 ("OTP (482913)", "482913", "OTP"),
                                 ("OTP=>482913", "482913", "OTP"),
                                 ("PIN#1234", "1234", "PIN"),
                                 ("otp is: 1234", "1234", "otp is"),
                                 ("PIN number 1234", "1234", "PIN number"),
                                 ("pin no 1234", "1234", "pin no"),
                                 ("Password -> hunter2", "hunter2", "Password"),
                                 ("password (min 8)", None, "password (min 8)"),
                                 ("password - min 8 chars", "min 8 chars", "password - "),
                                 ("Pass - user sees home", None, "Pass - user sees home"),
                                 ("Pass: Yes", None, "Pass: Yes"),
                                 ("Tap Pass 3 times", None, "Tap Pass 3 times"),
                                 ("Pass: hunter2", "hunter2", "Pass:"),
                                 ("User: a / Pass: Abc@123", "Abc@123", "User: a"),
                                 ("change MPIN 1234 to 5678", "5678", "change MPIN"),
                                 ("set PIN to 1234", "1234", "set PIN to"),
                                 ("OTP was 482913", "482913", "OTP was"),
                                 ("(PIN) 1234", "1234", "(PIN)"),
                                 ("OTP → 482913", "482913", "OTP"),
                                 ("Password-hunter2", "hunter2", "Password"),
                                 ("Verify OTP is 6 digits", None, "Verify OTP is 6 digits"),
                                 ("Enter 6-digit OTP", None, "Enter 6-digit OTP"),
                                 ("Pin #2 on map", None, "Pin #2 on map"),
                                 ("Pass: Yes", None, "Pass: Yes")):
            text = intake.to_plan(intake.read(f"Title,Expected,Test data\nT,E,{cell}\n"), app="a.b", name="s", source="x")
            if gone:
                self.assertNotIn(gone, text, cell)
            self.assertIn(kept, text, cell)

    def test_an_id_that_starts_with_a_secret_word_still_imports(self):
        cases = intake.read("ID,Title,Expected\nOTP-001,Verify OTP,ok\nPIN-02,Change PIN,ok\n")
        self.assertEqual([c.id for c in cases], ["OTP-001", "PIN-02"])

    def test_a_password_that_starts_like_a_result_is_hidden(self):
        for cell in ("user: test1 pass: Pass@123", "pass: Pass@123", "pass: Yes@123"):
            text = intake.to_plan(intake.read(f"Title,Expected,Test data\nT,E,{cell}\n"), app="a.b", name="s", source="x")
            self.assertNotIn("@123", text, cell)

    def test_expected_results_hide_secrets_but_keep_amounts_versions_and_durations(self):
        kept = ["Balance ₹1000 shown after PIN entry", "OTP valid for 120 seconds", "OTP screen on iOS 17.2",
                "Locked after 3 attempts with a wrong PIN"]
        hidden = {"Use password hunter2 → dashboard": "hunter2", "set PIN to 1234 then Home": "1234",
                  "change MPIN 1234 to 5678 succeeds": "5678", "Enter the OTP received (482913) and see Home": "482913",
                  "password hunter2 minutes": "hunter2", "set PIN to 1234 seconds": "1234"}
        rows = "".join(f"T{i},{e}\n" for i, e in enumerate(kept + list(hidden)))
        expects = [c.expects[0] for c in intake.read("Title,Expected\n" + rows)]
        self.assertEqual(expects[:len(kept)], kept)
        for got, value in zip(expects[len(kept):], hidden.values()):
            self.assertNotIn(value, got)
        [c] = intake.read("Title,Expected,Test data\nT,E,Pay ₹5000 then enter PIN 1234\n")
        self.assertEqual(c.fields["data"], "Pay ₹5000 then enter PIN ••••")

    def test_a_pass_column_is_a_secret_but_a_pass_fail_column_is_not(self):
        report: dict = {}
        [c] = intake.read("Title,Expected,Pass,User Pass,Pass/Fail\nLogin,Home,hunter,welcome,Fail\n", report=report)
        self.assertEqual(report["not_copied"], ["Pass", "User Pass"])
        self.assertEqual(c.fields, {"Pass/Fail": "Fail"})

    def test_a_password_without_digits_is_hidden_and_words_about_it_stay(self):
        for cell, gone, kept in (("login with password welcome", "welcome", "login with password"),
                                 ("Password is Hunter!", "Hunter", "Password is"),
                                 ("Use password Hunter#two", "Hunter#two", "Use password"),
                                 ("otp=abc", "abc", "otp="),
                                 ("Credentials user/abc", "user/abc", "Credentials"),
                                 ("Password reset link is sent", None, "Password reset link is sent"),
                                 ("Password field is masked", None, "Password field is masked"),
                                 ("Password is required.", None, "Password is required."),
                                 ("Enter the password and tap Login", None, "Enter the password and tap Login"),
                                 ("OTP: sent", None, "OTP: sent")):
            text = intake.to_plan(intake.read(f"Title,Expected,Test data\nT,E,{cell}\n"), app="a.b", name="s", source="x")
            if gone:
                self.assertNotIn(gone, text, cell)
            self.assertIn(kept, text, cell)

    def test_pr_review_leaks_are_hidden(self):
        for cell, gone in (("Enter OTP sent; for UAT use 123456", "123456"),
                           ("Enter verification code 482913", "482913"),
                           ("Enter the code: 123456", "123456"),
                           ("User: test1 / Pw: Hunter22", "Hunter22"),
                           ("Password\nTest@123", "Test@123"),
                           ("Enter password Welcome@abc", "Welcome@abc"),
                           ("Valid PIN (1234)", "1234")):
            text = intake.to_plan(intake.read(f'Title,Expected,Test data\nT,E,"{cell}"\n'), app="a.b", name="s", source="x")
            self.assertNotIn(gone, text, cell)
        report: dict = {}
        intake.read("Title,Expected,Pw\nT,E,Hunter22\n", report=report)
        self.assertEqual(report["not_copied"], ["Pw"])
        with self.assertRaises(intake.IntakeError):
            intake.read("ID,Title,Expected\nOTP123456,T,E\n")

    def test_ranges_and_times_in_expected_results_stay(self):
        expected = ["Password must be 8-16 characters", "Resend OTP enabled after 00:30", "Valid PIN (1234) unlocks"]
        got = [c.expects[0] for c in intake.read("Title,Expected\n" + "".join(f"T{i},{e}\n" for i, e in enumerate(expected)))]
        self.assertEqual(got[:2], expected[:2])
        self.assertEqual(got[2], "Valid PIN (••••) unlocks")

    def test_an_id_holding_a_value_is_refused_without_showing_it(self):
        for row in ("OTP:482913,T,E", "OTP 482913,T,E"):
            with self.assertRaises(intake.IntakeError) as cm:
                intake.read("ID,Title,Expected\n" + row + "\n")
            self.assertNotIn("482913", str(cm.exception))

    def test_a_long_run_of_digits_and_spaces_is_masked_quickly(self):
        import time
        began = time.monotonic()
        intake.read("Title,Expected,Test data\nT,E,OTP " + "1 " * 20000 + "\n")
        self.assertLess(time.monotonic() - began, 1.0)

    def test_a_sheet_without_a_header_never_echoes_a_secret(self):
        with self.assertRaises(intake.IntakeError) as cm:
            intake.read("TC-1,Login,OTP 1234,secret Pass@1,9876543210\n")
        msg = str(cm.exception)
        for value in ("1234", "Pass@1", "9876543210"):
            self.assertNotIn(value, msg)
        self.assertIn("Login", msg)


if __name__ == "__main__":
    unittest.main()
