"""The navigation map: routes learned from runs, so a screen found once is reached in one command.

Learning reads a run's own event log. Commands stamp the screen they read on their event
(`sid`, from ident.py); `learn()` turns "read screen X, one tap, next read is screen Y" into an
edge X -> Y, and a `goto` that missed a phrase plus the last passing check after it into a
name. Nothing is learned from a doubt: two actions before the next read, typing, a jump of
more than a minute, a plain relaunch or a deep link all break the chain.

The store is append-only JSON Lines per app package, outside any repository:
`${TNAZAR_MAP_DIR:-~/.tnazar/maps}/<package>/map.jsonl`. Loading folds it: screens seen again
under a new id merge into the first one stored (ident.match), repeats only add evidence, a
`forget` line drops what came before it. Selectors and labels are stored only when they hold
no digits or secrets; screens only as hashed tokens.

A learned edge runs only when it has a selector, is not money-moving (recomputed on every
load, so editing the file cannot clear it), did not fail in this run, and is not retired
(its last two results on this app version both failed).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import ident
from .errors import UserError

SCHEMA = 2  # 2: screens carry their menu; a map of an older schema is moved aside and relearned
MAX_GAP = 60.0          # seconds between an action and the read that shows where it went
EPISODE_ACTIONS = 25    # a phrase search ends after this many actions without a new goto
MAX_SELECTOR = 24
LOCK_WAIT = 10.0  # a run stopping while `map compact` rewrites waits rather than loses its learning
UNSAFE_WORDS = ("pay", "send", "transfer", "confirm", "buy", "purchase", "withdraw", "invest", "delete",
                "remove", "submit", "checkout", "place order", "add money", "log out", "logout", "sign out",
                "uninstall", "reset", "erase")
_FILLER = ("open", "go to", "goto", "show me", "show", "check", "view", "navigate to", "take me to", "my", "the", "me")
_PII = re.compile(r"\d|@|₹|\$|€|£|\brs\.?\b", re.IGNORECASE)
_BACK_KEYS = ("back", "4", "KEYCODE_BACK")
_MONEY = re.compile(r"@|₹|\$|€|£|\brs\.?\s?\d", re.IGNORECASE)
# Taps that go back to wherever the user came from: where they lead depends on history.
_BACKWARD = {"navigate up", "back", "go back", "close", "dismiss", "cancel", "done"}
_WORD = re.compile(r"[a-z]+")


def enabled() -> bool:
    return os.environ.get("TNAZAR_MAP", "on").lower() not in ("off", "0", "false", "no")


def map_dir() -> Path:
    return Path(os.environ.get("TNAZAR_MAP_DIR") or Path.home() / ".tnazar" / "maps")


# ---------------------------------------------------------------- phrases

def normalise(raw: str) -> str:
    """"Open my Passbook" -> "passbook"; a flow name `open_settings` -> "settings"."""
    s = " ".join(raw.replace("_", " ").replace("-", " ").casefold().split())
    changed = True
    while changed and s:
        changed = False
        for f in _FILLER:
            if s == f:
                return ""
            if s.startswith(f + " "):
                s, changed = s[len(f) + 1:], True
    return s


# Account, phone, card, PIN and OTP digits (4+ in a row) are refused; a year (19xx, 20xx) is fine.
_PII_PHRASE = re.compile(r"(?<!\d)(?!(?:19|20)\d\d(?!\d))\d{4,}|@|₹|\$|€|£|\brs\.?\s?\d", re.IGNORECASE)


def check_phrase(raw: str) -> str:
    """The phrase as the map stores it; refused when it names no screen or holds what looks like an
    account number, an email or an amount (the map is plain text for phrases)."""
    phrase = normalise(raw)
    if not phrase:
        raise UserError(f"say which screen: {raw!r} names none")
    if _PII_PHRASE.search(phrase) or any(v and len(v) >= 3 and v.casefold() in phrase for v in secret_values()):
        raise UserError(f"{raw!r} looks like it holds an account number, email or amount: name the screen "
                        f"without it")
    return phrase


def suggest(phrase: str, known) -> list[str]:
    return difflib.get_close_matches(phrase, list(known), n=3, cutoff=0.75)


# ---------------------------------------------------------------- learning

def _ts(t: str) -> float:
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def secret_values() -> set:
    """Values never stored in or used for the map: only .env values. The dashboard's secrets cost
    an HTTP call per step, and typed values (where they end up) are never part of the map."""
    from .env import dotenv_keys
    return {os.environ[k] for k in dotenv_keys() if os.environ.get(k)}


def _backward(sel: dict | None, label: str) -> bool:
    sel = sel or {}
    words = {normalise(str(v)) for v in (label, sel.get("text"), sel.get("desc")) if v}
    parts = set((sel.get("id") or "").casefold().replace("-", "_").split("_"))
    return bool(words & _BACKWARD) or bool(parts & {"back", "close", "up", "dismiss", "cancel"})


def _clean(value: str | None, secrets=()) -> bool:
    return bool(value) and len(value) <= MAX_SELECTOR and not _PII.search(value) \
        and not any(s and s in value for s in secrets)


def scrub_selector(sel: dict | None, secrets=()) -> dict | None:
    if not sel:
        return None
    for k in ("text", "desc"):
        if sel.get(k) is not None and not _clean(sel[k], secrets):
            return None
    return {k: v for k, v in sel.items() if k in ("id", "desc", "text", "contains", "nth")}


def sel_key(sel: dict | None) -> str:
    return json.dumps(sel, sort_keys=True) if sel else ""


VOID = "void"


def settled_sid(e: dict) -> str | None:
    """The screen an event's own read showed, when that read counts: what a tap or typed field
    acted on, what a passing wait or check found, where a scroll ended, an observe (`seen`)."""
    sid, typ = e.get("sid"), e.get("type")
    if not sid:
        return None
    if typ in ("tap", "type"):
        return sid if e.get("ok") is not False else None
    if typ in ("scroll", "seen"):
        return sid
    if typ in ("wait", "check"):
        return sid if e.get("ok", True) and not e.get("found_instead") else None
    return None


def edge_line(frm: str, to: str, kind: str, sel: dict | None, label: str, scroll: dict | None, run, ver: str,
              ok: bool, t) -> dict:
    return {"k": "edge", "from": frm, "to": to, "kind": kind, "sel": sel, "label": label, "scroll": scroll,
            "run": run, "ver": ver, "ok": ok, "t": t}


def _run_canon(screens: dict):
    """Within one run, a screen read again after its feed changed is the same screen: map each
    raw id to the first one read like it (stored records, in the order they were first seen).
    Returns the mapping and the screens that must never be a target: one read of them showed a
    PIN field, so all of them carry it."""
    canon, kept, guarded = {}, {}, set()
    for sid, rec in screens.items():
        try:
            same = ident.match(ident.Ident.from_record(dict(rec, id=sid)), kept, one_sided=False)
        except (KeyError, TypeError):
            same = None
        canon[sid] = same or sid
        if not same:
            kept[sid] = ident.Ident.from_record(dict(rec, id=sid))
        if rec.get("secure"):
            guarded.add(canon[sid])
    return (lambda sid: canon.get(sid, sid) if sid else sid), guarded


def learn(events: list[dict], run: str, version: str, screens: dict, secrets=()) -> list[dict]:
    """Map observations from one run's events (pure). `screens`: sid -> screen record."""
    c, guarded = _run_canon(screens)
    events = [dict(e, sid=c(e["sid"])) if e.get("sid") else e for e in events]
    out: list[dict] = []
    used: set[str] = set()
    last = None          # screen of the last settled read ("@cold" after a fresh launch)
    pending = None       # the one action since that read, VOID after two or after input
    hint = None          # a `scroll --to` that found the next tap's target

    def emit_edge(frm, to, act, t, ok=True):
        raw = act.get("sel")
        label = act.get("label") or ""
        kind = act["kind"]
        texts = [label] + [raw.get(k) for k in ("text", "desc") if raw and raw.get(k)]
        if kind == "tap" and _backward(raw, label):
            kind = "back"
        # The label says whether a step moves money ("Place order ₹1,299"): judged on the raw text,
        # before it is dropped. Amounts, emails and secrets make it knowledge only even by its id.
        if kind == "tap" and (unsafe_label(*texts) or any(_MONEY.search(x) or any(v and v in x for v in secrets)
                                                          for x in texts)):
            kind = "xy"
        sel = scrub_selector(raw, secrets) if kind == "tap" else None
        if kind == "tap" and not sel:
            kind = "xy"
        scroll_hint = act.get("scroll")
        if scroll_hint:
            to_sel = scrub_selector(scroll_hint.get("to"), secrets)
            scroll_hint = {"direction": scroll_hint.get("direction"), "to": to_sel} if to_sel else None
        out.append(edge_line(frm, to, kind, sel, label if _clean(label, secrets) else "", scroll_hint,
                             run, version, ok, t))
        used.update((frm, to))

    def settle(sid, t):
        nonlocal last, pending, hint
        if pending not in (None, VOID) and last and sid != last and _ts(t) - pending["at"] <= MAX_GAP:
            emit_edge(last, sid, pending, t)
        if sid != last:
            hint = None
        last, pending = sid, None

    def act(a):
        nonlocal pending
        pending = VOID if pending is not None else a

    for e in events:
        typ, t = e.get("type"), e.get("t", "")
        if seen := settled_sid(e):
            settle(seen, t)  # a tap's or a typed field's read comes before its action
        if typ == "tap":
            if e.get("ok") is False:
                continue
            sel = e.get("stable") or e.get("selector")
            act({"kind": "tap" if sel else "xy", "sel": sel, "label": e.get("label"), "at": _ts(t),
                 "scroll": hint})
            hint = None
        elif typ == "type":
            pending = VOID
        elif typ == "key":
            if e.get("key") in _BACK_KEYS and not e.get("keyboard"):
                act({"kind": "back", "sel": None, "label": "back", "at": _ts(t)})
            else:
                pending = VOID
        elif typ == "launch":
            if e.get("fresh") or e.get("restart"):
                last, pending = "@cold", {"kind": "cold", "sel": None, "label": "", "at": _ts(t)}
            else:
                last, pending = None, None
        elif typ == "open_url":
            last, pending = None, None
        elif typ in ("swipe", "adb"):
            if not (typ == "adb" and (e.get("args") or [""])[0] == "logcat"):
                pending = VOID
        elif typ == "scroll":
            if e.get("to") and e.get("found"):
                hint = {"direction": e.get("direction"), "to": e.get("to")}
        elif typ == "nav" and e.get("outcome") == "failed" and e.get("edge"):
            ed = e["edge"]
            out.append(edge_line(ed["from"], ed["to"], ed.get("kind", "tap"), ed.get("sel"), "", None, run,
                                 version, False, t))
    out = _once_per_run(out)
    named = set()
    for o in _episodes(events, run, secrets):
        if (o["phrase"], o["screen"]) not in named:
            named.add((o["phrase"], o["screen"]))
            out.append(o)
    for o in out:
        if o["k"] == "name":
            used.add(o["screen"])
    shots = [dict(screens[s], k="screen", secure=bool(screens[s].get("secure")) or s in guarded)
             for s in sorted(used) if s in screens]
    return shots + out


