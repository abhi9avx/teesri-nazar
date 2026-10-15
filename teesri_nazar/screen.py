"""Screen model: capture the accessibility tree, find elements by selector."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

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
    focused: bool = False
    scrollable: bool = False
    password: bool = False
    package: str = ""

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

    def describe(self) -> str:
        parts = [f"[{self.index}]", self.short_cls]
        if self.rid:
            parts.append(f"id={self.short_id}")
        if self.text:
            parts.append(f"text={_quote(self.text)}")
        if self.desc:
            parts.append(f"desc={_quote(self.desc)}")
        x, y = self.center
        parts.append(f"@{x},{y}")
        flags = [name for name, on in (
            ("tap", self.clickable), ("scroll", self.scrollable), ("checked", self.checked),
            ("focused", self.focused), ("disabled", not self.enabled), ("password", self.password),
        ) if on]
        if flags:
            parts.append(" ".join(flags))
        return " ".join(parts)


def _quote(s: str, limit: int = 60) -> str:
    s = " ".join(s.split())
    return '"' + (s if len(s) <= limit else s[: limit - 1] + "…") + '"'


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


@dataclass
class Screen:
    elements: list[Element]
    xml: str
    size: tuple[int, int] = (0, 0)
    package: str = ""

    def interesting(self) -> list[Element]:
        """Elements worth showing to a tester: anything labelled or interactive."""
        return [e for e in self.elements
                if e.text or e.desc or e.clickable or e.scrollable or e.password
                or (e.rid and "EditText" in e.cls)]

    def signature(self) -> str:
        return "|".join(f"{e.rid}/{e.text}/{e.desc}" for e in self.elements)


def parse(xml: str) -> Screen:
    root = ET.fromstring(xml)
    elements: list[Element] = []
    width = height = 0
    for node in root.iter("node"):
        m = _BOUNDS.fullmatch(node.get("bounds", ""))
        if not m:
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
            focused=a("focused") == "true",
            scrollable=a("scrollable") == "true",
            password=a("password") == "true",
            package=a("package", ""),
        ))
    package = elements[0].package if elements else ""
    return Screen(elements, xml, (width, height), package)


def capture(adb: Adb, attempts: int = 3) -> Screen:
    """Dump and read the UI tree in one adb round trip, retrying while the UI animates."""
    last_err = ""
    for attempt in range(attempts):
        raw = adb.exec_out(
            f"rm -f {DUMP_PATH}; uiautomator dump {DUMP_PATH} >/dev/null 2>&1; cat {DUMP_PATH}",
            timeout=30,
        ).decode("utf-8", errors="replace")
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
    w, a = _norm(wanted), _norm(actual)
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


def find(screen: Screen, sel: Selector) -> Element | None:
    hits = find_all(screen, sel)
    if len(hits) <= sel.nth:
        # A label someone typed loosely ("Pay now") often lives in a longer string.
        if sel.text and not sel.contains and not sel.id and not sel.desc:
            hits = find_all(screen, Selector(text=sel.text, contains=True))
        if len(hits) <= sel.nth:
            return None
    return hits[sel.nth]


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
