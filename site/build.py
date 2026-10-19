"""Build teesrinazar.github.io from site/src and CHANGELOG.md. Standard library only."""
import argparse
import html
import re
import shutil
from pathlib import Path

VERSION_HEADING = re.compile(r"^## (v\d+): (.+)$")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
EMPHASIS = re.compile(r"\*([^*]+)\*")
A_TAG = re.compile(r'(<a href="[^"]*">)')


def inline(text):
    """Escape text, then render `code`, [links](https://...) and *emphasis*."""
    out = []
    for i, part in enumerate(text.split("`")):
        if i % 2:
            out.append("<code>" + html.escape(part, quote=False) + "</code>")
        else:
            part = html.escape(part, quote=False)
            part = LINK.sub(lambda m: '<a href="%s">%s</a>' % (m.group(2).replace('"', "&quot;"),
                                                              m.group(1)), part)
            out.append("".join(p if i % 2 else EMPHASIS.sub(r"<em>\1</em>", p)
                               for i, p in enumerate(A_TAG.split(part))))
    return "".join(out)


def _blocks(lines):
    """Split lines into blocks at blank lines."""
    blocks, cur = [], []
    for line in lines:
        if line.strip():
            cur.append(line)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _render(block):
    if not block[0].startswith("- "):
        return "<p>" + inline(" ".join(l.strip() for l in block)) + "</p>"
    items = []
    for line in block:
        if line.startswith("- "):
            items.append(line[2:].strip())
        else:
            items[-1] += " " + line.strip()
    return "<ul>\n" + "".join("<li>" + inline(i) + "</li>\n" for i in items) + "</ul>"


def parse_changelog(text):
    """Versions in file order: slug, title, meta (first paragraph) and html (the rest)."""
    versions = []
    for line in text.splitlines():
        m = VERSION_HEADING.match(line)
        if m:
            versions.append({"slug": m.group(1), "title": html.escape(m.group(2).strip(), quote=False),
                             "lines": []})
        elif line.startswith("# "):
            continue
        elif line.startswith("## "):
            raise ValueError("CHANGELOG.md: %r is not a '## vN: Title' heading" % line)
        elif versions:
            versions[-1]["lines"].append(line)
    for v in versions:
        blocks = _blocks(v.pop("lines"))
        v["meta"] = inline(" ".join(l.strip() for l in blocks[0])) if blocks else ""
        v["html"] = "\n".join(_render(b) for b in blocks[1:])
    return versions


HEADER = re.compile(r"\A<!--\n(.*?)\n-->\n", re.S)
INCLUDE = re.compile(r"\{\{include:([\w.-]+)\}\}")
DESCRIPTION_META = re.compile(r'<meta name="?description"?[^>]*>')
H2 = re.compile(r'<h2 id="([^"]+)">(.*?)</h2>')
SECTIONS = ("/docs/", "/changelog/", "/about/")


def _page(layout, path, title, description, content, base_url):
    """Wrap content in the layout; the nav link of the page's section gets aria-current."""
    page = (layout.replace("{{title}}", title)
                  .replace("{{description}}", description)
                  .replace("{{canonical}}", base_url + path)
                  .replace("{{base}}", base_url)
                  .replace("{{content}}", content))
    for section in SECTIONS:
        if path.startswith(section):
            page = page.replace('<a href="%s">' % section, '<a href="%s" aria-current="page">' % section, 1)
    current = 'class="docs-link" href="%s"' % path
    return page.replace(current, current + ' aria-current="page"')


def _content(src, text):
    """Fill {{include:_name.html}} from src, then {{toc}} from the page's own h2 headings."""
    def include(m):
        part = src / m.group(1)
        if not part.is_file():
            raise ValueError("{{include:%s}}: no such partial in %s" % (m.group(1), src))
        return part.read_text(encoding="utf-8")
    text = INCLUDE.sub(include, text)
    items = "".join('<li><a href="#%s">%s</a></li>' % h for h in H2.findall(text))
    return text.replace("{{toc}}", '<ul class="toc-list">%s</ul>' % items)


def _url_path(rel):
    """'index.html' -> '/', 'docs/index.html' -> '/docs/', '404.html' -> '/404.html'."""
    rel = rel.replace("\\", "/")
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[:-len("index.html")]
    return "/" + rel


def _summary(body):
    """The first paragraph or list item as plain text, short enough for a meta description."""
    m = re.search(r"<(p|li)>(.*?)</\1>", body)
    text = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))) if m else ""
    if len(text) > 155:
        text = text[:154].rsplit(" ", 1)[0] + "…"
    return html.escape(text)


