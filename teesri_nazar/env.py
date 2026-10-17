"""Secrets: .env loading, and hiding known secret values in recorded commands, output and UI trees."""

from __future__ import annotations

import dataclasses
import html
import os
import re
from xml.sax.saxutils import escape

from . import live
from . import screen as scr
from .session import home

HIDDEN = "••••"
_DOTENV_KEYS: frozenset[str] = frozenset()
_ATTR = re.compile(r'="([^"]*)"')


def load_dotenv() -> None:
    """KEY=VALUE lines from ./.env, without overriding the real environment."""
    global _DOTENV_KEYS
    keys = set()
    f = home() / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                keys.add(key.strip())
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    _DOTENV_KEYS = frozenset(keys)


def dotenv_keys() -> frozenset[str]:
    return _DOTENV_KEYS


def secret_values() -> set[str]:
    """Every .env value and dashboard secret."""
    return {v for v in [os.environ[k] for k in _DOTENV_KEYS if k in os.environ] + live.secret_values() if v}


def mask(s: str, values: set[str] | None = None) -> str:
    values = secret_values() if values is None else values
    if not s or not values:
        return s
    # A value of 1-2 characters would match inside unrelated words ("42" in "tcp:8420"),
    # so it is hidden only when it is the whole string.
    if s in values:
        return HIDDEN
    for v in sorted((v for v in values if len(v) >= 3), key=len, reverse=True):
        s = s.replace(v, HIDDEN)
    return s


def redact(argv: list[str]) -> list[str]:
    """Hide any .env value or dashboard secret that appears in a recorded command line."""
    values = secret_values()
    return [mask(a, values) for a in argv]


def mask_xml(xml: str) -> str:
    """A UI tree with secrets hidden in its attribute values: a typed secret is plain text in it."""
    values = secret_values()
    if not values:
        return xml

    def one(m: re.Match) -> str:
        raw = html.unescape(m.group(1))
        hidden = mask(raw, values)
        return m.group(0) if hidden == raw else '="' + escape(hidden, {'"': "&quot;"}) + '"'
    return _ATTR.sub(one, xml)


def describe(el: scr.Element, values: set[str] | None = None) -> str:
    # Asking the dashboard for its secrets is an HTTP call: list callers pass `values` once.
    values = secret_values() if values is None else values
    return dataclasses.replace(el, text=mask(el.text, values), desc=mask(el.desc, values)).describe()
