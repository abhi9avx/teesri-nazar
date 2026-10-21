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
# A column whose header has one of these words is never copied into a plan, and a value
# following one of them inside any cell is masked. Plans hold only `--env` names for secrets.
SECRET = {"otp", "mpin", "pin", "password", "passcode", "passwd", "secret", "token"}
_WORD = r"\b(" + "|".join(sorted(SECRET)) + r")\b"
# "otp: 1234", "OTP - 1234", "password is x y" (to the next ; or ,), "Enter OTP 123456".
SECRET_VALUE = (re.compile(_WORD + r"(\s*[:=-]\s*|\s+is\s+)([^;,\n]+)", re.I),
                re.compile(_WORD + r"(\s+)(\d[^;,\s]*)", re.I))
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
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", header)  # "PINCode" -> PIN, Code
    return bool({w.casefold() for w in words} & SECRET)


def _hide(value: str) -> str:
    for pattern in SECRET_VALUE:
        value = pattern.sub(lambda m: m.group(1) + m.group(2) + "••••", value)
    return value


def _rows(text: str) -> tuple[list[list[str]], set[int]]:
    """The table's rows, and the indexes of rows that got lines joined into a cell."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return [], set()
    if lines[0].lstrip().startswith("|") or (len(lines) > 1 and "|" in lines[0] and MD_RULE.match(lines[1].strip())):
        return [_md_cells(ln) for ln in lines if not MD_RULE.match(ln.strip())], set()
    if "\t" in lines[0]:
        return _tsv(_tsv_records(text), lines[0].count("\t") + 1)
    return [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)], set()


def _md_cells(line: str) -> list[str]:
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    # "\|" is a pipe inside a cell: park it on a character a table cell never holds.
    return [BR.sub("\n", c.replace("\0", "|")).strip() for c in line.replace("\\|", "\0").split("|")]


def _tsv_records(text: str) -> list[list[str]]:
    """Tab-separated lines. A cell is quoted (and may hold line breaks) only when it starts with
    `"` and its closing quote comes right before a tab or the line's end; any other quote is text."""
    records, row, cell, i, n = [], [], [], 0, len(text)
    at_start = True
    while i < n:
        ch = text[i]
        if at_start and ch == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == '"' and text[j + 1:j + 2] == '"':
                    buf.append('"')
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    buf.append(text[j])
                    j += 1
            if j < n and text[j + 1:j + 2] in ("", "\t", "\n"):
                cell, i, at_start = buf, j + 1, False
                continue
        if ch == "\t":
            row.append("".join(cell))
            cell, at_start = [], True
        elif ch == "\n":
            row.append("".join(cell))
            records.append(row)
            row, cell, at_start = [], [], True
        else:
            cell.append(ch)
            at_start = False
        i += 1
    if cell or row:
        records.append(row + ["".join(cell)])
    return [r for r in records if any(c.strip() for c in r)]


def _tsv(records: list[list[str]], width: int) -> tuple[list[list[str]], set[int]]:
    """A chat paste drops a cell's quotes: a line with no tab continues the cell above, and the
    line with tabs that ends such a cell completes its row. A row is never joined otherwise."""
    rows: list[list[str]] = []
    joined: set[int] = set()
    open_cell = False
    for cells in records:
        if rows and len(cells) == 1 and not _is_header(rows[-1]):
            rows[-1][-1] += "\n" + cells[0]
            joined.add(len(rows) - 1)
            open_cell = True
        elif rows and open_cell and len(rows[-1]) + len(cells) - 1 <= width:
            rows[-1][-1] += "\n" + cells[0]
            rows[-1] += cells[1:]
            open_cell = len(rows[-1]) < width  # a later cell of the same row may have line breaks too
        else:
            rows.append(cells)
            open_cell = False
    return rows, joined


def _is_header(row: list[str]) -> bool:
    return sum(1 for h in row if _field_of(h)) >= 2


def _split_expected(cell: str) -> list[str]:
    parts = [p for p in cell.split("\n") if p.strip()]
    if len(parts) == 1 and ";" in parts[0]:
        parts = parts[0].split(";")
    return [t for t in (_flat(BULLET.sub("", p)) for p in parts) if t]


def read(text: str, columns: str | None = None, report: dict | None = None) -> list[Imported]:
    """Cases from the table. `report` (when given) gets what the reader decided on its own:
    `headings`, `joined` (cases with lines joined into a cell), `short` (rows with fewer cells than
    the header) and `not_copied` (secret-looking columns), so nothing is decided silently."""
    report = {} if report is None else report
    rows, joined = _rows(text)
    if not rows:
        raise IntakeError("no rows found")
    if columns:
        headers = [c.strip() for c in columns.split(",")]
        unknown = [h for h in headers if h and not _field_of(h) and not _secret(h)]
        if unknown:
            raise IntakeError(f"--columns: unknown name(s) {', '.join(unknown)}; use {', '.join(FIELDS)}")
        body, offset = rows, 0
    else:
        headers, body, offset = rows[0], rows[1:], 1
        if not _is_header(headers):
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
    report["not_copied"] = [h for h in headers if _secret(h)]
    report.update(headings=[], joined=[], short=[])

    id_col = names.index("id") if "id" in names else None
    taken = {r[id_col].strip() for r in body if id_col is not None and len(r) > id_col}
    cases, section, seen, next_n = [], "", {}, 1
    for n, row in enumerate(body, 1):
        cells = {name: _hide(c.strip()) for name, c in zip(names, row) if not _secret(name)}
        cells.update({f"column {i}": _hide(c.strip()) for i, c in enumerate(row[len(names):], len(names) + 1)
                      if c.strip()})
        filled = [c for c in row if c.strip()]
        if not filled:
            continue
        # A heading row ("Notes app") fills only its first cell. Under an ID column, a lone value
        # that could be an ID is a case.
        first = row[0].strip()
        if len(filled) == 1 and first and len(names) >= 3 and (names[0] != "id" or not SAFE_ID.match(first)):
            section = first
            report["headings"].append(first)
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
        if n - 1 + offset in joined:
            report["joined"].append(cid)
        if len(row) < len(names) and len(filled) > 1:
            report["short"].append(f"{cid} ({len(row)} of {len(names)} cells)")
        extra = {k: v for k, v in cells.items() if v and k not in ("id", "title", "expected", "steps")}
        cases.append(Imported(cid, _flat(cells.get("title", "")), _split_expected(cells.get("expected", "")),
                              [s.strip() for s in cells.get("steps", "").split("\n") if s.strip()], section, extra))
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
