"""Screen model: capture the accessibility tree, find elements by selector."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import uia2
from .adb import Adb, AdbError

DUMP_PATH = "/sdcard/tnazar-ui.xml"
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Element:
    index: int
    cls: str
    text: str
    rid: str
    desc: str
    bounds: tuple[int, int, int, int]
    clickable: bool = False
    enabled: bool = True
    checked: bool = False
    checkable: bool = False
    focused: bool = False
    scrollable: bool = False
    password: bool = False
    package: str = ""
    selected: bool = False  # the current tab; not shown by `observe` (its output is pinned)

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return (l + r) // 2, (t + b) // 2

    @property
    def short_id(self) -> str:
        return self.rid.split(":id/", 1)[-1]

    @property
    def short_cls(self) -> str:
        return self.cls.rsplit(".", 1)[-1]

    def describe(self, full: bool = False) -> str:
        quote = _whole if full else _quote
        parts = [f"[{self.index}]", self.short_cls]
        if self.rid:
            parts.append(f"id={self.short_id}")
        if self.text:
            parts.append(f"text={quote(self.text)}")
        if self.desc:
            parts.append(f"desc={quote(self.desc)}")
        x, y = self.center
        parts.append(f"@{x},{y}")
        flags = [name for name, on in (
            ("tap", self.clickable), ("scroll", self.scrollable), ("checked", self.checked),
            ("unchecked", self.checkable and not self.checked),
            ("focused", self.focused), ("disabled", not self.enabled), ("password", self.password),
        ) if on]
        if flags:
            parts.append(" ".join(flags))
        return " ".join(parts)


def _quote(s: str, limit: int = 60) -> str:
    s = " ".join(s.split())
    return '"' + (s if len(s) <= limit else s[: limit - 1] + "…") + '"'


def _whole(s: str) -> str:
    """All of it, line breaks kept (indented under the element), for reading long messages."""
    lines = [" ".join(ln.split()) for ln in s.strip().splitlines() if ln.strip()]
    return '"' + "\n    ".join(lines) + '"'


_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def norm(s: str) -> str:
    return " ".join(s.translate(_QUOTES).split()).casefold()


@dataclass
class Screen:
    elements: list[Element]
    xml: str
    size: tuple[int, int] = (0, 0)
    package: str = ""

    def interesting(self) -> list[Element]:
        """Elements worth showing to a tester: anything labelled or interactive."""
        return [e for e in self.elements
                if e.text or e.desc or e.clickable or e.scrollable or e.password or e.checkable
                or (e.rid and "EditText" in e.cls)]

    def signature(self) -> str:
        return "|".join(f"{e.rid}/{e.text}/{e.desc}" for e in self.elements)


def parse(xml: str) -> Screen:
    root = ET.fromstring(xml)
    elements: list[Element] = []
    width = height = 0
    # `uiautomator dump` tags every element <node>; the UiAutomator2 server uses the class name.
    for node in root.iter():
        m = _BOUNDS.fullmatch(node.get("bounds", ""))
        if not m or node.get("displayed") == "false":
            continue
        l, t, r, b = map(int, m.groups())
        width, height = max(width, r), max(height, b)
        if r <= l or b <= t:
            continue
        a = node.get
        elements.append(Element(
            index=len(elements),
            cls=a("class", ""),
            text=a("text", ""),
            rid=a("resource-id", ""),
            desc=a("content-desc", ""),
            bounds=(l, t, r, b),
            clickable=a("clickable") == "true" or a("long-clickable") == "true",
            enabled=a("enabled", "true") == "true",
            checked=a("checked") == "true",
            checkable=a("checkable") == "true",
            focused=a("focused") == "true",
            scrollable=a("scrollable") == "true",
            password=a("password") == "true",
            package=a("package", ""),
            selected=a("selected") == "true",
        ))
    package = elements[0].package if elements else ""
    return Screen(elements, xml, (width, height), package)


DUMP_TIMEOUT = 10


def capture(adb: Adb, attempts: int = 3) -> Screen:
    """Read the UI tree: from the UiAutomator2 server when the run started one, else by dump."""
    xml = uia2.source(adb)
    if xml is not None:
        return parse(xml)
    last_err = ""
    for attempt in range(attempts):
        try:
            raw = adb.exec_out(
                f"rm -f {DUMP_PATH}; uiautomator dump {DUMP_PATH} >/dev/null 2>&1; cat {DUMP_PATH}",
                timeout=DUMP_TIMEOUT,
            ).decode("utf-8", errors="replace")
        except AdbError as exc:
            # uiautomator waits for the app to go idle; a busy app can hold it for 30 s or more.
            # Kill the stuck dump on the device (exact name: leaves other servers alone) and retry.
            adb.shell("pkill", "-x", "uiautomator", check=False)
            last_err = str(exc)
            continue
        start = raw.find("<?xml")
        if start >= 0 and "</hierarchy>" in raw:
            return parse(raw[start: raw.rindex("</hierarchy>") + len("</hierarchy>")])
        last_err = raw.strip()[:200] or "empty dump"
        time.sleep(0.7 * (attempt + 1))
    raise AdbError(f"uiautomator dump failed after {attempts} attempts: {last_err}")


@dataclass
class Selector:
    """How to find an element. Priority when recording: id > desc > text."""
    id: str | None = None
    desc: str | None = None
    text: str | None = None
    contains: bool = False
    nth: int = 0

    def is_empty(self) -> bool:
        return not (self.id or self.desc or self.text)

    def to_dict(self) -> dict:
        d = {k: v for k, v in (("id", self.id), ("desc", self.desc), ("text", self.text)) if v}
        if self.contains:
            d["contains"] = True
        if self.nth:
            d["nth"] = self.nth
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Selector":
        return cls(id=d.get("id"), desc=d.get("desc"), text=d.get("text"),
                   contains=bool(d.get("contains")), nth=int(d.get("nth", 0)))

    def __str__(self) -> str:
        parts = []
        if self.id:
            parts.append(f"id={self.id}")
        if self.desc:
            parts.append(f'desc="{self.desc}"')
        if self.text:
            parts.append(("~" if self.contains else "") + f'"{self.text}"')
        if self.nth:
            parts.append(f"(match #{self.nth})")
        return " ".join(parts) or "(empty selector)"


def _text_match(wanted: str, actual: str, contains: bool) -> bool:
    w, a = norm(wanted), norm(actual)
    return bool(a) and (w in a if contains else w == a)


def _id_match(wanted: str, rid: str) -> bool:
    if not rid:
        return False
    return rid == wanted if ":id/" in wanted else rid.split(":id/", 1)[-1] == wanted


def find_all(screen: Screen, sel: Selector) -> list[Element]:
    hits = []
    for e in screen.elements:
        if sel.id and not _id_match(sel.id, e.rid):
            continue
        if sel.desc and not _text_match(sel.desc, e.desc, sel.contains):
            continue
        if sel.text and not _text_match(sel.text, e.text, sel.contains):
            continue
        hits.append(e)
    return hits


def find(screen: Screen, sel: Selector, strict: bool = False) -> Element | None:
    """`strict`: no loose fallbacks; used for "is it gone?", where a longer label must not count."""
    for attempt in _fallbacks(sel, strict):
        hits = find_all(screen, attempt)
        if len(hits) > sel.nth:
            return hits[sel.nth]
    return None


def _fallbacks(sel: Selector, strict: bool = False) -> list[Selector]:
    """A bare text selector also tries content-desc, where Compose apps put labels.

    Order: as given (visible text), the same match on content-desc, then for an exact selector
    the partial forms: a label typed loosely ("Pay now") often lives in a longer string.
    """
    out = [sel]
    if not sel.text or sel.id or sel.desc:
        return out
    out.append(Selector(desc=sel.text, contains=sel.contains))
    if not sel.contains and not strict:
        out += [Selector(text=sel.text, contains=True), Selector(desc=sel.text, contains=True)]
    return out


def stable_selector(screen: Screen, el: Element) -> Selector:
    """Pick the most durable selector that identifies `el` on this screen."""
    candidates = []
    if el.rid:
        candidates.append(Selector(id=el.short_id))
    if el.desc:
        candidates.append(Selector(desc=el.desc))
    if el.text and not el.password:
        candidates.append(Selector(text=el.text))
    if el.rid and el.text and not el.password:
        candidates.append(Selector(id=el.short_id, text=el.text))
    for sel in candidates:
        hits = find_all(screen, sel)
        if len(hits) == 1:
            return sel
    for sel in candidates:
        hits = find_all(screen, sel)
        if el in hits:
            sel.nth = hits.index(el)
            return sel
    return Selector()
