# TRD — Coverage ledger: technical design

| | |
|---|---|
| Status | **Built v0.3**, 2026-10-21. Design review findings H1–H3, M1–M9, L1–L9 folded in (PRD R1–R7). Code review (`ai-code-review`, standards + spec): no HIGH; MED test gaps and the LOWs fixed (shared `judge.close_run`, one `coverage.case_plan` builder, `--for` read by the CLI parser, "added during run" mark, both selections named on a continuation). |
| PRD | [prd-coverage.md](prd-coverage.md) (Q-numbers refer to its decisions log) |
| Test plan | [test-plan-coverage.md](test-plan-coverage.md) |
| Constraints | Standard library only; Python 3.9 (no `match`, no `X | None` outside annotations); append-only `events.jsonl`; exit-code meanings unchanged (ARCHITECTURE.md §11) |
| Estimate | 5 working days (§10) |

---

## 1. Shape of the change

One new **pure** module holds every coverage rule; everything else only records events or asks it.

```
plan.py ──(Case.expects, --for checked)──► runner.py ──logs case_plan──┐
cli.py  `case plan`, `check --for`  ──────────────────logs case_plan, check.for ─┤
                                                                               ▼
                                                                         events.jsonl
                                                                               │
judge._end_case ──asks──► coverage.py (pure) ◄──asks── report.py / ui.py / replay.py
finish_run ─────asks "who never started?"──► logs not_run
```

`coverage.py` takes event lists and returns plain dicts; it never touches the device or disk, so
every rule is unit-tested without a phone.

## 2. Event model (additive only)

| Event | Fields | Written by |
|---|---|---|
| **`case_plan`** (new) | `case`, `title`, `expects: [str]`, `added: bool` | `run` (per selected case, before the first `case start`), `case plan`, `case start` of an undeclared case when the run already has a declared list, replay (copied from the source run) |
| **`not_run`** (new) | `case`, `title`, `reason` | `finish_run` and replay's finish, once per requested case that has no `case_start` |
| `check` (existing) | **+ `for: ["E1", …]`** (optional) | `check --for`; replay's re-check copies it |
| `case_end` (existing) | unchanged fields; reason text may name E's | `judge._end_case` |
| run meta (existing) | **+ `stop_reason`** (str), **+ `selection`** `{"plan_cases": 12, "how": "--only TC-05"}` | runner, **only when it creates the run** (a `--from` continuation leaves it; the header counts `case_plan` IDs against `plan_cases`) |

Rules:

- The **requested list** is the ordered, de-duplicated set of `case_plan.case`; the latest
  `case_plan` for an ID wins (Q12 re-declare). A run is **declared** iff it has ≥1 `case_plan`.
- `not_run` is written once per run, at the end. A case started later (e.g. `--from` after a
  finish is impossible: a finished run is closed) never coexists with its `not_run`; the report
  still prefers a real attempt over `not_run` if both exist (defensive).
- Consumers of the event log, checked one by one (review H3, M1):
  - `report.build_cases`: switches on `case_start`/`case_end`; unknown events fall into the
    current case's events or the case-less row. `case_plan`/`not_run` are logged with no case open
    or are handled explicitly (§4.5), so they never land in a case's step list.
  - `replay.py`: any event not in `NARRATION_EVENTS` is treated as a device step (`step += 1`,
    `_do`, 0.3 s sleep). **Add `case_plan` and `not_run` to `commands.NARRATION_EVENTS`.**
  - `ui.html layout()`: unknown types become `• <type>` rows. **Skip `case_plan`** like `seen`;
    give `not_run` its own block (§4.7).
  - `navmap.learn_run`, `usage.per_case`: ignore unknown types (verified).

## 3. `teesri_nazar/coverage.py` (new, pure)

