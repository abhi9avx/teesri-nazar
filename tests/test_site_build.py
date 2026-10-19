import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
site_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_build)


CHANGELOG = """# Changelog

Intro text that is not a version.

## v2: Steer it live

2026-10-19 · [PR #2](https://github.com/abhi9avx/teesri-nazar/pull/2)

The dashboard became a control panel.

- A status line says what the agent is doing.
- `tnazar wait-message` hears a message
  while the agent is busy.

## v1: The tester

2026-10-16 to 2026-10-18

- Look, act, check, record.
"""


class ParseChangelogTest(unittest.TestCase):
    def test_versions_come_out_newest_first_with_slug_and_title(self):
        versions = site_build.parse_changelog(CHANGELOG)
        self.assertEqual([(v["slug"], v["title"]) for v in versions],
                         [("v2", "Steer it live"), ("v1", "The tester")])

    def test_first_line_is_the_meta_and_the_rest_renders_as_html(self):
        v2 = site_build.parse_changelog(CHANGELOG)[0]
        self.assertEqual(v2["meta"], '2026-10-19 · <a href="https://github.com/abhi9avx/'
                                     'teesri-nazar/pull/2">PR #2</a>')
        self.assertEqual(v2["html"],
                         "<p>The dashboard became a control panel.</p>\n"
                         "<ul>\n"
                         "<li>A status line says what the agent is doing.</li>\n"
                         "<li><code>tnazar wait-message</code> hears a message while the "
                         "agent is busy.</li>\n"
                         "</ul>")

    def test_html_in_the_changelog_is_escaped(self):
        v = site_build.parse_changelog("## v9: <b>Bold</b>\n\nmeta\n\n"
                                       "- `<script>` & *safe*\n")[0]
        self.assertEqual(v["title"], "&lt;b&gt;Bold&lt;/b&gt;")
        self.assertEqual(v["html"], "<ul>\n<li><code>&lt;script&gt;</code> &amp; "
                                    "<em>safe</em></li>\n</ul>")

    def test_a_link_to_a_non_web_url_stays_plain_text(self):
        v = site_build.parse_changelog("## v9: T\n\nmeta\n\n[x](javascript:alert(1))\n")[0]
        self.assertEqual(v["html"], "<p>[x](javascript:alert(1))</p>")


LAYOUT = ("<title>{{title}}</title><meta name=description content=\"{{description}}\">"
          "<link rel=canonical href=\"{{canonical}}\"><main>{{content}}</main>")


class BuildTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.src, self.out = root / "src", root / "out"
        (self.src / "docs").mkdir(parents=True)
        (self.src / "_layout.html").write_text(LAYOUT)
        (self.src / "index.html").write_text(
            "<!--\ntitle: Teesri Nazar\ndescription: The AI tester\n-->\n<h1>Home</h1>\n"
            "<ol>{{releases}}</ol>\n")
        (self.src / "docs" / "index.html").write_text(
            "<!--\ntitle: Docs\ndescription: How to use it\n-->\n<h1>Docs</h1>\n")
        (self.src / "about").mkdir()
        (self.src / "about" / "index.html").write_text(
            '<!--\ntitle: About "us"\ndescription: It means "the third eye" & more\n-->\n<h1>About</h1>\n')
        (self.src / "_docs-nav.html").write_text(
            '<nav><a class="docs-link" href="/docs/">Overview</a><a class="docs-link" href="/docs/start/">Start</a></nav>')
        (self.src / "docs" / "start").mkdir()
        (self.src / "docs" / "start" / "index.html").write_text(
            "<!--\ntitle: Start\ndescription: Start here\n-->\n{{include:_docs-nav.html}}\n<h1>Start</h1>\n"
            "{{toc}}\n<h2 id=\"need\">What you need</h2>\n<h2 id=\"run\">Run &amp; check</h2>\n")
        (self.src / "404.html").write_text("<!--\ntitle: Not found\ndescription: x\n-->\n<h1>404</h1>\n")
        (self.src / "site.css").write_text("body{}")
        changelog = root / "CHANGELOG.md"
        changelog.write_text(CHANGELOG)
        site_build.build(self.src, changelog, self.out, "https://teesrinazar.github.io")

    def read(self, rel):
        return (self.out / rel).read_text()

    def test_pages_are_wrapped_in_the_layout_with_their_own_canonical_url(self):
        self.assertEqual(self.read("docs/index.html"),
                         '<title>Docs</title><meta name=description content="How to use it">'
                         '<link rel=canonical href="https://teesrinazar.github.io/docs/">'
                         "<main><h1>Docs</h1>\n</main>")

    def test_quotes_in_a_page_title_or_description_cannot_break_the_tags(self):
        about = self.read("about/index.html")
        self.assertIn("<title>About &quot;us&quot;</title>", about)
        self.assertIn('content="It means &quot;the third eye&quot; &amp; more"', about)

    def test_a_version_description_is_its_first_sentence_of_plain_text(self):
        v2 = self.read("changelog/v2/index.html")
        self.assertIn('content="The dashboard became a control panel."', v2)
        v1 = self.read("changelog/v1/index.html")
        self.assertIn('content="Look, act, check, record."', v1)

    def test_a_page_can_include_a_partial_and_its_own_link_is_marked_current(self):
        start = self.read("docs/start/index.html")
        self.assertIn('<a class="docs-link" href="/docs/start/" aria-current="page">Start</a>', start)
        self.assertIn('<a class="docs-link" href="/docs/">Overview</a>', start)

    def test_toc_lists_the_pages_h2_headings(self):
        start = self.read("docs/start/index.html")
        self.assertIn('<ul class="toc-list"><li><a href="#need">What you need</a></li>'
                      '<li><a href="#run">Run &amp; check</a></li></ul>', start)

    def test_assets_are_copied_and_partials_are_not(self):
        self.assertEqual(self.read("site.css"), "body{}")
        self.assertFalse((self.out / "_layout.html").exists())

    def test_every_version_gets_its_own_page_and_the_index_links_to_them(self):
        v2 = self.read("changelog/v2/index.html")
        self.assertIn("<title>v2: Steer it live | Teesri Nazar changelog</title>", v2)
        self.assertIn('href="https://teesrinazar.github.io/changelog/v2/"', v2)
        self.assertIn("<li>A status line says what the agent is doing.</li>", v2)
        index = self.read("changelog/index.html")
        self.assertLess(index.index('href="/changelog/v2/"'), index.index('href="/changelog/v1/"'))

    def test_the_home_page_lists_the_newest_versions_from_the_changelog(self):
        home = self.read("index.html")
        self.assertIn('<ol><li><a href="/changelog/v2/"><strong>v2</strong> Steer it live</a></li>'
                      '<li><a href="/changelog/v1/"><strong>v1</strong> The tester</a></li></ol>', home)

    def test_sitemap_lists_every_page_but_the_404(self):
        sitemap = self.read("sitemap.xml")
        locs = sorted(l.strip()[5:-6] for l in sitemap.splitlines() if "<loc>" in l)
        base = "https://teesrinazar.github.io"
        self.assertEqual(locs, sorted([base + "/", base + "/docs/", base + "/docs/start/", base + "/about/", base + "/changelog/",
                                       base + "/changelog/v2/", base + "/changelog/v1/"]))


if __name__ == "__main__":
    unittest.main()
