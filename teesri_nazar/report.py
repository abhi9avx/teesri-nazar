"""Turn a run's event log into a pass/fail report (Markdown + self-contained HTML)."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .session import Run

ICON = {"pass": "✅", "fail": "❌", "blocked": "⛔", "skipped": "⏭", "running": "⏳"}
ACTIONS = {"launch", "open_url", "tap", "type", "key", "scroll", "swipe", "adb"}


def case_events(events: list[dict], case: str) -> list[dict]:
    """Events of the latest run of `case`."""
    out, inside = [], False
    for e in events:
        if e["type"] == "case_start":
            inside = e["case"] == case
            if inside:
                out = []
        elif e["type"] == "case_end":
            inside = False
        elif inside:
            out.append(e)
    return out


def build_cases(events: list[dict]) -> list[dict]:
    cases: list[dict] = []
    current = None
    loose = {"id": "-", "title": "outside any case", "status": None, "reason": "",
             "events": [], "start": None, "end": None}
    for e in events:
        if e["type"] == "case_start":
            current = {"id": e["case"], "title": e.get("title", ""), "status": "running",
                       "reason": "", "events": [], "start": e["t"], "end": None,
                       "evidence": [], "crash": None}
            # A re-run of the same case replaces the earlier attempt, in its original position.
            prior = next((i for i, c in enumerate(cases) if c["id"] == current["id"]), None)
            if prior is None:
                cases.append(current)
            else:
                cases[prior] = current
        elif e["type"] == "case_end" and current:
            current.update(status=e["status"], reason=e.get("reason", ""), end=e["t"],
                           evidence=e.get("evidence", []), crash=e.get("crash"))
            current = None
        else:
            (current or loose)["events"].append(e)
    for c in cases + [loose]:
        c["checks"] = [e for e in c["events"] if e["type"] == "check"]
        c["notes"] = [e for e in c["events"] if e["type"] == "note"]
        c["shots"] = [e for e in c["events"] if e["type"] == "shot"]
        c["steps"] = sum(1 for e in c["events"] if e["type"] in ACTIONS)
    if loose["checks"] or loose["notes"]:
        loose["status"] = "fail" if any(not c["ok"] for c in loose["checks"]) else "pass"
        cases.append(loose)
    return cases


def summarize(run: Run) -> dict:
    cases = build_cases(run.events())
    counts = {k: 0 for k in ICON}
    for c in cases:
        if c["id"] != "-":
            counts[c["status"]] = counts.get(c["status"], 0) + 1
    checks = [ch for c in cases for ch in c["checks"]]
    return {
        "cases": cases,
        "counts": counts,
        "checks_total": len(checks),
        "checks_failed": sum(1 for ch in checks if not ch["ok"]),
        "bugs": [n for c in cases for n in c["notes"] if n.get("kind") == "bug"],
    }


def summary_line(s: dict) -> str:
    c = s["counts"]
    parts = [f"{ICON[k]} {c[k]} {k}" for k in ("pass", "fail", "blocked", "skipped", "running") if c.get(k)]
    total = sum(c.values())
    head = f"{total} case(s): " + ("  ".join(parts) if parts else "none recorded")
    return f"{head}  | checks {s['checks_total'] - s['checks_failed']}/{s['checks_total']} passed"


def _duration(a: str | None, b: str | None) -> str:
    if not a or not b:
        return ""
    fmt = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))  # noqa: E731
    secs = int((fmt(b) - fmt(a)).total_seconds())
    return f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"


def _check_line(ch: dict) -> str:
    mark = "✅" if ch["ok"] else "❌"
    line = f"{mark} [{ch.get('kind', 'ui')}] {ch['what']}"
    if ch.get("detail"):
        line += f" — {ch['detail']}"
    return line


def to_markdown(run: Run, s: dict) -> str:
    m = run.meta()
    out = [f"# Teesri Nazar report: {m['name']}", ""]
    app = f"{m.get('package') or '-'} {m.get('app_version', '')}".strip()
    out += [
        "| | |", "|---|---|",
        f"| App | {app} |",
        f"| Device | {m.get('model', '')} ({m.get('device', '')}), Android {m.get('android', '')} |",
        f"| Started | {m.get('started_at', '')} |",
        f"| Duration | {_duration(m.get('started_at'), m.get('finished_at')) or 'running'} |",
        "",
    ]
    if m.get("prompt"):
        out += ["**Request:** " + m["prompt"], ""]
    out += ["## Summary", "", summary_line(s), ""]
    cases = s["cases"]
    if cases:
        out += ["| Case | Title | Result | Checks | Reason |", "|---|---|---|---|---|"]
        for c in cases:
            ok = sum(1 for ch in c["checks"] if ch["ok"])
            out.append(f"| {c['id']} | {c['title']} | {ICON.get(c['status'], '')} {c['status']} | "
                       f"{ok}/{len(c['checks'])} | {c['reason']} |")
        out.append("")
    for c in cases:
        out += [f"## {ICON.get(c['status'], '')} {c['id']}: {c['title']}".rstrip(": "), ""]
        meta = f"{c['steps']} step(s)"
        if _duration(c.get("start"), c.get("end")):
            meta += f", {_duration(c['start'], c['end'])}"
        out += [f"*{c['status'].upper()}* — {meta}" + (f" — {c['reason']}" if c["reason"] else ""), ""]
        for ch in c["checks"]:
            line = "- " + _check_line(ch)
            if ch.get("shot"):
                line += f" ([screenshot]({ch['shot']}))"
            out.append(line)
        for n in c["notes"]:
            out.append(f"- 📝 **{n['kind']}**: {n['text']}")
        if c.get("crash"):
            out.append(f"- 💥 crash/ANR detected: [{c['crash']}]({c['crash']})")
        for ev in c.get("evidence", []):
            out.append(f"- evidence: [{ev}]({ev})")
        out.append("")
    if s["bugs"]:
        out += ["## Bugs found", ""] + [f"- {b['text']} (case {b.get('case') or '-'})" for b in s["bugs"]] + [""]
    return "\n".join(out)


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--card:#f7f7f9;--pass:#1a7f37;--fail:#cf222e;--block:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#111113;--fg:#ececf1;--muted:#a1a1aa;--line:#2a2a30;--card:#1a1a1e;--pass:#3fb950;--fail:#f85149;--block:#d29922}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted{color:var(--muted)}table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid currentColor}
.pass{color:var(--pass)}.fail{color:var(--fail)}.blocked,.running{color:var(--block)}.skipped{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
.shots{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.shots img{height:220px;border:1px solid var(--line);border-radius:6px}
ul{margin:6px 0;padding-left:20px}code{font-size:13px}
.tiles{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 14px}
.tile b{display:block;font-size:22px}
"""