def _once_per_run(obs: list[dict]) -> list[dict]:
    """Ten trips Home -> Savings in one run are one piece of evidence, not ten rows."""
    seen, out = set(), []
    for o in obs:
        key = (o["from"], o["to"], o["kind"], sel_key(o["sel"]), o["ok"])
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def _words(text: str) -> set:
    return set(_WORD.findall(normalise(text.replace("_", " "))))


def _check_label(e: dict) -> str:
    sel = e.get("selector") or {}
    return " ".join(str(sel.get(k) or "") for k in ("text", "desc", "id"))


def _episodes(events: list[dict], run: str, secrets=()) -> list[dict]:
    """After `goto <phrase>` missed, which screen did the agent go looking for? A passing check
    that says every word of the phrase ends the search there. Otherwise the check saying most of
    it, the latest of equals; a check that says none of it counts only while the agent stays (a
    Back after it means that was not the place)."""
    names, ep = [], None

    def close():
        nonlocal ep
        if ep and ep["best"]:
            _, _, sid, t, via = ep["best"]
            names.append(dict(name_line(ep["phrase"], sid, explicit=False, run=run, via=via), t=_ts(t)))
        ep = None

    last_tap, here, entered = None, None, {}  # entered: screen -> the tap that opened it

    for n, e in enumerate(events):
        typ = e.get("type")
        seen = settled_sid(e)
        if seen and seen != here:
            entered[seen], here = last_tap, seen
        if typ == "nav":
            close()
            try:
                ok_phrase = bool(e.get("episode") and e.get("phrase") and check_phrase(e["phrase"]))
            except UserError:
                ok_phrase = False  # never stored: a phrase holding an account number or an email
            if ok_phrase:
                ep = {"phrase": e["phrase"], "words": _words(e["phrase"]), "start": e.get("sid"),
                      "best": None, "actions": 0}
        elif typ in ("case_start", "case_end") or (typ == "launch" and (e.get("fresh") or e.get("restart"))):
            close()
        if typ == "tap" and e.get("ok") is not False:
            last_tap = scrub_selector(e.get("stable") or e.get("selector"), secrets)
        if typ in ("nav", "case_start", "case_end") or ep is None:
            continue
        if typ in ("tap", "type", "key", "swipe", "scroll", "adb", "launch", "open_url"):
            ep["actions"] += 1
            if typ == "key" and e.get("key") in _BACK_KEYS and ep["best"] and ep["best"][0] == 0:
                ep["best"] = None  # left the unrelated screen it checked: not what it was looking for
            if ep["actions"] > EPISODE_ACTIONS:
                close()
        elif typ == "check" and e.get("ok") and e.get("kind", "ui") == "ui" and e.get("selector") \
                and not e.get("gone") and e.get("sid") and e["sid"] != ep["start"]:
            said = _words(_check_label(e))
            score = len(ep["words"] & said) / max(len(ep["words"]), 1)
            if ep["best"] is None or score >= ep["best"][0]:
                ep["best"] = (score, n, e["sid"], e.get("t", ""), entered.get(e["sid"]))
            if score >= 1:
                close()
    close()
    return names