```python
def requested(events) -> list[dict]
    # [{"id", "title", "expects": [str], "added": bool}] in first-declared order; latest wins

def expects_of(events, case_id) -> list[str]
    # the latest case_plan's expects for case_id; [] when none

def declared(events) -> bool

def expectation_states(expects: list[str], checks: list[dict]) -> list[dict]
    # per E (1-based): {"id": "E1", "text", "state": "verified"|"failed"|"unverified",
    #                   "by": [check what…]}
    # failed if any linked check ok=False; verified if ≥1 linked check ok=True and none failed;
    # unverified otherwise. Linked = "E{n}" in check.get("for", []).

def not_started(events) -> list[dict]
    # requested cases with no case_start and no not_run yet

def ledger(cases_built, events, finished: bool) -> dict
    # adds not_run rows (from not_run events) and, for a live run, "pending" rows for declared
    # cases not started; returns {"rows", "requested", "verified", "complete", "green"}
```

`verified = pass + fail`; `complete = every requested row is pass or fail`;
`green = every requested row is pass and no check in the run failed` (Q5, R6: a failed case-less
check, e.g. replay `--visual`, is not green). Undeclared runs: `requested = None`, and callers keep
today's behaviour (Q6).

## 4. Changes per module

### 4.1 `plan.py`

- `Case` gets `expects: list[str] = field(default_factory=list)`.
- New keyword `expect "<text>"`: only directly inside a `case`, before its first step, exactly one
  quoted argument (non-empty). In a `flow`, `if`, `retry`, or after a step → `PlanError` naming
  the line.
- After parsing, for each case with expects: collect `--for` IDs from `check` steps in the case's
  own body (recursing into `if`/`else`; `retry` cannot hold a check already, `plan.py:218`;
  **not** into flows). Errors:
  - `--for` on a check inside a `flow` → "a flow's checks are shared; link expected results in the case".
  - `--for E9` where the case has fewer expects, or on a case with none → "case TC-04 has no E9".
  - an E with no linked check → "case TC-04: E2 'Card ending…' has no `check --for E2`".
- `--for` itself is validated by the existing `_check_args` (it parses through `build_parser()`,
  `plan.py:286-296`) once it is added to the `check` parser with a `type=` that accepts only
  `E<n>[,E<n>]…`.
- Plan lines take no trailing `#` comments (`shlex.split` without `comments=True`); docs and
  examples must not show them (R5). The parser is not changed: `#12` targets depend on it.
- Docstring at the top of `plan.py` gains the `expect` / `--for` lines (it is the plan format spec).

### 4.2 `cli.py`

- `check` parser: `--for` (`E<n>[,E<n>]…`, parsed into a list). At runtime with an active case:
  if the current case's latest `case_plan` lacks that E → `UserError` (exit 2) and **no check is
  recorded**. Without an active run/case the flag is an error too. The event gets `"for": [...]`.
- `case` gets a third sub-command `plan <id> [title] [--expect TEXT]…`: needs an active run; logs
  `case_plan`; prints `TC-02 planned: E1 …; E2 …`. Allowed while another case is in progress (it
  does not end it).
- `case start <id>`: if the run is declared and `<id>` is not requested → log `case_plan`
  (`added: true`, no expects) before `case_start`. Title shown in the report: the `case_start`
  title, else the latest `case_plan` title (L5).
- `finish_run(quiet, reason=None)` (R3, M2): **before** the device-cleanup `try`, (1) close the
  current case as today (`case_end` blocked "run stopped before the case finished", plus
  `expects` states, L7), then (2) log one `not_run` per `coverage.not_started(events)`, reason =
  `reason` or `meta.stop_reason` or `"run stopped with tnazar stop before this case started"`.
  Neither touches the device, so an adb (or any) error in cleanup cannot skip them.
  `cmd_start` closing an earlier run passes `reason="run closed by a new tnazar start"` (L6).

### 4.3 `judge._end_case`

After computing `checks`, `failed`, `crash` as today:

```python
expects = coverage.expects_of(events, case)   # [] when none; `events` read once, reused
states = coverage.expectation_states(expects, checks)
failed_e = [s for s in states if s["state"] == "failed"]
unverified = [s for s in states if s["state"] == "unverified"]
```

- `status is None`: `fail` if `failed or crash` (unchanged; a failed linked check is already in
  `failed`), else `blocked` if `unverified`, else today's rule.
- Whenever `unverified` is non-empty and the case did not fail, `not verified: E2 <text>; E3 …`
  is **appended** to the reason, even when a reason was passed in (R4; a failed case already
  says why): `cmd_case start` auto-ends with "next case started
  without an explicit result", which today would hide it (`judge.py:30`).
- `status == "pass"` with `unverified` (and no failure) → `blocked`,
  `"marked pass by the tester; not verified: …"`.
- Explicit `fail`/`blocked`/`skipped` from the tester: unchanged.
- The `case_end` event gets `"expects": states` on every path through `_end_case`, including
  explicit tester results, and on `finish_run`'s direct close (L7). The report reads states from
  `case_end`; it never recomputes. Evidence capture for blocked: unchanged (already captures).

Cases with no expects take exactly today's path (characterisation test PH0-3).

### 4.4 `runner.py`

- After `the_run` is known: log `case_plan` (title, expects) for every **selected** case. Set
  `selection` meta only in the branch that creates the run (`runner.py:73-80`), never on a
  `--from` continuation (M5). Re-logging on a continuation is harmless (latest wins, order kept).
- Exception path (F1): `the_run.update_meta(stop_reason=f"run stopped at {case.id}: error: {exc}")`
  before `_stop()`.
- `--stop-on-fail` (F2): `update_meta(stop_reason=f"--stop-on-fail after {case.id}")` before `break`.
- Dashboard hand-back (F3): unchanged (run stays open). If the agent later runs `stop`, the
  default reason applies.
- Exit code: `failed` already becomes true for any non-pass case; a blocked-by-unverified case is
  non-pass, so `run` exits 1. No new code needed beyond the above; a test pins it.

### 4.5 `report.py`

- `ICON` gains `"not_run": "○"` and `"pending": "…"`; `build_cases` turns `not_run` events into
  rows (`status "not_run"`, reason, no events) in requested order; a real attempt wins.
- `summarize` adds `requested`, `verified`, `declared`, `complete`, `green`, `selection` from
  `coverage.ledger`.
- `summary_line` (R1, H1–H2): **if not declared → today's exact string**. If declared, today's
  string with additions only: N = requested cases; `○ k not run` and, live, `⏳ k pending` among
  the counts (zero counts omitted, as today); `  | verified V/N` appended:
  `20 case(s): ✅ 16 pass  ❌ 2 fail  ⛔ 1 blocked  ○ 1 not run  | checks 40/43 passed  | verified 18/20`.
  It still contains `case(s)`, so `runner._stop()`'s filter (`runner.py:119`) keeps printing it;
  a test pins that.
- Markdown + HTML: for declared runs a `Result: all passed | failed | incomplete` line, the
  selection line, not-run rows greyed, per-case "Expected results" list (✅/❌/○ + linked check
  text). Undeclared: one line `requested cases not declared: completeness unknown`.
  `incomplete` wins over `failed` when any row is blocked/not run/pending/skipped.

### 4.6 `replay.py`

- Before the first source event: copy every source `case_plan` into the target. In the main
  loop the originals are skipped (they are in `NARRATION_EVENTS`, §2), so step numbering and
  timing are unchanged (H3).
- `_recheck`: the re-check event copies `for` from the source check. A DB/Figma/manual check still
  becomes the existing note `[db] not re-verified on replay: …` (unchanged) and links nothing, so
  its E is unverified and `_end_case` blocks with `not verified: E3 …` (Q11). The note beside it
  says why; no extra plumbing (R7).
- In the `finally`, before `rpt.write(target)` (`replay.py:243-244`): log `not_run` for requested
  cases never started, reason `not run in the recorded run` (M4); the exit `failed` count adds
  `not_run`.