def to_html(run: Run, s: dict) -> str:
    m = run.meta()
    e = html.escape
    app = f"{m.get('package') or '-'} {m.get('app_version', '')}".strip()
    rows = []
    for c in s["cases"]:
        ok = sum(1 for ch in c["checks"] if ch["ok"])
        rows.append(f"<tr><td><a href='#c-{e(c['id'])}'>{e(c['id'])}</a></td><td>{e(c['title'])}</td>"
                    f"<td><span class='pill {c['status']}'>{c['status']}</span></td>"
                    f"<td>{ok}/{len(c['checks'])}</td><td>{e(c['reason'])}</td></tr>")
    tiles = "".join(f"<div class='tile {k}'><b>{s['counts'].get(k, 0)}</b>{k}</div>"
                    for k in ("pass", "fail", "blocked", "skipped"))
    cards = []
    for c in s["cases"]:
        items = []
        for ch in c["checks"]:
            items.append(f"<li class='{'pass' if ch['ok'] else 'fail'}'>{e(_check_line(ch))}</li>")
        for n in c["notes"]:
            items.append(f"<li>📝 <b>{e(n['kind'])}</b>: {e(n['text'])}</li>")
        if c.get("crash"):
            items.append(f"<li class='fail'>💥 crash/ANR: <a href='{e(c['crash'])}'>{e(c['crash'])}</a></li>")
        for ev in c.get("evidence", []):
            if not ev.endswith(".png"):
                items.append(f"<li>evidence: <a href='{e(ev)}'>{e(ev)}</a></li>")
        imgs = [ch["shot"] for ch in c["checks"] if ch.get("shot")] + [x["file"] for x in c["shots"]] \
            + [ev for ev in c.get("evidence", []) if ev.endswith(".png")]
        shots = "".join(f"<a href='{e(p)}'><img loading='lazy' src='{e(p)}' alt=''></a>" for p in imgs)
        cards.append(
            f"<div class='card' id='c-{e(c['id'])}'><div><span class='pill {c['status']}'>{c['status']}</span> "
            f"<b>{e(c['id'])}</b> {e(c['title'])} <span class='muted'>· {c['steps']} steps "
            f"{_duration(c.get('start'), c.get('end'))}</span></div>"
            + (f"<div class='muted'>{e(c['reason'])}</div>" if c["reason"] else "")
            + (f"<ul>{''.join(items)}</ul>" if items else "")
            + (f"<div class='shots'>{shots}</div>" if shots else "") + "</div>")
    prompt = f"<p><b>Request:</b> {e(m['prompt'])}</p>" if m.get("prompt") else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Teesri Nazar · {e(m['name'])}</title>
<style>{CSS}</style></head><body><main>
<h1>Teesri Nazar · {e(m['name'])}</h1>
<div class="muted">{e(app)} · {e(m.get('model', ''))} ({e(m.get('device', ''))}) Android {e(m.get('android', ''))}
· {e(m.get('started_at', ''))} · {_duration(m.get('started_at'), m.get('finished_at')) or 'running'}</div>
{prompt}<div class="tiles">{tiles}</div>
<p class="muted">checks {s['checks_total'] - s['checks_failed']}/{s['checks_total']} passed</p>
<table><tr><th>Case</th><th>Title</th><th>Result</th><th>Checks</th><th>Reason</th></tr>{''.join(rows)}</table>
<h2>Details</h2>{''.join(cards)}
</main></body></html>
"""


def write(run: Run) -> list[Path]:
    s = summarize(run)
    md = run.path / "report.md"
    page = run.path / "report.html"
    md.write_text(to_markdown(run, s))
    page.write_text(to_html(run, s))
    return [md, page]