def name_line(phrase: str, screen: str, explicit: bool, run: str | None = None, via: dict | None = None) -> dict:
    """`via`: the tap that opened the screen when it was named. On a template screen (several
    buttons of one screen open it: FAQ articles, order details) only that tap reaches it."""
    return {"k": "name", "phrase": normalise(phrase) or phrase, "screen": screen, "via": via,
            "src": "explicit" if explicit else "learned", "run": run, "t": time.time()}


def forget_line(what: str, key: str) -> dict:
    return {"k": "forget", "what": what, "key": key, "t": time.time()}


# ---------------------------------------------------------------- store

class Store:
    def __init__(self, app: str):
        self.app = app
        self.dir = map_dir() / app
        self.path = self.dir / "map.jsonl"

    def lines(self, warnings: list | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for n, raw in enumerate(self.path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                if warnings is not None:
                    warnings.append(f"{self.path}:{n}: unreadable line skipped")
                continue
            if not isinstance(d, dict):
                continue
            if d.get("k") == "header" and d.get("schema") != SCHEMA:
                if warnings is not None:
                    warnings.append(f"{self.path}: schema {d.get('schema')} not understood; map ignored")
                return []
            out.append(d)
        return out

    def learned(self, run: str) -> bool:
        return any(d.get("k") == "learned" and d.get("run") == run for d in self.lines())

    def schema(self):
        if not self.path.exists():
            return None
        with self.path.open(encoding="utf-8", errors="replace") as f:
            first = f.readline()
        try:
            head = json.loads(first)
        except ValueError:
            return None
        return head.get("schema") if isinstance(head, dict) and head.get("k") == "header" else None

    def foreign_schema(self) -> bool:
        return self.schema() not in (None, SCHEMA)

    def older_schema(self) -> bool:
        """Written by an older tnazar whose screens cannot be compared with today's: set aside."""
        v = self.schema()
        return isinstance(v, int) and v < SCHEMA

    def append(self, obs: list[dict], run: str | None) -> bool:
        """Add observations; a run's are added once. False when nothing was written."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with _Lock(self.dir / "map.lock"):
            if self.older_schema():
                os.replace(self.path, self.path.with_name(f"map.schema{self.schema()}.jsonl"))
            if self.foreign_schema() or (run and self.learned(run)):
                return False
            fresh = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("ab") as f:
                if not fresh:
                    with self.path.open("rb") as r:
                        r.seek(-1, os.SEEK_END)
                        if r.read(1) != b"\n":
                            f.write(b"\n")  # a torn last line from a crash: keep it apart
                lines = ([{"k": "header", "schema": SCHEMA, "app": self.app}] if fresh else []) + list(obs)
                if run:
                    lines.append({"k": "learned", "run": run})
                f.write("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in lines).encode())
        return True

    def lock(self) -> "_Lock":
        self.dir.mkdir(parents=True, exist_ok=True)
        return _Lock(self.dir / "map.lock")

    def rewrite_locked(self, lines: list[dict]) -> None:
        """Replace the file; the caller holds `lock()` around reading what it rewrites."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n"
                               for d in [{"k": "header", "schema": SCHEMA, "app": self.app}] + lines),
                       encoding="utf-8")
        os.replace(tmp, self.path)


