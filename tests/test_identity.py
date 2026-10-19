"""Screen identity: which screen is this, independent of balances, rows and scroll position."""

import unittest

from fakedevice import PACKAGE, node, screen_xml, server_xml
from teesri_nazar import ident
from teesri_nazar import screen as scr

TOOLBAR = node(id="toolbar", cls="androidx.appcompat.widget.Toolbar", clickable=False, bounds=(0, 80, 1080, 220))


def title(text):
    return node(text=text, clickable=False, bounds=(160, 110, 700, 190))


def rows(texts, top=400):
    out = [node(id="list", cls="androidx.recyclerview.widget.RecyclerView", clickable=False, scrollable=True,
                bounds=(0, top, 1080, 2000))]
    for i, t in enumerate(texts):
        out.append(node(id="row_title", text=t, bounds=(0, top + i * 150, 1080, top + i * 150 + 140)))
    return out


def home(balance="₹1,000.00", greeting="Hi Asha", row_texts=("Coffee", "Rent")):
    return [
        TOOLBAR, title("Home"),
        node(text=greeting, clickable=False, bounds=(60, 240, 900, 300)),
        node(id="balance", text=balance, clickable=False, bounds=(60, 320, 900, 380)),
        node(id="btn_savings", text="Savings", cls="android.widget.Button", bounds=(60, 2100, 500, 2200)),
        node(id="btn_loans", text="Loans", cls="android.widget.Button", bounds=(560, 2100, 1020, 2200)),
        *rows(row_texts),
    ]


def ident_of(nodes, package=PACKAGE, app=PACKAGE, xml=screen_xml):
    return ident.identify(scr.parse(xml(nodes, package=package)), app=app)


class SameScreenTest(unittest.TestCase):
    def test_balances_names_and_rows_do_not_change_the_screen(self):
        a = ident_of(home())
        b = ident_of(home(balance="₹52.10", row_texts=("Fuel", "Groceries", "Salary", "Movie")))
        self.assertEqual(a.kind, "screen")
        self.assertTrue(ident.same(a, b))

    def test_digits_in_labels_are_ignored(self):
        a = ident_of([title("Orders"), node(text="3 new", bounds=(0, 500, 300, 600))])
        b = ident_of([title("Orders"), node(text="12 new", bounds=(0, 500, 300, 600))])
        self.assertTrue(ident.same(a, b))

    def test_both_readers_give_the_same_identity(self):
        self.assertEqual(ident_of(home()).id, ident_of(home(), xml=server_xml).id)

    def test_keyboard_nodes_are_not_part_of_the_screen(self):
        keys = [dict(node(text=k, bounds=(i * 100, 2000, i * 100 + 90, 2100)), pkg="com.android.ime")
                for i, k in enumerate("qwertyuiop")]
        mixed = scr.parse(_two_packages(home(), keys))
        self.assertEqual(ident.identify(mixed, app=PACKAGE).id, ident_of(home()).id)

    def test_one_extra_badge_is_still_the_same_screen(self):
        base = home()
        badge = node(id="badge_new", text="New", clickable=False, bounds=(500, 2100, 560, 2150))
        self.assertTrue(ident.same(ident_of(base), ident_of(base + [badge])))


class DifferentScreenTest(unittest.TestCase):
    def test_different_titles_never_merge(self):
        a = ident_of([TOOLBAR, title("Passbook"), *rows(["x"])])
        b = ident_of([TOOLBAR, title("Loan statement"), *rows(["x"])])
        self.assertFalse(ident.same(a, b))
        self.assertNotEqual(a.id, b.id)

    def test_different_buttons_differ(self):
        a = ident_of([node(id="btn_a", text="A"), node(id="btn_b", text="B"), node(id="btn_c", text="C")])
        b = ident_of([node(id="btn_x", text="X"), node(id="btn_y", text="Y"), node(id="btn_z", text="Z")])
        self.assertFalse(ident.same(a, b))

    def test_compose_screens_without_ids_differ_by_their_buttons(self):
        a = ident_of([node(text="Orders"), node(text="Wishlist"), node(text="Profile")])
        b = ident_of([node(text="Track"), node(text="Cancel order"), node(text="Help")])
        self.assertFalse(ident.same(a, b))