### 4.7 `ui.py` / `ui.html`

- `ui.py`: no change (it already sends `summary_line` and the events).
- The case-end row needs no change for expected results: an unproven one is named in the
  reason it already shows (UI-3).
- `ui.html` (pure functions, node-tested):
  - `layout()`: `case_plan` skipped (M1); `not_run` closes any open `say` group and becomes its own
    block kind `not_run` (L9), so it never lands under a `say`.
  - `describe()`: `not_run` → `○`, `TC-07 not run — <reason>`; the row renderer gives the
    `not_run` block the muted style.


### 4.8 `commands.py`

`NARRATION_EVENTS` gains `"case_plan"` and `"not_run"` (H3).

### 4.9 Docs and agent prompt

| File | Change |
|---|---|
| `CLAUDE.md` (tester prompt) | Step 1 expected results per case; step 3 `expect` + `--for`; hand-driven `case plan`; step 5 quote `verified N/M` |
| `.claude/skills/test-feature/SKILL.md` | same |
| `ARCHITECTURE.md` §10 events, §11 results, §15.1 plans, acceptance list DA-14 | new events, statuses, rules |
| `README.md`, `GLOSSARY.md`, site docs page for plans | syntax and words |
| `CHANGELOG.md` | v4 entry |

## 5. Backward compatibility (the "only adds" proof)

| Surface | Guarantee | Pinned by |
|---|---|---|
| Summary line, undeclared | identical string | PH0-1 |
| Report rows/status, undeclared | identical | PH0-2 |
| Case result without expects | identical rule incl. tester overrides | PH0-3 |
| `run` exit codes 0/1/2/3 | same meanings | PH0-4 |
| Old plan files | parse to the same `Plan` (expects empty) | PH0-5 |
| Old runs on disk | report and replay without error | PH0-6 |
| Dashboard on old runs | same rows | existing ui tests + PH0-7 |

Phase 0 tests are written and green **before** any change.

## 6. Edge cases (decided)

| Case | Behaviour |
|---|---|
| `case start` title differs from `case plan` title | report uses the `case_start` title, else the `case_plan` one |
| `tnazar start` while a declared run is open | it is closed with not-run reason "run closed by a new tnazar start" |
| Failed check outside any case (replay `--visual`) | run is not green (`Result: failed`) |
| Same case run twice in one run | latest attempt is the row (existing); expectations from that attempt only |
| `case plan` after the case already ended | updates title/expects for the report; result not recomputed (documented) |
| `check --for` with no active case | `UserError`, nothing recorded |
| `--for E1,E1` | de-duplicated |
| A linked check passes, another linked check for the same E fails | E failed → case fail |
| Crash detected and all E verified | fail (crash rule unchanged) |
| Runner killed (SIGKILL) | run stays active as today; the next `stop` writes `not_run` with the default reason |
| `--only` with an unknown ID | existing `PlanError`, nothing logged |
| Plan with 0 selected cases | impossible today (`_select` errors) |
| Non-ASCII `expect` text (₹, Hindi) | stored as UTF-8 (`ensure_ascii=False` already) |
| Very long expect text | report truncates display to 120 chars; full text in Markdown |

## 7. Performance

All new work is list scans of the run's events at case end and report time. `_end_case` already
reads `run.events()`; reuse that list (one read, not two). No device calls added. Replay adds no
step and no sleep for the new events (H3; test RPL-6).

## 8. Security and privacy

`expect` text and `case plan` titles are user-written test descriptions, stored like titles today.
The agent prompt keeps the existing rule: no secrets or customer data in titles or notes.

## 9. Rollout

Branch `feature/coverage`, stacked on `feature/site-trust` (v4 work not yet on `main`). Order: Phase 0 → coverage.py → plan.py → cli/judge →
runner → report → replay → ui.html → docs → smoke. One commit per step; tests green at each.