class MapLocked(UserError):
    pass


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.f = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:  # no flock (Windows): appends are still one write each
            return self
        self.f = open(self.path, "a")
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self.f.close()
                    raise MapLocked(f"the map is locked by another process: {self.path}") from exc
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.f:
            self.f.close()  # closing releases the lock


# ---------------------------------------------------------------- the folded map

@dataclass
class Edge:
    frm: str
    to: str
    kind: str
    sel: dict | None
    label: str = ""
    scroll: dict | None = None
    ok_runs: set = field(default_factory=set)
    results: list = field(default_factory=list)  # (version, ok, run) in file order

    @property
    def key(self) -> tuple:
        return (self.frm, self.to, self.kind, sel_key(self.sel))


def unsafe_label(*texts) -> bool:
    words = " ".join(t.replace("_", " ").casefold() for t in texts if t)
    flat = " ".join(_WORD.findall(words))
    return any(re.search(rf"\b{re.escape(w)}\b", flat) for w in UNSAFE_WORDS + _extra_unsafe())


def _extra_unsafe() -> tuple:
    raw = os.environ.get("TNAZAR_UNSAFE_WORDS", "")
    return tuple(w.strip().casefold() for w in raw.split(",") if w.strip())


class NavMap:
    def __init__(self, app: str, version: str = ""):
        self.app = app
        self.version = version
        self.screens: dict[str, ident.Ident] = {}
        self.secure: set[str] = set()
        self.alias: dict[str, str] = {}
        self.edges: dict[tuple, Edge] = {}
        self.names: dict[str, list] = {}   # phrase -> [(screen, explicit)] in file order
        self.failed: set = set()
        self.forgot: dict = {}  # (what, key) -> when: evidence older than that is ignored
        self.warnings: list[str] = []

    @classmethod
    def load(cls, app: str, version: str = "", extra: list[dict] | None = None, failed=()) -> "NavMap":
        m = cls(app, version)
        m.failed = {tuple(k) for k in failed}
        lines = Store(app).lines(m.warnings) + list(extra or [])
        for d in lines:
            if d.get("k") == "forget":
                key = (d.get("what"), normalise(d.get("key", "")) if d.get("what") == "name" else d.get("key"))
                m.forgot[key] = max(m.forgot.get(key, 0.0), _when(d))
        for d in lines:
            if d.get("k") != "forget":
                m._fold(d)
        return m

    def _forgotten(self, what: str, key, d: dict) -> bool:
        return _when(d) <= self.forgot.get((what, key), -1.0)

    def canon(self, sid: str | None) -> str | None:
        return self.alias.get(sid, sid) if sid else sid

    def _fold(self, d: dict) -> None:
        k = d.get("k")
        try:
            if k == "header" and d.get("app") not in (None, self.app):
                raise ValueError("another app's map")
            if k == "screen":
                self._screen(d)
            elif k == "edge":
                self._edge(d)
            elif k == "name":
                phrase = normalise(d["phrase"]) or d["phrase"]
                if not self._forgotten("name", phrase, d) and not self._forgotten("screen", d["screen"], d):
                    self.add_name(phrase, self.canon(d["screen"]), d.get("src") == "explicit", d.get("via"))
        except (KeyError, TypeError, ValueError) as exc:
            self.warnings.append(f"map line skipped ({exc}): {str(d)[:80]}")

    def _screen(self, d: dict) -> None:
        sid = d["id"]
        if sid in self.screens or sid in self.alias:
            return
        it = ident.Ident.from_record(d)
        same = ident.match(it, self.screens, one_sided=False)  # never fold a titled page into an untitled one
        if same:
            self.alias[sid] = same
            if it.secure:
                self.secure.add(same)
            return
        self.screens[sid] = it
        if it.secure:
            self.secure.add(sid)

    def _edge(self, d: dict) -> None:
        frm, to = self.canon(d["from"]), self.canon(d["to"])
        if frm == to:
            return  # read as two ids, folded into one screen: the tap went nowhere
        kind = d.get("kind") or "tap"
        sel = d.get("sel") or None
        key = (frm, to, kind, sel_key(sel))
        if any(self._forgotten("screen", s, d) for s in (d["from"], d["to"], frm, to)) \
                or self._forgotten("edge", edge_id(Edge(frm, to, kind, sel)), d):
            return
        e = self.edges.get(key)
        if e is None:
            e = self.edges[key] = Edge(frm, to, kind, sel, d.get("label") or "", d.get("scroll"))
        if d.get("ok"):
            if d.get("run"):
                e.ok_runs.add(d["run"])
            if d.get("label"):
                e.label = d["label"]
        e.results.append((d.get("ver") or "", bool(d.get("ok")), d.get("run")))

    def add_name(self, phrase: str, screen: str, explicit: bool, via: dict | None = None) -> None:
        phrase = normalise(phrase) or phrase
        self.names.setdefault(phrase, []).append((self.canon(screen), explicit, via))

    # -------------------------------------------------- queries

    def resolve(self, phrase: str) -> set:
        """Screens a phrase or a screen id means. More than one: a conflict for the caller."""
        if phrase in self.screens or phrase in self.alias:
            return {self.canon(phrase)}
        binds = self.names.get(normalise(phrase) or phrase, [])
        explicit = [s for s, ex, _ in binds if ex]
        if explicit:
            return {explicit[-1]}
        return {s for s, _, _ in binds}

    def via(self, phrase: str) -> dict | None:
        """The tap a name was learned with (the latest binding that has one)."""
        for _, _, v in reversed(self.names.get(normalise(phrase) or phrase, [])):
            if v:
                return v
        return None

    def template(self, sid: str) -> bool:
        """Several buttons of one screen open this screen: it shows data (an article, an order),
        so arriving says which kind of screen, not which one."""
        froms: dict = {}
        for e in self.edges.values():
            if e.to == sid and e.kind != "back":
                sel = e.sel or {}
                # Counted by what the button says: one button learned by its text and by its id is one.
                said = normalise(e.label or sel.get("text") or sel.get("desc") or "")
                # One button learned by its text and by its id is one way in; the same label on
                # different rows (nth) is several, like different labels.
                froms.setdefault(e.frm, set()).add((said, sel.get("nth", 0)) if said else sel_key(e.sel) or id(e))
        return any(len(v) > 1 for v in froms.values())

    def known_names(self) -> list[str]:
        return sorted(p for p, bs in self.names.items() if bs)

    def unsafe(self, e: Edge) -> bool:
        sel = e.sel or {}
        return e.to in self.secure or unsafe_label(e.label, sel.get("text"), sel.get("desc"), sel.get("id"))

    def retired(self, e: Edge) -> bool:
        mine = [ok for v, ok, _ in e.results if v == self.version or not self.version]
        return len(mine) >= 2 and not mine[-1] and not mine[-2]

    def runnable(self, e: Edge) -> bool:
        return e.kind == "tap" and bool(e.sel) and not self.unsafe(e) and e.key not in self.failed \
            and not self.retired(e)

    def route(self, start: str | None, targets, via: dict | None = None) -> list | None:
        """Fewest hops from `start` to any of `targets` over runnable edges: [] already there,
        None no route. Into a template screen the last hop must be the `via` tap."""
        start = self.canon(start)
        targets = {self.canon(t) for t in targets}
        if not start or not targets:
            return None
        # A template screen (an article, an order) is reached only by the tap its name was learned
        # with; named without one, it cannot be reached at all rather than reached wrongly.
        strict = {t for t in targets if self.template(t)}
        if start in targets - strict:
            return []
        out: dict[str, list] = {}
        for e in self.edges.values():
            if self.runnable(e):
                out.setdefault(e.frm, []).append(e)
        for es in out.values():
            es.sort(key=lambda e: (-len(e.ok_runs), sel_key(e.sel)))
        prev: dict[str, Edge] = {}
        seen, q = {start}, deque([start])
        while q:
            cur = q.popleft()
            for e in out.get(cur, []):
                if e.to in strict and sel_key(e.sel) != sel_key(via):
                    continue
                if e.to in seen and e.to not in strict:
                    continue
                if e.to in strict and e.to in prev:
                    continue
                seen.add(e.to)
                prev[e.to] = e
                if e.to in targets:
                    path, node = [], e.to
                    while node != start:
                        path.append(prev[node])
                        node = prev[node].frm
                    return path[::-1]
                q.append(e.to)
        return None

    def where(self, it: ident.Ident) -> str | None:
        return ident.match(it, self.screens)

    def label(self, sid: str) -> str:
        """A human name for a screen: its name, else the label of a tap that leads to it."""
        for p, bs in self.names.items():
            if any(b[0] == sid for b in bs):
                return p
        labels = [e.label for e in self.edges.values() if e.to == sid and e.label and e.kind != "back"]
        return labels[0] if labels else ""


