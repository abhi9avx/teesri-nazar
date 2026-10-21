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
# Never copied into a plan: plans hold only `--env` names for these.
SECRET = {"otp", "mpin", "pin", "password", "passcode", "secret", "token"}
COMMENTED = (("description", "description"), ("precondition", "precondition"), ("data", "test data"),
             ("priority", "priority"), ("status", "status"), ("account", "account"))
BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")


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


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.casefold())


def _field_of(header: str) -> str | None:
    k = _key(header)
    return next((f for f, names in FIELDS.items() if k in names), None)


def _rows(text: str) -> list[list[str]]:
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n")]
    first = next((ln for ln in lines if ln.strip()), "")
    if first.lstrip().startswith("|"):
        rows = []
        for ln in lines:
            if not ln.strip().startswith("|"):
                continue
            cells = [c.strip().replace("<br>", "\n").replace("<br/>", "\n") for c in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
        return rows
    if "\t" in first:
        return _tsv(lines, first.count("\t"))
    return [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]


def _tsv(lines: list[str], tabs: int) -> list[list[str]]:
    """Sheets paste a cell's line breaks as they are: a row ends once it has all its tabs."""
    rows, buf = [], ""
    for ln in lines:
        buf = f"{buf}\n{ln}" if buf else ln
        if buf.count("\t") >= tabs:
            rows.append([_unquote(c) for c in buf.split("\t")])
            buf = ""
    if buf.strip():
        rows.append([_unquote(c) for c in buf.split("\t")])
    return [r for r in rows if any(c.strip() for c in r)]


def _unquote(cell: str) -> str:
    cell = cell.strip()
    if len(cell) >= 2 and cell[0] == cell[-1] == '"':
        cell = cell[1:-1].replace('""', '"')
    return cell


def _split_expected(cell: str) -> list[str]:
    parts = [p for p in cell.split("\n") if p.strip()]
    if len(parts) == 1 and ";" in parts[0]:
        parts = parts[0].split(";")
    return [" ".join(BULLET.sub("", p).split()) for p in parts if BULLET.sub("", p).strip()]


def read(text: str, columns: str | None = None) -> list[Imported]:
    rows = _rows(text)
    if not rows:
        raise IntakeError("no rows found")
    if columns:
        headers = [c.strip() for c in columns.split(",")]
        unknown = [h for h in headers if h and not _field_of(h) and _key(h) not in SECRET]
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
    if "title" not in names and "id" not in names:
        raise IntakeError("no title or ID column: name one in the header or with --columns")

    cases, section, seen = [], "", {}
    for n, row in enumerate(body, 1):
        cells = dict(zip(names, (c.strip() for c in row)))
        filled = [c for c in row if c.strip()]
        # A heading row ("Overdue users") fills only its first cell; in a narrow table that is a case.
        if len(filled) == 1 and row[0].strip() and len(names) >= 3:
            section = row[0].strip()
            continue
        if not filled:
            continue
        cid = cells.get("id") or f"TC-{len(cases) + 1:02d}"
        if re.search(r"\s", cid):
            raise IntakeError(f"row {n}: case ID {cid!r} has spaces; a plan case ID is one word")
        if cid in seen:
            raise IntakeError(f"case ID {cid} is used twice: rows {seen[cid]} and {n}")
        seen[cid] = n
        extra = {k: v for k, v in cells.items() if v and k not in ("id", "title", "expected", "steps")
                 and _key(k) not in SECRET}
        cases.append(Imported(cid, " ".join(cells.get("title", "").split()),
                              _split_expected(cells.get("expected", "")),
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
        for key, label in COMMENTED:
            if c.fields.get(key):
                out.append(f"  # {label}: {' '.join(c.fields[key].split())}")
        for key, value in c.fields.items():
            if key not in FIELDS:
                out.append(f"  # {key}: {' '.join(value.split())}")
        if c.steps:
            out.append("  # steps:")
            out += [f"  #   {s}" for s in c.steps]
        out += [f"  expect {_q(e)}" for e in c.expects]
        out.append("")
    return "\n".join(out)
