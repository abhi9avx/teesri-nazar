import unittest
from pathlib import Path

from teesri_nazar import screen as scr

XML = (Path(__file__).parent / "fixtures" / "login.xml").read_text()


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.screen = scr.parse(XML)

    def test_skips_zero_area_nodes_and_reads_size(self):
        self.assertEqual(len(self.screen.elements), 7)
        self.assertEqual(self.screen.size, (1080, 2400))
        self.assertEqual(self.screen.package, "com.example.app")

    def test_element_fields(self):
        login = self.screen.elements[4]
        self.assertEqual(login.text, "Login")
        self.assertEqual(login.short_id, "btn_login")
        self.assertEqual(login.center, (540, 860))
        self.assertFalse(login.enabled)
        self.assertTrue(self.screen.elements[3].password)

    def test_interesting_hides_bare_layout(self):
        shown = self.screen.interesting()
        self.assertNotIn(self.screen.elements[0], shown)
        self.assertIn(self.screen.elements[3], shown)  # password field without text


class CheckableTest(unittest.TestCase):
    """A Switch whose row is the tap target has no label and is not clickable itself (Settings > Internet)."""

    def setUp(self):
        self.screen = scr.parse(
            "<?xml version='1.0' ?><hierarchy rotation=\"0\">"
            "<node class=\"android.widget.FrameLayout\" bounds=\"[0,0][1080,2400]\">"
            "<node class=\"android.widget.Switch\" resource-id=\"com.android.settings:id/switchWidget\" "
            "checkable=\"true\" checked=\"false\" clickable=\"false\" bounds=\"[860,740][1000,840]\" />"
            "</node></hierarchy>")
        self.switch = self.screen.elements[1]

    def test_observe_shows_unlabelled_switch(self):
        self.assertIn(self.switch, self.screen.interesting())

    def test_unchecked_state_is_visible(self):
        self.assertIn("unchecked", self.switch.describe())


class FindTest(unittest.TestCase):
    def setUp(self):
        self.screen = scr.parse(XML)

    def find(self, **kw):
        el = scr.find(self.screen, scr.Selector(**kw))
        return el.text or el.desc or el.short_id if el else None

    def test_by_short_and_full_id(self):
        self.assertEqual(self.find(id="btn_login"), "Login")
        self.assertEqual(self.find(id="com.example.app:id/btn_login"), "Login")
        self.assertIsNone(self.find(id="login"))

    def test_text_is_case_and_space_insensitive(self):
        self.assertEqual(self.find(text="  welcome   BACK "), "Welcome back")

    def test_exact_text_falls_back_to_partial(self):
        self.assertEqual(self.find(text="Terms"), "Terms of service")

    def test_partial_does_not_match_when_exact_requested_with_id(self):
        self.assertIsNone(self.find(id="link", text="Terms"))

    def test_desc_and_nth(self):
        self.assertEqual(self.find(desc="phone number"), "Phone number")
        self.assertEqual(self.find(id="link", nth=1), "Terms of service")
        self.assertIsNone(self.find(id="link", nth=2))


class StableSelectorTest(unittest.TestCase):
    def setUp(self):
        self.screen = scr.parse(XML)

    def test_unique_id_wins(self):
        sel = scr.stable_selector(self.screen, self.screen.elements[4])
        self.assertEqual(sel.to_dict(), {"id": "btn_login"})

    def test_duplicate_id_falls_back_to_text(self):
        sel = scr.stable_selector(self.screen, self.screen.elements[6])
        self.assertEqual(sel.to_dict(), {"text": "Terms of service"})

    def test_password_text_is_never_used(self):
        sel = scr.stable_selector(self.screen, self.screen.elements[3])
        self.assertEqual(sel.to_dict(), {"id": "mpin_input"})

    def test_round_trip(self):
        sel = scr.Selector(id="link", text="Help", contains=True, nth=1)
        self.assertEqual(scr.Selector.from_dict(sel.to_dict()).to_dict(), sel.to_dict())


if __name__ == "__main__":
    unittest.main()
