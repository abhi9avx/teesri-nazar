"""Which cases a run was asked to execute, and which expected results its checks proved.

Pure: event lists in, plain values out. The CLI, the plan runner, the report, replay and the
dashboard all ask here, so the rules live in one place (docs/trd-coverage.md §3).
"""

from __future__ import annotations


def case_plan(case: str, title: str = "", expects: list[str] | None = None, added: bool = False) -> dict:
    """The event that puts a case on the requested list."""
    return {"type": "case_plan", "case": case, "title": title, "expects": list(expects or []), "added": added}


def declared(events: list[dict]) -> bool:
    """A run is declared once any case was planned: only then can it say what never ran."""
    return any(e["type"] == "case_plan" for e in events)


def requested(events: list[dict]) -> list[dict]:
    """The planned cases in first-declared order; a later `case_plan` for the same id wins."""
    out: dict[str, dict] = {}
    for e in events:
        if e["type"] == "case_plan":
            out[e["case"]] = {"id": e["case"], "title": e.get("title", ""),
                              "expects": list(e.get("expects", [])), "added": bool(e.get("added"))}
    return list(out.values())


def expects_of(events: list[dict], case: str) -> list[str]:
    return next((c["expects"] for c in requested(events) if c["id"] == case), [])


def expectation_states(expects: list[str], checks: list[dict]) -> list[dict]:
    """Per expected result E1, E2…: failed if a linked check failed, verified if one passed, else unverified."""
    out = []
    for n, text in enumerate(expects, 1):
        eid = f"E{n}"
        # A require proves nothing, and a check on a screen read empty saw nothing either way.
        linked = [c for c in checks if eid in c.get("for", []) and not c.get("setup") and not c.get("blank_screen")]
        state = ("failed" if any(not c["ok"] for c in linked)
                 else "verified" if linked else "unverified")
        out.append({"id": eid, "text": text, "state": state, "by": [c["what"] for c in linked]})
    return out


def not_started(events: list[dict]) -> list[dict]:
    """Requested cases that never began and have no `not_run` row yet."""
    seen = {e["case"] for e in events if e["type"] in ("case_start", "not_run")}
    return [c for c in requested(events) if c["id"] not in seen]


def tally(statuses: list[str], checks_failed: int) -> dict:
    """verified = pass + fail; complete = every case has one of them; green = all passed, no failed check."""
    verified = sum(1 for s in statuses if s in ("pass", "fail"))
    complete = verified == len(statuses)
    return {"requested": len(statuses), "verified": verified, "complete": complete,
            "green": complete and all(s == "pass" for s in statuses) and not checks_failed}


def scope(source: str, fingerprint: str, approved: str, requirements: list[dict], cases: list[dict],
          selected: list[str]) -> dict:
    """The event that records what a requirements plan claims: every requirement, every case's links
    (selected or not) and the cases this `run` call selected. `case_plan` stays selected-only."""
    return {"type": "scope", "source": source, "fingerprint": fingerprint, "approved": approved,
            "requirements": requirements, "cases": cases, "selected": selected}


def requirement_states(events: list[dict], cases: list[dict], case_label) -> dict | None:
    """Per requirement: verified, failed, not verified, not covered or excluded, and the run's
    Requirements verdict. None when no `scope` was recorded (requirements not declared).

    `cases` are the report's built cases (status, cause, crash, expectation states);
    `case_label(status, cause)` words a case's result. A link counts only when its expected result
    was verified and its case passed; a requirement is verified only when every link is.
    """
    scopes = [e for e in events if e["type"] == "scope"]
    if not scopes:
        return None
    last = scopes[-1]
    selected = {cid for s in scopes for cid in s.get("selected", [])}
    built = {c["id"]: c for c in cases}
    links: dict[str, list[tuple[str, int]]] = {r["id"]: [] for r in last["requirements"]}
    for c in last["cases"]:
        for n, x in enumerate(c["expects"], 1):
            for rid in x.get("reqs", []):
                links[rid].append((c["id"], n))

    def link(cid: str, n: int) -> tuple[str, str]:
        c = built.get(cid)
        if cid not in selected:
            return "unverified", f"{cid} not selected"
        if c is None or c["status"] in ("not_run", "pending", "running"):
            return "unverified", f"{cid} not run"
        x = next((x for x in c.get("expects", []) if x["id"] == f"E{n}"), None)
        if x and x["state"] == "failed":
            return "failed", f"{cid} E{n} failed: {'; '.join(x['by'])}"
        if x and x["state"] == "verified" and c["status"] == "pass":
            return "verified", f"{cid} E{n} ← {'; '.join(x['by'])}"
        if c["status"] == "pass" or c.get("cause") in (None, "unverified") and not c.get("crash"):
            return "unverified", f"{cid} E{n} not verified"
        return "unverified", f"{cid} {'failed: crash' if c.get('crash') else case_label(c['status'], c.get('cause'))}"

    rows, problems = [], []
    for r in last["requirements"]:
        got = [link(cid, n) for cid, n in links[r["id"]]]
        if r.get("out_of_scope"):
            state, detail = "excluded", f"out of scope: {r['out_of_scope']}"
        elif not got:
            state, detail = "not_covered", "no expected result links to it"
            problems.append(f"{r['id']} not covered")
        else:
            state = ("failed" if any(s == "failed" for s, _ in got)
                     else "verified" if all(s == "verified" for s, _ in got) else "unverified")
            shown = [d for s, d in got if state == "verified" or s == state]
            detail = "; ".join(shown)
            if state != "verified":
                problems.append(f"{r['id']} {'failed' if state == 'failed' else 'not verified'} ({detail})")
        rows.append({**r, "state": state, "detail": detail})

    scope_problems = []
    if len({s["fingerprint"] for s in scopes}) > 1:
        scope_problems.append("scope changed mid-run")
    if not last.get("approved"):
        scope_problems.append("scope not approved")
    elif last["approved"] != last["fingerprint"]:
        scope_problems.append("approved fingerprint does not match")
    total = len(rows)
    verified = sum(1 for r in rows if r["state"] == "verified")
    excluded = [r for r in rows if r["state"] == "excluded"]
    problems = scope_problems + problems
    if problems:
        line = "incomplete — " + "; ".join(problems)
    elif len(excluded) == total:
        line = "no in-scope requirement: feature verification not claimed"
    elif excluded:
        line = (f"all in-scope requirements verified; {len(excluded)} excluded ("
                + "; ".join(f"{r['id']}: {r['out_of_scope']}" for r in excluded) + ")")
    else:
        line = "all requirements verified"
    return {"rows": rows, "verified": verified, "total": total, "excluded": len(excluded),
            "ok": not problems and len(excluded) < total, "line": line, "source": last.get("source", "")}