class UntitledMenuTest(unittest.TestCase):
    """Found on a real app: a profile page with no title, all one scroll view, whose own list
    content varies between visits. Its menu labels are what say which page it is."""

    def page(self, menu, extra=()):
        scroll = node(cls="android.widget.ScrollView", clickable=False, scrollable=True, bounds=(0, 200, 1080, 2400))
        icons = [node(cls="android.widget.ImageView", bounds=(40, 100, 150, 190)),
                 node(cls="android.widget.ImageView", bounds=(930, 100, 1040, 190))]
        items = [node(desc=m, cls="android.widget.ImageView", bounds=(0, 1700 + 120 * i, 1080, 1800 + 120 * i))
                 for i, m in enumerate(menu)]
        return [scroll, *icons, *items, *extra]

    def test_the_same_menu_is_the_same_page_whatever_else_loaded(self):
        menu = ["Help & support", "Profile details", "App settings", "Reports & documents", "Pricing"]
        header = node(id="accountsSettingsSectionHeader", desc="ACCOUNTS & SETTINGS", clickable=False,
                      bounds=(0, 1600, 1080, 1680))
        self.assertTrue(ident.same(ident_of(self.page(menu)), ident_of(self.page(menu, [header]))))

    def test_different_menus_are_different_pages(self):
        a = ident_of(self.page(["Help & support", "Profile details", "App settings"]))
        b = ident_of(self.page(["Wi-Fi", "Bluetooth", "Hotspot"]))
        self.assertFalse(ident.same(a, b))

    def test_a_title_seen_on_one_visit_only_does_not_split_the_page(self):
        menu = ["Help & support", "Profile details", "App settings", "Pricing"]
        with_title = self.page(menu, [node(text="FAQs", clickable=False, bounds=(200, 100, 600, 190))])
        self.assertTrue(ident.same(ident_of(self.page(menu)), ident_of(with_title)))
        self.assertTrue(ident.match(ident_of(with_title), {ident_of(self.page(menu)).id: ident_of(self.page(menu))}))

    def test_rows_with_amounts_are_not_menu(self):
        a = ident_of(self.page(["Help & support", "Pricing", "Rent ₹12,000"]))
        b = ident_of(self.page(["Help & support", "Pricing", "Fuel ₹900"]))
        self.assertTrue(ident.same(a, b))


class TabsTest(unittest.TestCase):
    def tabs(self, selected):
        out = [TOOLBAR, title("Bank")]
        for i, t in enumerate(["Home", "Pay", "Rewards"]):
            n = node(id=f"tab_{t.lower()}", text=t, bounds=(i * 300, 2200, i * 300 + 300, 2400))
            n["selected"] = t == selected
            out.append(n)
        return out

    def test_tabs_sharing_a_title_and_frame_differ_by_the_selected_tab(self):
        self.assertFalse(ident.same(ident_of(self.tabs("Home")), ident_of(self.tabs("Rewards"))))
        self.assertTrue(ident.same(ident_of(self.tabs("Pay")), ident_of(self.tabs("Pay"))))


class KindTest(unittest.TestCase):
    def test_webview_covering_the_screen_is_opaque(self):
        i = ident_of([node(cls="android.webkit.WebView", clickable=False, bounds=(0, 0, 1080, 2400))])
        self.assertEqual(i.kind, "opaque")

    def test_flutter_with_semantics_is_a_normal_screen(self):
        view = node(cls="io.flutter.embedding.android.FlutterView", clickable=False, bounds=(0, 0, 1080, 2400))
        labels = [node(desc=d, bounds=(0, 200 * i, 1080, 200 * i + 150)) for i, d in enumerate(["A", "B", "C", "D"])]
        self.assertEqual(ident_of([view, *labels]).kind, "screen")

    def test_spinner_only_is_loading(self):
        i = ident_of([node(cls="android.widget.ProgressBar", clickable=False, bounds=(500, 1100, 580, 1180))])
        self.assertEqual(i.kind, "loading")

    def test_no_element_of_the_app_is_foreign(self):
        i = ident_of([node(text="Allow"), node(text="Don't allow")], package="com.android.permissioncontroller")
        self.assertEqual(i.kind, "foreign")

    def test_transparent_kinds_have_no_id(self):
        i = ident_of([node(cls="android.widget.ProgressBar", clickable=False, bounds=(500, 1100, 580, 1180))])
        self.assertEqual(i.id, "")


class PrivacyTest(unittest.TestCase):
    def test_tokens_are_hashed_and_hold_no_text(self):
        i = ident_of(home())
        blob = repr(i)
        for word in ("Asha", "1,000", "Coffee", "Rent", "Savings", "Home"):
            self.assertNotIn(word, blob)

    def test_typed_values_and_passwords_are_not_part_of_identity(self):
        a = ident_of([node(id="phone", cls="android.widget.EditText", text="98765"), node(id="go", text="Go")])
        b = ident_of([node(id="phone", cls="android.widget.EditText", text=""), node(id="go", text="Go")])
        self.assertEqual(a.id, b.id)

    def test_password_screen_is_marked_secure(self):
        i = ident_of([node(id="mpin", cls="android.widget.EditText", password=True), node(id="go", text="Go")])
        self.assertTrue(i.secure)


