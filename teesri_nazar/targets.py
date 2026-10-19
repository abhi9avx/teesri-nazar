"""Targets as typed on the command line, and finding them on screen."""

from __future__ import annotations

import argparse
import re

from . import device as dev
from . import env
from . import screen as scr
from .errors import NotFound, UserError

_XY = re.compile(r"^(\d+)\s*,\s*(\d+)$")
_PCT = re.compile(r"^(\d+(?:\.\d+)?)%\s*,\s*(\d+(?:\.\d+)?)%$")


def parse_target(raw: str | None, args: argparse.Namespace) -> tuple[str, object]:
    """Return ("xy", (x,y)) | ("pct", (x%,y%)) | ("index", n) | ("selector", Selector)."""
    nth = getattr(args, "nth", 0) or 0
    sel = scr.Selector(id=getattr(args, "id", None), desc=getattr(args, "desc", None),
                       text=getattr(args, "text", None), nth=nth)
    if raw:
        if m := _XY.match(raw):
            return "xy", (int(m.group(1)), int(m.group(2)))
        if m := _PCT.match(raw):
            pct = (float(m.group(1)), float(m.group(2)))
            if max(pct) > 100:
                raise UserError(f"{raw}: a screen position is 0%-100% across, 0%-100% down")
            return "pct", pct
        if raw.startswith("#") and raw[1:].isdigit():
            return "index", int(raw[1:])
        if raw.startswith("~"):
            sel.text, sel.contains = raw[1:], True
        elif "=" in raw and raw.split("=", 1)[0] in ("id", "desc", "text"):
            k, v = raw.split("=", 1)
            setattr(sel, k, v)
        else:
            sel.text = raw
    if getattr(args, "contains", False):
        sel.contains = True
    if sel.is_empty():
        raise UserError("give a target: \"Text\", ~partial, id=..., desc=..., #index or x,y")
    return "selector", sel


def selector_of(raw: str | None, contains: bool = False) -> scr.Selector:
    """A target that must name an element: coordinates and #index are refused."""
    kind, value = parse_target(raw, argparse.Namespace(contains=contains))
    if kind != "selector":
        raise UserError(f"{raw!r} is not an element: use \"Text\", ~partial, id=... or desc=...")
    return value


def resolve(ctx, raw: str | None, args, timeout: float = 0) -> dict:
    """Find where to act. Returns {xy, selector, label, element, screen}; `screen` is None unless
    it was read just now (an `#index` resolves against the last `observe`)."""
    kind, value = parse_target(raw, args)
    if kind == "xy":
        return {"xy": value, "selector": None, "label": raw, "element": None, "screen": None}
    if kind == "pct":
        # For an element with no label: the same spot on any screen size.
        width, height = ctx.adb.screen_size()
        xy = (round(width * value[0] / 100), round(height * value[1] / 100))
        return {"xy": xy, "selector": None, "label": raw, "element": None, "screen": None}
    fresh = kind != "index"  # an index points into the last `observe`, which may be old
    if kind == "index":
        screen = ctx.last_screen()
        if value >= len(screen.elements):
            raise NotFound(f"#{value} is not on the last observed screen")
        el = screen.elements[value]
        sel = scr.stable_selector(screen, el)
        if sel.is_empty() and (inner := _label_inside(screen, el)) is not None:
            # A list row is often an unlabelled container with its label on a text inside it:
            # tapping that text is the same tap, and it can be found again (replay, the map).
            el, sel = inner, scr.stable_selector(screen, inner)
    else:
        sel = value
        if timeout:
            ok, screen, el = dev.wait_for(ctx.adb, sel, timeout)
        else:
            screen = ctx.capture()
            el = scr.find(screen, sel)
        if el is None:
            raise NotFound(_not_found_msg(sel, screen))
    label = env.mask(el.text or el.desc) or el.short_id or el.short_cls
    return {"xy": el.center, "selector": sel.to_dict() or None, "label": label,
            "element": el, "screen": screen if fresh else None}


def _label_inside(screen: scr.Screen, box: scr.Element) -> scr.Element | None:
    l, t, r, b = box.bounds
    for e in screen.elements:
        el_, et, er, eb = e.bounds
        if e is not box and (e.text or e.desc) and not e.password and l <= el_ and t <= et and er <= r and eb <= b:
            if not scr.stable_selector(screen, e).is_empty():
                return e
    return None


def _not_found_msg(sel: scr.Selector, screen: scr.Screen) -> str:
    values = env.secret_values()
    labels = [env.mask(e.text or e.desc, values) for e in screen.interesting() if e.text or e.desc]
    seen = ", ".join(f'"{x}"' for x in dict.fromkeys(labels[:20]))
    return f"not found: {sel}\nvisible: {seen or '(no labelled elements)'}"