def _when(d: dict) -> float:
    t = d.get("t")
    if isinstance(t, (int, float)):
        return float(t)
    return _ts(t) if t else 0.0


def edge_id(e: Edge) -> str:
    return hashlib.sha1("|".join(e.key).encode()).hexdigest()[:8]


# ---------------------------------------------------------------- the CLI's side

def stamp(run, screen, it: ident.Ident | None = None) -> str | None:
    """Identify `screen` for the run's app (unless `it` already did) and keep its tokens with the
    run, once per screen."""
    app = run.meta().get("package")
    if not app:
        return None
    it = it or ident.identify(screen, app=app, secrets=secret_values())
    if not it.id:
        return None
    f = run.path / "screens.jsonl"
    known = f.read_text() if f.exists() else ""
    if f'"id": "{it.id}"' not in known:
        with f.open("a") as out:
            out.write(json.dumps(it.to_record()) + "\n")
    return it.id


def run_screens(run) -> dict:
    f = run.path / "screens.jsonl"
    out = {}
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
                out[d["id"]] = d
            except (ValueError, KeyError, TypeError):
                continue
    return out


def entry_tap(events: list[dict], sid: str, screens: dict | None = None) -> dict | None:
    """The tap that last opened screen `sid` in these events (scrubbed), for `map name here`;
    `screens` (the run's records) lets a read after a feed change count as the same screen."""
    c, _ = _run_canon(screens or {})
    sid = c(sid)
    hidden = secret_values()
    last_tap, here, found = None, None, None
    for e in events:
        seen = c(settled_sid(e))
        if seen and seen != here:
            if seen == sid:
                found = last_tap
            here = seen
        if e.get("type") == "tap" and e.get("ok") is not False:
            last_tap = scrub_selector(e.get("stable") or e.get("selector"), hidden)
    return found