class MatchTest(unittest.TestCase):
    def test_match_finds_the_stored_screen_and_never_drifts(self):
        a = ident.Ident("aaaaaaaa", "screen", frozenset("abcdefghij"), "t", False)
        b = ident.Ident("bbbbbbbb", "screen", frozenset("abcdefghiK"), "t", False)   # ~a
        c = ident.Ident("cccccccc", "screen", frozenset("abcdLMNOPQ"), "t", False)   # far from a
        stored = {a.id: a}
        self.assertEqual(ident.match(b, stored), "aaaaaaaa")
        self.assertIsNone(ident.match(c, stored))


class SettingsShapeTest(unittest.TestCase):
    """Found on a real device: every settings page is one scroll view with the same row ids, and the
    page title is the description of a collapsing toolbar inside that scroll view."""

    def page(self, title_desc, rows_):
        content = node(id="content_parent", cls="android.widget.ScrollView", clickable=False, scrollable=True,
                       bounds=(0, 0, 1080, 2400))
        bar = node(id="collapsing_toolbar", desc=title_desc, cls="android.widget.FrameLayout", clickable=False,
                   bounds=(0, 60, 1080, 220))
        up = node(desc="Navigate up", cls="android.widget.ImageButton", bounds=(40, 90, 150, 190))
        items = [node(id="title", text=t, clickable=False, bounds=(100, 300 + 200 * i, 900, 360 + 200 * i))
                 for i, t in enumerate(rows_)]
        return [content, bar, up, *items]

    def test_title_comes_from_a_toolbar_description_inside_the_scroll_view(self):
        a = ident_of(self.page("Network & internet", ["Internet", "SIMs"]))
        b = ident_of(self.page("Connected devices", ["Pair new device"]))
        self.assertTrue(a.title)
        self.assertFalse(ident.same(a, b))

    def test_a_page_whose_list_is_still_loading_is_the_same_page(self):
        loading = self.page("Internet", ["Wi-Fi"]) + [node(id="progress_bar", cls="android.widget.ProgressBar",
                                                          clickable=False, bounds=(0, 220, 1080, 230))]
        loaded = self.page("Internet", ["Wi-Fi", "Mobile data"]) + [
            node(id="settings_button", desc="Settings", bounds=(900, 900, 1000, 1000)),
            node(id="switchWidget", cls="android.widget.Switch", bounds=(900, 1100, 1000, 1200))]
        self.assertTrue(ident.same(ident_of(loading), ident_of(loaded)))

    def test_two_pages_with_the_same_title_but_different_rows_stay_apart(self):
        def page(ids):
            content = node(id="content", cls="android.widget.ScrollView", clickable=False, scrollable=True,
                           bounds=(0, 0, 1080, 2400))
            bar = node(id="collapsing_toolbar", desc="Settings", cls="android.widget.FrameLayout", clickable=False,
                       bounds=(0, 60, 1080, 220))
            rows_ = [node(id=i, text=i, bounds=(0, 300 + 200 * n, 1080, 480 + 200 * n)) for n, i in enumerate(ids)]
            return [content, bar, *rows_]
        a = ident_of(page(["security", "notifications"]))
        b = ident_of(page(["change_mpin", "biometric"]))
        self.assertFalse(ident.same(a, b))
        self.assertTrue(ident.same(a, ident_of(page(["security", "notifications", "privacy"]))))

    def test_a_scrolling_marquee_is_not_a_selected_tab(self):
        marquee = node(id="ticker", text="Hi Asha", clickable=False, bounds=(0, 300, 1080, 360))
        marquee["selected"] = True
        other = dict(marquee, text="Hi Ravi")
        self.assertTrue(ident.same(ident_of(home() + [marquee]), ident_of(home() + [other])))

    def test_history_pages_are_not_money_screens_but_confirm_and_pin_are(self):
        for t, guarded in (("Payment history", False), ("Transfer history", False), ("Pay later", False),
                           ("Confirm payment", True), ("Enter MPIN", True)):
            self.assertEqual(ident_of([TOOLBAR, title(t), node(id="go", text="Go")]).secure, guarded, t)

    def test_untitled_screens_never_merge_by_similarity(self):
        a = ident.Ident("aaaaaaaa", "screen", frozenset("abcde"), "", False)
        b = ident.Ident("bbbbbbbb", "screen", frozenset("abcdX"), "", False)
        self.assertFalse(ident.same(a, b))
        self.assertIsNone(ident.match(b, {a.id: a}))


def _two_packages(app_nodes, other_nodes) -> str:
    xml = screen_xml(app_nodes)
    extra = screen_xml([{k: v for k, v in n.items() if k != "pkg"} for n in other_nodes], package="com.android.ime")
    inner = extra.split("<node ", 2)[2].rsplit("</node>", 1)[0]
    return xml.replace("</node></hierarchy>", "<node " + inner + "</node></hierarchy>")


if __name__ == "__main__":
    unittest.main()