## 10. Estimate

| Step | Days |
|---|---|
| Phase 0 characterisation tests | 0.5 |
| `coverage.py` + unit tests | 0.5 |
| `plan.py` `expect` / `--for` validation | 0.5 |
| `cli.py` (`case plan`, `check --for`, `finish_run`) + `judge` | 1 |
| `runner.py` reasons + selection | 0.25 |
| `report.py` summary + HTML/MD | 0.75 |
| `replay.py` + `NARRATION_EVENTS` | 0.5 |
| `ui.html` + DA-14 + smoke step | 0.5 |
| Docs, agent prompt, code review fixes, device smoke | 0.5 |
| **Total** | **5** (the PRD's appetite; if it runs over, the HTML expected-results list moves to a follow-up and Markdown keeps it) |

## 11. Step 1 completion (v0.4; PRD §11 S1–S11)

| Piece | Where | Shape |
|---|---|---|
| Import | `teesri_nazar/intake.py` (pure: `read(text, columns, report) -> [Imported]`, `to_plan(...) -> str`), `cli.cmd_cases` | Format from the first lines: Markdown (with or without outer pipes; `\|` and any `<br>` handled), TSV (a tab in the first line) or CSV; BOM and `\r` handled. TSV is read by its own splitter: a cell is quoted only when it starts with `"` and its closing quote comes right before a tab or the line end (any other quote is text). A chat paste drops quotes, so while a row is still short a line with no tab continues its last cell, and the tabbed line ending that cell completes the row (it stays open while still short); a complete row is never joined, and a quoted cell holding a tab and a line break is reported as a join. Header synonyms in `FIELDS`, or `--columns`; two headers meaning one field are refused; cells beyond the header kept as `column N`. Heading row = only its first cell filled in a table of 3+ columns, unless it sits under an ID column and looks like an ID (one word with a digit: then it is a case). IDs one safe word, else `TC-NN` skipping used IDs; duplicates name both rows. Secret columns found by the words of their header (camel case split); only the value is masked, in every copied cell, titles included: a number after OTP/PIN/MPIN/… (`OTP 1234`, `OTP code 12 34`, `PIN is 0852`) or a word after `:`/`=` (`password: hunter2`); `pin code 560001`, `OTP-based`, `Password is required` stay as written. Expected results split only on line breaks, bullets or `;`. Every decision the reader made alone is in `report` (`headings`, `joined`, `short`, `not_copied`) and printed by the CLI. The draft is parsed before it is written; an existing plan is never replaced without `--force`. |
| Draft parsing | `plan.parse(..., draft=True)`, `Plan.gaps` | Completeness problems become gaps (all of them, in parse order); syntax errors still raise. Strict mode raises the first gap with the same text as before. |
| Review | `plan.review(plan) -> (text, ready)`, `tnazar plan review` | Per case: `E<n> text  <- line N: check …` or `NOT PROVEN`; then gaps. Exit 0 ready, 1 gaps, 2 syntax. |
| Dead preconditions | `runner.run`, `Failed.flows`, `Executor.completed`, `_flows_used`, `_block` | Every `run X` a failure passes through adds X to `Failed.flows`, so a failure counts for nested flows too. `_flows_used` lists the flows a case always runs (nested, not those only in an `if` branch). A flow failing in a case is appended to `broken[flow]`; only a case where it ran to its end clears it. A case needing a flow with ≥ 2 failures is logged as `case_start` + blocked `case_end` (with `expects` states) directly: no CLI, no device. |
| Full text | `screen.Element.describe(full)`, `env.describe(..., full)`, `observe --full` | Whole text, line breaks kept and indented, empty lines dropped, secrets masked as before. |

Known limitation found while testing (not changed here): a replay run records checks but not
its taps or typing, so replaying a replay has nothing to repeat and its UI cases fail (never
pass). Coverage survives it: the requested list and not-run rows carry over.

