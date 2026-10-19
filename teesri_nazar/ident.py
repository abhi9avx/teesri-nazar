"""Screen identity: "which screen of the app is this?", for the navigation map.

`Screen.signature()` changes with every balance, row and timestamp, so it cannot tell that two
reads show the same screen. `identify()` splits a screen into its frame (the title, buttons and
labels outside scrolling lists, the selected tab, the list containers) and its content (the ids
inside the lists). Digits, typed values, list text and other apps' windows (the keyboard, a
system dialog) are left out, and every token is hashed: the map never holds screen text.

Two reads are the same screen when their ids are equal, or their titles are equal and their
frames overlap by at least `SAME` (Jaccard). Content is left out of that comparison: a list
that is still loading, or holds other rows today, is the same page (found on a real device).
Without a title nothing anchors a fuzzy match (settings pages share every row id), so an
untitled screen must match exactly. The first identity stored is canonical; a new read is
compared with stored canonicals only, so a screen never drifts into its neighbour.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass

from .screen import Element, Screen, norm

SAME = 0.6  # with equal titles: one changed button on a small screen keeps it the same screen
SMALL_FRAME = 4  # a frame this small (a page that is all list) cannot tell two same-titled pages apart
MAX_LABEL = 24
TITLE_TOP = 0.15  # a title with no toolbar or title id: the topmost label in this band of the screen
_DIGITS = re.compile(r"\d+")
_CANVAS = ("WebView", "FlutterView", "SurfaceView", "TextureView")
_BAR_IDS = ("toolbar", "action_bar", "app_bar", "appbar")


@dataclass(frozen=True)
class Ident:
    id: str                 # 8 hex characters; "" for a screen that is never stored
    kind: str               # screen | loading | opaque | foreign
    tokens: frozenset       # the frame, hashed
    title: str              # hashed title, "" when the screen has none
    secure: bool            # never route into it: a password field, or a pay/transfer/... title
    inner: frozenset = frozenset()  # the lists' content, hashed: only for the exact id
    tab: str = ""           # hashed selected tab(s): two different tabs are never one screen
    menu: frozenset = frozenset()  # hashed short labels of buttons inside lists with no digits: a page's menu

    def to_record(self) -> dict:
        """The screen as the map stores it (every value already hashed)."""
        return {"id": self.id, "tokens": sorted(self.tokens), "title": self.title, "secure": self.secure,
                "inner": sorted(self.inner), "tab": self.tab, "menu": sorted(self.menu)}

    @classmethod
    def from_record(cls, d: dict) -> "Ident":
        return cls(d["id"], "screen", frozenset(d.get("tokens") or ()), d.get("title") or "", bool(d.get("secure")),
                   frozenset(d.get("inner") or ()), d.get("tab") or "", frozenset(d.get("menu") or ()))


def identify(screen: Screen, app: str | None = None, secrets=()) -> Ident:
    els = _app_elements(screen, app)
    if not els:
        return Ident("", "foreign", frozenset(), "", False)
    if _opaque(els, screen):
        return Ident("", "opaque", frozenset(), "", False)
    clickable = [e for e in els if e.clickable]
    if not clickable and (any("ProgressBar" in e.cls for e in els) or not any(e.text or e.desc for e in els)):
        return Ident("", "loading", frozenset(), "", False)
    scrolls = [e for e in els if e.scrollable]
    secrets = [s for s in secrets if s and len(s) >= 3]
    tokens, inner, tabs, menu = set(), set(), set(), set()
    quiet = []  # labels that are not buttons: used only when a screen has nothing else
    for e in els:
        if not (e.text or e.desc or e.clickable or e.scrollable or e.password or e.checkable
                or (e.rid and "EditText" in e.cls)):
            continue
        if any(_inside(e, s) for s in scrolls if s is not e):
            if e.rid:
                inner.add(f"in:{e.short_cls}:{e.short_id}")
            label = _label(e.text or e.desc, secrets) if e.clickable else ""
            if label and "#" not in label:
                menu.add(label)  # a static menu entry; rows with amounts or dates are left out
            continue
        if e.selected and e.clickable:  # a tab; marquee text is "selected" too
            tabs.add(e.short_id or _label(e.text or e.desc, secrets))
        if e.scrollable:
            tokens.add(f"scroll:{e.short_cls}:{e.short_id}")
        elif "EditText" in e.cls or e.password:
            tokens.add(f"edit:{e.short_id or e.short_cls}")
        elif e.rid:
            tokens.add(f"{e.short_cls}:{e.short_id}")
        else:
            role = "tap" if e.clickable else "check" if e.checkable else "txt"
            label = _label(e.text or e.desc, secrets)
            token = f"{e.short_cls}:{role}" + (f":{label}" if label else "")
            if role == "txt":
                quiet.append(token)
            else:
                tokens.add(token)
    if not tokens:
        tokens.update(quiet)
    title = _title(els, scrolls, screen, secrets)
    frame = frozenset(_h(t) for t in tokens)
    content = frozenset(_h(t) for t in inner)
    t_hash = _h("title:" + title) if title else ""
    tab = _h("tab:" + "|".join(sorted(tabs))) if tabs else ""
    menu_h = frozenset(_h("menu:" + m) for m in menu)
    sid = hashlib.sha1((t_hash + "|" + tab + "|" + "|".join(sorted(frame)) + "|" + "|".join(sorted(content))
                        + "|" + "|".join(sorted(menu_h))).encode()).hexdigest()[:8]
    guarded = any(e.password for e in els) or (bool(title) and _unsafe_title(title))
    return Ident(sid, "screen", frame, t_hash, guarded, content, tab, menu_h)


def same(a: Ident, b: Ident) -> bool:
    if a.id and a.id == b.id:
        return True
    return _score(a, b) >= SAME


def _score(a: Ident, b: Ident) -> float:
    """How alike two reads are; 0 when they cannot be one screen. Two different titles or tabs
    never match. A page that is all list has a tiny frame shared by every page of the app's
    layout: then its list ids must overlap too, counted against the smaller list (one still
    loading is the same page, one with other rows is not)."""
    if (a.title and b.title and a.title != b.title) or a.tab != b.tab:
        return 0.0
    frame = len(a.tokens & b.tokens) / len(a.tokens | b.tokens) if a.tokens | b.tokens else 1.0
    if not (a.title and b.title):
        # Untitled, or a top label read on one visit only (it comes and goes on Compose screens):
        # only the menu anchors the page. The same frame and mostly the same menu, counted against
        # the shorter one (a long list shows other entries when scrolled).
        if _menu_overlap(a, b) < SAME:
            return 0.0
        return frame
    if len(a.tokens | b.tokens) < SMALL_FRAME and a.inner and b.inner:
        if len(a.inner & b.inner) / min(len(a.inner), len(b.inner)) < 0.5:
            return 0.0
    return frame


def _menu_overlap(a: Ident, b: Ident) -> float:
    """Jaccard; for two menus of at least 3 entries and similar length, against the shorter one
    (a long list shows other entries when scrolled). A small menu inside a bigger one (an article's
    one button that the profile page also has) is another page."""
    small, big = sorted((len(a.menu), len(b.menu)))
    if not small:
        return 0.0
    shared = len(a.menu & b.menu)
    if small >= 3 and big <= 2 * small:
        return shared / small
    return shared / len(a.menu | b.menu)


def compatible(expected: Ident, read: Ident) -> bool:
    """Could `read` be a visit to `expected`? Looser than `same`, for checking a goto step's
    arrival when the map holds the page twice (once with a title, once without): no conflicting
    title or tab, the same frame, and most of the shorter menu."""
    if read.id == expected.id:
        return True
    if (expected.title and read.title and expected.title != read.title) or expected.tab != read.tab:
        return False
    union = expected.tokens | read.tokens
    if union and len(expected.tokens & read.tokens) / len(union) < SAME:
        return False
    if expected.title and expected.title == read.title:
        return True
    return _menu_overlap(expected, read) >= SAME


def match(ident: Ident, stored: dict, one_sided: bool = True) -> str | None:
    """The stored screen this read shows: its id, or None for a screen not seen before.

    A read and a screen of which only one has a title match on their menu alone; that is
    refused when more than one stored screen fits (a credit card and a debit card page share a
    menu, and only the missing title told them apart), and with `one_sided=False`."""
    if not ident.id:
        return None
    if ident.id in stored:
        return ident.id
    fits = []
    for sid, other in stored.items():
        s = _score(other, ident)
        if s >= SAME:
            fits.append((s, sid, bool(other.title) != bool(ident.title)))
    if not fits:
        return None
    fits.sort(key=lambda f: -f[0])
    _, best, lopsided = fits[0]
    if lopsided and (not one_sided or len(fits) > 1):
        return None
    return best


def _h(token: str) -> str:
    return hashlib.sha1(token.encode()).hexdigest()[:10]


def _label(raw: str, secrets, limit: int = MAX_LABEL) -> str:
    if any(s in raw for s in secrets):
        return ""
    label = _DIGITS.sub("#", norm(raw))
    return label if len(label) <= limit else ""


# The screen where money moves or a PIN is asked; "Payment history" or "Pay later" are places to go.
# A bare "Pay" or "Transfer" title is usually a hub menu, not where money moves: the step that
# moves money is caught by its own label ("Pay ₹500", "Confirm"); more words: TNAZAR_UNSAFE_WORDS.
_UNSAFE_TITLE = re.compile(r"\b(confirm|review and pay|checkout|enter (m|upi )?pin|authori[sz]e|verify payment)\b")


def _unsafe_title(title: str) -> bool:
    extra = [w.strip().casefold() for w in os.environ.get("TNAZAR_UNSAFE_WORDS", "").split(",") if w.strip()]
    return bool(_UNSAFE_TITLE.search(title)) or any(re.search(rf"\b{re.escape(w)}\b", title) for w in extra)


def _app_elements(screen: Screen, app: str | None) -> list[Element]:
    els = screen.elements
    if not app:
        counts = Counter(e.package for e in els if e.package and (e.clickable or e.text or e.desc))
        if not counts:
            return els
        app = counts.most_common(1)[0][0]
    return [e for e in els if e.package == app]


def _inside(e: Element, box: Element) -> bool:
    l, t, r, b = box.bounds
    el, et, er, eb = e.bounds
    return l <= el and t <= et and er <= r and eb <= b and e.bounds != box.bounds


def _area(e: Element) -> int:
    l, t, r, b = e.bounds
    return (r - l) * (b - t)


def _opaque(els: list[Element], screen: Screen) -> bool:
    w, h = screen.size
    full = max(w * h, 1)
    for v in els:
        if any(c in v.cls for c in _CANVAS) and _area(v) >= full / 2:
            inner = [e for e in els if e is not v and _inside(e, v) and (e.clickable or e.text or e.desc)]
            outer = [e for e in els if not _inside(e, v) and e is not v and (e.clickable or e.text or e.desc)]
            if len(inner) < 3 and len(outer) < 3:
                return True
    return False


def _title(els, scrolls, screen, secrets) -> str:
    def plain(e: Element) -> bool:
        return bool(e.text) and not e.clickable and "EditText" not in e.cls and not e.password \
            and not any(_inside(e, s) for s in scrolls)

    def norm_raw(raw: str) -> str:
        return _label(raw, secrets, limit=10_000)[:40]

    def norm(e: Element) -> str:
        return norm_raw(e.text)

    bars = [e for e in els if e.cls.endswith(("Toolbar", "ActionBar"))
            or any(b in e.short_id.lower() for b in _BAR_IDS)]
    for bar in bars:
        # Toolbars are page chrome even when a collapsing layout puts them inside the scroll view.
        if bar.desc or bar.text:
            return norm_raw(bar.text or bar.desc)
        inside = sorted((e for e in els if e.text and not e.clickable and _inside(e, bar)),
                        key=lambda e: (e.bounds[1], e.bounds[0]))
        if inside:
            return norm(inside[0])
    named = sorted((e for e in els if plain(e) and e.short_id.lower().endswith("title")),
                   key=lambda e: (e.bounds[1], e.bounds[0]))
    if named:
        return norm(named[0])
    top = screen.size[1] * TITLE_TOP
    high = sorted((e for e in els if plain(e) and e.bounds[1] < top), key=lambda e: (e.bounds[1], e.bounds[0]))
    return norm(high[0]) if high else ""