def _changelog_pages(versions):
    """(path, title, description, content) for the changelog index and each version."""
    entries = []
    for v in versions:
        entries.append(
            '<article class="release" id="%s">\n<h2><a href="/changelog/%s/">%s: %s</a></h2>\n'
            '<p class="release-meta">%s</p>\n%s\n</article>' % (v["slug"], v["slug"], v["slug"],
                                                               v["title"], v["meta"], v["html"]))
    index = ('<div class="prose">\n<h1>Changelog</h1>\n<p class="lede">What changed in each version, '
             'newest first.</p>\n%s\n</div>\n' % "\n".join(entries))
    pages = [("/changelog/", "Changelog | Teesri Nazar",
              "Every version of Teesri Nazar, the AI tester for Android apps, and what it added.", index)]
    for i, v in enumerate(versions):
        nav = []
        if i + 1 < len(versions):
            older = versions[i + 1]
            nav.append('<a rel="prev" href="/changelog/%s/">Older: %s</a>' % (older["slug"], older["title"]))
        if i > 0:
            newer = versions[i - 1]
            nav.append('<a rel="next" href="/changelog/%s/">Newer: %s</a>' % (newer["slug"], newer["title"]))
        content = ('<article class="prose release">\n<p class="release-meta"><a href="/changelog/">Changelog</a>'
                   ' / %s</p>\n<h1>%s: %s</h1>\n<p class="release-meta">%s</p>\n%s\n'
                   '<nav class="release-nav" aria-label="Other versions">%s</nav>\n</article>\n'
                   % (v["slug"], v["slug"], v["title"], v["meta"], v["html"], "\n".join(nav)))
        pages.append(("/changelog/%s/" % v["slug"], "%s: %s | Teesri Nazar changelog" % (v["slug"], v["title"]),
                      _summary(v["html"]), content))
    return pages


def build(src, changelog, out, base_url):
    """Write the site to out: src pages wrapped in src/_layout.html, assets copied as they are,
    a changelog index plus one page per version, and sitemap.xml. {{releases}} in a page
    becomes the three newest versions."""
    src, out, base_url = Path(src), Path(out), base_url.rstrip("/")
    if out.exists() and any(out.iterdir()) and not (out / "sitemap.xml").is_file():
        raise ValueError("%s is not empty and holds no previous build (no sitemap.xml); refusing to delete it" % out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # Marks out as a build at once, so a build that fails half way can still be redone.
    (out / "sitemap.xml").write_text("", encoding="utf-8")
    layout = (src / "_layout.html").read_text(encoding="utf-8")
    versions = parse_changelog(Path(changelog).read_text(encoding="utf-8"))
    releases = "".join('<li><a href="/changelog/%s/"><strong>%s</strong> %s</a></li>' % (v["slug"], v["slug"], v["title"])
                       for v in versions[:3])
    listed = []
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src).as_posix()
        if f.is_dir() or any(part.startswith("_") for part in rel.split("/")):
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix != ".html":
            shutil.copyfile(f, dest)
            continue
        text = f.read_text(encoding="utf-8")
        if text.lstrip().lower().startswith("<!doctype html>"):
            shutil.copyfile(f, dest)
            continue
        m = HEADER.match(text)
        if not m:
            raise ValueError("%s: missing the <!-- title/description --> header" % rel)
        meta = {}
        for line in m.group(1).splitlines():
            if ": " not in line:
                raise ValueError("%s: header line %r is not 'key: value'" % (rel, line))
            key, value = line.split(": ", 1)
            meta[key] = value
        missing = [k for k in ("title", "description") if k not in meta]
        if missing:
            raise ValueError("%s: header has no %s" % (rel, " or ".join(missing)))
        path = _url_path(rel)
        page = _page(layout, path, html.escape(meta["title"]), html.escape(meta["description"]),
                     _content(src, text[m.end():]).replace("{{releases}}", releases), base_url)
        if meta.get("robots"):
            tag = DESCRIPTION_META.search(page)
            if not tag:
                raise ValueError("%s: robots needs a <meta name=\"description\"> tag in the layout" % rel)
            page = '%s<meta name="robots" content="%s">%s' % (page[:tag.end()], html.escape(meta["robots"]),
                                                              page[tag.end():])
        dest.write_text(page, encoding="utf-8")
        if rel != "404.html":
            listed.append(path)
    for path, title, description, content in _changelog_pages(versions):
        dest = out / path.strip("/") / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_page(layout, path, title, description, content, base_url), encoding="utf-8")
        listed.append(path)
    urls = "".join("  <url>\n    <loc>%s%s</loc>\n  </url>\n" % (base_url, p) for p in listed)
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
                                     '"http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % urls,
                                     encoding="utf-8")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(here / "_site"))
    ap.add_argument("--base-url", default="https://teesrinazar.github.io")
    args = ap.parse_args()
    build(here / "src", here.parent / "CHANGELOG.md", args.out, args.base_url)
    print("built", args.out)