def failed_in(events: list[dict]) -> set:
    return {(e["edge"]["from"], e["edge"]["to"], e["edge"].get("kind", "tap"), sel_key(e["edge"].get("sel")))
            for e in events if e.get("type") == "nav" and e.get("outcome") == "failed" and e.get("edge")}


def learn_run(run, app: str, version: str) -> str:
    """Store what a finished run taught; a summary line, or "" when it taught nothing new."""
    obs = learn(run.events(), run.name, version, run_screens(run), secret_values())
    if not any(o["k"] in ("edge", "name") for o in obs):
        return ""
    if not Store(app).append(obs, run.name):
        return ""
    routes = sum(1 for o in obs if o["k"] == "edge" and o["ok"] and o["kind"] == "tap" and o["sel"])
    names = sum(1 for o in obs if o["k"] == "name")
    return f"map: learned {routes} step(s) and {names} name(s) for {app}"


def current(run, app: str, version: str) -> NavMap:
    """The stored map plus what the active run has learned so far (not yet stored)."""
    events = run.events()
    extra = [] if Store(app).learned(run.name) else learn(events, run.name, version, run_screens(run), secret_values())
    return NavMap.load(app, version, extra=extra, failed=failed_in(events))


def compact(app: str, version: str = "") -> int:
    """Rewrite the map folded: screens once, each step's results in order, names, no forgotten
    things. What `NavMap.load` makes of it is unchanged. Holds the lock throughout, so a run
    learned meanwhile waits instead of being lost."""
    store = Store(app)
    if store.foreign_schema():
        raise ValueError(f"{store.path} was written by another tnazar version; left as it is")
    with store.lock():
        m = NavMap.load(app, version)
        lines: list[dict] = []
        for sid, it in m.screens.items():
            lines.append(dict(it.to_record(), k="screen", secure=sid in m.secure))
        for e in m.edges.values():
            for ver, ok, run in e.results:
                lines.append(edge_line(e.frm, e.to, e.kind, e.sel, e.label, e.scroll, run, ver, ok, 0))
        for phrase, binds in m.names.items():
            for sid, explicit, via in binds:
                lines.append({"k": "name", "phrase": phrase, "screen": sid, "via": via,
                              "src": "explicit" if explicit else "learned", "run": None, "t": 0})
        runs = sorted({d["run"] for d in store.lines() if d.get("k") == "learned"})
        lines += [{"k": "learned", "run": r} for r in runs]
        store.rewrite_locked(lines)
    return len(lines) + 1
