"""Targets as typed on the command line, and finding them on screen."""

from __future__ import annotations

import argparse
import re

from . import device as dev
from . import env
from . import screen as scr
from .errors import NotFound, UserError

_XY = re.compile(r"^(\d+)\s*,\s*(\d+)$")


def parse_target(raw: str | None, args: argparse.Namespace) -> tuple[str, object]:
    """Return ("xy", (x,y)) | ("index", n) | ("selector", Selector)."""
    nth = getattr(args, "nth", 0) or 0
    sel = scr.Selector(id=getattr(args, "id", None), desc=getattr(args, "desc", None),
                       text=getattr(args, "text", None), nth=nth)
    if raw:
        if m := _XY.match(raw):
            return "xy", (int(m.group(1)), int(m.group(2)))
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
    """Find where to act. Returns {xy, selector, label, element, screen}."""
    kind, value = parse_target(raw, args)
    if kind == "xy":
        return {"xy": value, "selector": None, "label": raw, "element": None, "screen": None}
    if kind == "index":
        screen = ctx.last_screen()
        if value >= len(screen.elements):
            raise NotFound(f"#{value} is not on the last observed screen")
        el = screen.elements[value]
        sel = scr.stable_selector(screen, el)
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
            "element": el, "screen": screen}


def _not_found_msg(sel: scr.Selector, screen: scr.Screen) -> str:
    values = env.secret_values()
    labels = [env.mask(e.text or e.desc, values) for e in screen.interesting() if e.text or e.desc]
    seen = ", ".join(f'"{x}"' for x in dict.fromkeys(labels[:20]))
    return f"not found: {sel}\nvisible: {seen or '(no labelled elements)'}"
