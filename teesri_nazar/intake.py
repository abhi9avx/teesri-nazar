"""Test cases from a sheet (CSV, TSV pasted from a spreadsheet, or a Markdown table) to a draft plan.

The draft holds every case with its expected results as `expect` lines and everything else as
comments; the tester writes the steps and checks. `plan.parse` refuses to run it until every case
has a check and every expected result a `check --for`, so nothing imported is skipped silently.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

FIELDS = {
    "id": {"id", "testid", "testcaseid", "tcid", "caseid", "key"},
    "title": {"title", "testcase", "testcasename", "name", "scenario", "summary", "testscenario"},
    "description": {"description", "objective", "desc"},
    "precondition": {"precondition", "preconditions", "prerequisite", "prerequisites"},
    "steps": {"steps", "teststeps", "step", "procedure"},
    "data": {"testdata", "data"},
    "expected": {"expected", "expectedresult", "expectedresults", "expectedoutcome", "expectedbehaviour",
                 "expectedbehavior"},
    "priority": {"priority", "severity"},
    "status": {"status"},
    "account": {"account", "phone", "phonenumber", "mobile", "uid", "user", "userid", "testaccount", "login"},
}
# A column whose header has one of these words is never copied into a plan; neither is a
# `word: value` with one inside another cell. Plans hold only `--env` names for secrets.
SECRET = {"otp", "mpin", "pin", "password", "passcode", "passwd", "secret", "token"}
SECRET_VALUE = re.compile(r"\b(" + "|".join(sorted(SECRET)) + r")\b(\s*[:=]\s*)\S+", re.I)
COMMENTED = ("description", "precondition", "data", "priority", "status", "account")
LABEL = {"data": "test data"}
BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])(?:\s+|$)")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
BR = re.compile(r"<br\s*/?>", re.I)
MD_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


class IntakeError(ValueError):
    pass


@dataclass
class Imported:
    id: str
    title: str
    expects: list[str]
    steps: list[str] = field(default_factory=list)
    section: str = ""
    fields: dict = field(default_factory=dict)  # every other column, by field name or original header


def _flat(s: str) -> str:
    return " ".join(s.split())


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.casefold())


def _field_of(header: str) -> str | None:
    k = _key(header)
    return next((f for f, names in FIELDS.items() if k in names), None)


def _secret(header: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", header.casefold())) & SECRET)


def _hide(value: str) -> str:
    return SECRET_VALUE.sub(lambda m: m.group(1) + m.group(2) + "••••", value)


def _rows(text: str) -> list[list[str]]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []
    if lines[0].lstrip().startswith("|") or (len(lines) > 1 and "|" in lines[0] and MD_RULE.match(lines[1].strip())):
        return [_md_cells(ln) for ln in lines if not MD_RULE.match(ln.strip())]
    if "\t" in lines[0]:
        return _tsv(text, lines[0].count("\t") + 1)
    return [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]


def _md_cells(line: str) -> list[str]:
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    return [BR.sub("\n", c.replace("\0", "|")).strip() for c in line.replace("\\|", "\0").split("|")]


def _tsv(text: str, width: int) -> list[list[str]]:
    """Tab-separated rows. A sheet quotes a cell with line breaks; a chat paste may not, so an
    unquoted short line continues the row before it while the two still fit in `width` cells."""
    rows: list[list[str]] = []
    for cells in csv.reader(io.StringIO(text), delimiter="\t"):
        if not any(c.strip() for c in cells):
            continue
        prev = rows[-1] if rows else None
        if prev is not None and len(prev) < width and len(prev) + len(cells) - 1 <= width:
            prev[-1] += "\n" + cells[0]
            prev += cells[1:]
        else:
            rows.append(cells)
    return rows


def _split_expected(cell: str) -> list[str]:
    parts = [p for p in cell.split("\n") if p.strip()]
    if len(parts) == 1 and ";" in parts[0]:
        parts = parts[0].split(";")
    return [t for t in (_flat(BULLET.sub("", p)) for p in parts) if t]


def read(text: str, columns: str | None = None) -> list[Imported]:
    rows = _rows(text)
    if not rows:
        raise IntakeError("no rows found")
    if columns:
        headers = [c.strip() for c in columns.split(",")]
        unknown = [h for h in headers if h and not _field_of(h) and not _secret(h)]
        if unknown:
            raise IntakeError(f"--columns: unknown name(s) {', '.join(unknown)}; use {', '.join(FIELDS)}")
        body = rows
    else:
        headers, body = rows[0], rows[1:]
        if sum(1 for h in headers if _field_of(h)) < 2:
            sample = next((r for r in rows if sum(1 for c in r if c.strip()) > 1), rows[0])
            preview = " | ".join(c for c in sample if c.strip())[:120]
            raise IntakeError(f"no header row found (first case row: {preview}). Name the columns in order, e.g. "
                              f"--columns title,description,precondition,steps,data,expected,priority,status,account")
    names = [_field_of(h) or h.strip() for h in headers]
    for f in FIELDS:
        same = [h for h, n in zip(headers, names) if n == f]
        if len(same) > 1:
            raise IntakeError(f"columns {' and '.join(repr(h) for h in same)} both mean {f}: rename or remove one")
    if "title" not in names and "id" not in names:
        raise IntakeError("no title or ID column: name one in the header or with --columns")

    taken = {r[names.index("id")].strip() for r in body if "id" in names and len(r) > names.index("id")}
    cases, section, seen, next_n = [], "", {}, 1
    for n, row in enumerate(body, 1):
        cells = {name: c.strip() for name, c in zip(names, row)}
        cells.update({f"column {i}": c.strip() for i, c in enumerate(row[len(names):], len(names) + 1) if c.strip()})
        filled = [c for c in row if c.strip()]
        if not filled:
            continue
        # A heading row ("Notes app") fills only a title in the first column; an ID alone is a case.
        if len(filled) == 1 and names[0] == "title" and row[0].strip() and len(names) >= 3:
            section = row[0].strip()
            continue
        cid = cells.get("id", "")
        if not cid:
            while f"TC-{next_n:02d}" in taken:
                next_n += 1
            cid = f"TC-{next_n:02d}"
            taken.add(cid)
        if not SAFE_ID.match(cid):
            raise IntakeError(f"row {n}: case ID {cid!r} must be one word of letters, digits, '.', '_', ':', '/' or '-'")
        if cid in seen:
            raise IntakeError(f"case ID {cid} is used twice: rows {seen[cid]} and {n}")
        seen[cid] = n
        extra = {k: _hide(v) for k, v in cells.items() if v and k not in ("id", "title", "expected", "steps")
                 and not _secret(k)}
        cases.append(Imported(cid, _flat(cells.get("title", "")), _split_expected(_hide(cells.get("expected", ""))),
                              [_hide(s.strip()) for s in cells.get("steps", "").split("\n") if s.strip()],
                              section, extra))
    return cases


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_plan(cases: list[Imported], app: str, name: str, source: str) -> str:
    out = [f"# Imported from {source} by `tnazar cases import`: {len(cases)} case(s).",
           "# Write each case's steps and checks, proving every `expect` with `check ... --for E<n>`;",
           "# `./tnazar plan review <this file>` lists what is still missing. Secrets only as --env NAME.",
           f"app {app}", f"name {name}", ""]
    section = None
    for c in cases:
        if c.section != section and c.section:
            out += [f"# == {c.section} ==", ""]
        section = c.section
        out.append(f"case {c.id} {_q(c.title)}" if c.title else f"case {c.id}")
        for key in COMMENTED:
            if c.fields.get(key):
                out.append(f"  # {LABEL.get(key, key)}: {_flat(c.fields[key])}")
        for key, value in c.fields.items():
            if key not in FIELDS:
                out.append(f"  # {key}: {_flat(value)}")
        if c.steps:
            out.append("  # steps:")
            out += [f"  #   {s}" for s in c.steps]
        out += [f"  expect {_q(e)}" for e in c.expects]
        out.append("")
    return "\n".join(out)
