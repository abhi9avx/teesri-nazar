"""The dashboard acceptance list (ARCHITECTURE.md, *Dashboard acceptance list*): every line
has tests, and every test named here exists, so a line can't quietly lose its check."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ACCEPTANCE = {
    "DA-01": ["test_returns_as_soon_as_the_user_sends_a_message",
              "test_wait_message_listens_then_reports_the_message"],
    "DA-02": ["test_an_info_note_does_not_wake_the_agent",
              "test_an_info_message_is_shown_but_the_command_still_runs",
              "test_the_already_typed_note_does_not_stop_the_agent"],
    "DA-03": ["test_type_now_types_into_the_focused_field_without_waiting_for_the_agent",
              "test_typing_waits_for_an_agent_step_in_progress",
              "test_an_agent_tap_waits_while_the_dashboard_is_typing"],
    "DA-04": ["test_two_secrets_sent_at_once_are_typed_one_after_the_other"],
    "DA-05": ["test_a_typing_failure_never_reveals_any_part_of_the_value",
              "test_secret_is_served_only_with_the_token_and_never_written"],
    "DA-06": ["test_a_tap_that_finds_nothing_is_recorded_with_the_reason",
              "test_typing_into_a_missing_field_is_recorded_without_the_text",
              "test_a_typing_failure_is_a_red_row_in_the_timeline",
              "test_a_failed_adb_command_records_why_without_secrets",
              "test_a_step_that_could_not_run_says_why"],
    "DA-07": ["test_the_status_line_never_gets_stuck",
              "test_the_time_shows_only_on_lines_it_belongs_to",
              "test_say_shows_the_step_and_a_live_listener_still_counts_as_listening",
              "test_a_listener_that_died_is_not_listening",
              "test_the_status_quotes_the_message_not_an_info_note_after_it"],
    "DA-08": ["test_your_message_is_never_shown_below_steps_that_came_after_it"],
    "DA-09": ["test_a_wait_that_found_the_other_screen_says_so",
              "test_a_real_timeout_names_the_time_and_an_old_event_without_one_still_reads",
              "test_ids_read_like_words_and_words_stay_as_written",
              "test_check_and_if_lines_name_the_element_in_words"],
    "DA-10": ["test_paused_with_only_an_info_note_is_still_a_pause"],
    "DA-11": ["test_a_message_the_background_listener_delivered_still_stops_the_flow",
              "test_a_message_delivered_while_the_flow_starts_still_stops_it"],
    "DA-12": ["test_runs_a_saved_route_in_one_command_and_logs_each_step",
              "test_an_unknown_flow_names_the_ones_there_are"],
    "DA-13": ["test_a_goto_reads_as_one_line_and_a_broken_route_says_what_it_expected",
              "test_screen_reads_for_the_map_are_not_timeline_rows"],
    "DA-14": ["test_not_run_cases_are_rows_with_their_reason",
              "test_layout_gives_not_run_its_own_block",
              "test_layout_skips_case_plan",
              "test_cli_11_stop_writes_a_not_run_row_per_unstarted_case"],
    "DA-16": ["test_the_dashboard_says_why_a_case_did_not_pass",
              "test_the_report_says_why_a_case_did_not_pass",
              "test_a_target_not_on_screen_blocks_the_case_and_names_the_miss_not_the_slowness"],
    "DA-15": ["test_a_run_that_starts_takes_over_the_open_page",
              "test_a_past_run_you_pick_while_one_runs_stays_until_another_starts",
              "test_with_nothing_running_the_page_shows_the_newest_run_and_never_an_older_one",
              "test_the_run_list_is_newest_start_first_whatever_touched_the_folders"],
}


def _test_names() -> set[str]:
    names = set()
    for f in (ROOT / "tests").glob("test_*.py"):
        names.update(re.findall(r"^\s+def (test_\w+)\(", f.read_text(), re.M))
    return names


class AcceptanceListTest(unittest.TestCase):
    def test_every_line_of_the_list_has_tests_and_nothing_else_is_listed(self):
        doc = (ROOT / "ARCHITECTURE.md").read_text()
        ids = re.findall(r"^\| (DA-\d+) \|", doc, re.M)
        self.assertTrue(ids, "the acceptance list is missing from ARCHITECTURE.md")
        self.assertEqual(sorted(ids), sorted(ACCEPTANCE))
        self.assertTrue(all(ACCEPTANCE[i] for i in ids))

    def test_every_named_test_exists(self):
        names = _test_names()
        missing = {i: [t for t in tests if t not in names] for i, tests in ACCEPTANCE.items()}
        self.assertEqual({i: t for i, t in missing.items() if t}, {})


if __name__ == "__main__":
    unittest.main()
