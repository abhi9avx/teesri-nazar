"""Screen identity: "which screen of the app is this?", for the navigation map.

`Screen.signature()` changes with every balance, row and timestamp, so it cannot tell that two
reads show the same screen. `identify()` keeps only what stays put on a screen: element ids,
the labels of buttons outside scrolling lists, and the title. Digits, typed values, list rows
and other apps' windows (the keyboard, a system dialog) are left out, and every token is
hashed: the map never holds screen text.

Two reads are the same screen when their titles are equal and their token sets overlap by at
least `SAME` (Jaccard; untitled screens must match exactly). The first identity stored for a screen is its canonical one; a new read
is compared with stored canonicals only, so a screen never drifts into its neighbour.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from .screen import Element, Screen, _norm

SAME = 0.6  # with equal titles: one changed button on a small screen keeps it the same screen
MAX_LABEL = 24
TITLE_TOP = 0.15  # a title with no toolbar or title id: the topmost label in this band of the screen
_DIGITS = re.compile(r"\d+")
_CANVAS = ("WebView", "FlutterView", "SurfaceView", "TextureView")
_BAR_IDS = ("toolbar", "action_bar", "app_bar", "appbar")


@dataclass(frozen=True)
class Ident:
    id: str                 # 8 hex characters; "" for a screen that is never stored
    kind: str               # screen | loading | opaque | foreign
    tokens: frozenset       # hashed
    title: str              # hashed title, "" when the screen has none
    secure: bool            # has a password field


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
    tokens = set()
    quiet = []  # labels that are not buttons: used only when a screen has nothing else
    for e in els:
        if not (e.text or e.desc or e.clickable or e.scrollable or e.password or e.checkable
                or (e.rid and "EditText" in e.cls)):
            continue
        if any(_inside(e, s) for s in scrolls if s is not e):
            if e.rid:
                tokens.add(f"in:{e.short_cls}:{e.short_id}")
            continue
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
    hashed = frozenset(_h(t) for t in tokens)
    t_hash = _h("title:" + title) if title else ""
    sid = hashlib.sha1((t_hash + "|" + "|".join(sorted(hashed))).encode()).hexdigest()[:8]
    return Ident(sid, "screen", hashed, t_hash, any(e.password for e in els))


def same(a: Ident, b: Ident) -> bool:
    """Equal ids, or the same title and overlapping tokens. Without a title nothing anchors a
    fuzzy match (settings pages share every row id), so untitled screens must match exactly."""
    if a.id and a.id == b.id:
        return True
    if not a.title or a.title != b.title or not a.tokens or not b.tokens:
        return False
    return len(a.tokens & b.tokens) / len(a.tokens | b.tokens) >= SAME


def match(ident: Ident, stored: dict) -> str | None:
    """The stored screen this read shows: its id, or None for a screen not seen before."""
    if not ident.id:
        return None
    if ident.id in stored:
        return ident.id
    if not ident.title:
        return None
    best, score = None, SAME
    for sid, other in stored.items():
        if other.title != ident.title or not other.tokens or not ident.tokens:
            continue
        s = len(other.tokens & ident.tokens) / len(other.tokens | ident.tokens)
        if s >= score:
            best, score = sid, s
    return best


def _h(token: str) -> str:
    return hashlib.sha1(token.encode()).hexdigest()[:10]


def _label(raw: str, secrets) -> str:
    if any(s in raw for s in secrets):
        return ""
    label = _DIGITS.sub("#", _norm(raw))
    return label if len(label) <= MAX_LABEL else ""


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
        if any(s in raw for s in secrets):
            return ""
        return _DIGITS.sub("#", _norm(raw))[:40]

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
