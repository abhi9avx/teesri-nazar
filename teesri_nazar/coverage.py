"""Which cases a run was asked to execute, and which expected results its checks proved.

Pure: event lists in, plain values out. The CLI, the plan runner, the report, replay and the
dashboard all ask here, so the rules live in one place (docs/trd-coverage.md §3).
"""

from __future__ import annotations


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
        linked = [c for c in checks if eid in c.get("for", [])]
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
