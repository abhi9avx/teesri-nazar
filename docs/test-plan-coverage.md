# Test plan — Coverage ledger

| | |
|---|---|
| Status | **Draft v0.2**, 2026-10-21: review findings folded in. Not implemented. |
| PRD / TRD | [prd-coverage.md](prd-coverage.md) · [trd-coverage.md](trd-coverage.md) (section refs below are TRD sections) |
| Branch | `feature/coverage` |
| Rule | **Test first** (`tdd`): Phase 0 green on today's code, then one failing test → the code that makes it pass, one behaviour at a time. |

---

## 1. Strategy

| Level | Proves | Where | When |
|---|---|---|---|
| **L1 Pure** | `coverage.py` rules, plan parsing, `summary_line`, ui.html `describe`/`layout` | `tests/test_coverage.py`, `tests/test_plan.py`, `tests/test_report.py`, node tests in `tests/test_fix_ui.py` | every commit, CI (Python 3.9) |
| **L2 CLI in-process** | `case plan`, `check --for`, `case end`, `stop`, `run`, `replay` through `cli.main` on `FakeDevice` | `tests/test_coverage.py`, `tests/test_plan.py`, `tests/test_replay.py` | every commit, CI |
| **L4 Device smoke** | DA-14 live: grey not-run rows in the real dashboard; a real plan stopped early | `scripts/smoke_dashboard.py` + manual §6 | before any push |

**Seams:** event lists in, dicts out for `coverage.py` (no mocks). CLI through `tnazar()` from
`tests/fakedevice.py`. Assertions use stable tokens (`not run`, `verified 5/8`, `E2`, status
names, exit codes), never full sentences, except the Phase 0 golden strings.

**Python 3.9:** `/usr/bin/python3 -m unittest discover -s tests`.

## 2. Fixtures

| Fixture | Content |
|---|---|
| Inline plan text written to a temp dir (app = `fakedevice.PACKAGE`) | `ten_cases` (TC-01…TC-10; TC-03 starts with an `if` step, made to raise by `mock.patch.object(runner.Executor, "visible", side_effect=AdbError(...))`, as in `test_review_fixes.py:68-78`), `expects` (E1/E2 linked, one in an `if`), one bad plan per PLAN error |
| Event lists built in tests | small helpers `ev_plan(id, expects)`, `ev_start(id)`, `ev_check(ok, for_)`, `ev_end(id, status)` |
| `tests/fixtures/runs/legacy/` | an events.jsonl + run.json copied from a real pre-change Settings run (no app content) for PH0-6 |

## 3. Phase 0 — characterisation (green on today's code, stay green)

| ID | Proves |
|---|---|
| PH0-1 | `summary_line` for undeclared runs: exact strings for 0 cases, all pass, mixed (the existing `test_cli.py:619` assertion + one with skipped/blocked) |
| PH0-1b | `tnazar run` output still contains today's `N case(s): ✅ …  \| checks x/y passed` substring (the existing `test_plan.py:193` assertion, kept **unchanged**) |
| PH0-2 | `build_cases` rows/status/reason for an undeclared mixed run |
| PH0-3 | `_end_case` without expects: none→blocked "no checks", failed check→fail, crash→fail, tester `pass` with failed check→fail, tester `skipped` kept |
| PH0-4 | `run` exit codes 0/1/2/3: already pinned by `test_plan.py` and `test_review_fixes.py:68`; referenced, not duplicated |
| PH0-5 | every `examples/*.plan` (tracked, app-agnostic) parses to the same cases, expects `[]`. `plans/` is gitignored and local, so it is not used |
| PH0-6 | the legacy run reports and replays (FakeDevice) without error and with today's rows |
| PH0-7 | ui.html `layout` output for a legacy event list unchanged (node) |

## 4. L1 / L2 cases

### COV — `coverage.py` (TRD §3)

| ID | Given → expect |
|---|---|
| COV-1 | no `case_plan` → `declared False`, `requested []` |
| COV-2 | plan TC-01, TC-02, re-plan TC-01 new title → order [TC-01, TC-02], TC-01 new title |
| COV-3 | E1 linked check pass → verified, `by` names it |
| COV-4 | E1 one pass + one fail → failed |
| COV-5 | E2 no linked check → unverified |
| COV-6 | check `for` E1,E1 → counted once |
| COV-7 | `not_started`: declared 3, started 1 → 2; with a `not_run` already logged → excluded |
| COV-8 | ledger finished: rows in requested order, `verified` = pass+fail, `complete`/`green` per Q5 |
| COV-9 | ledger live: unstarted declared → `pending`, not `not_run` |
| COV-10 | a real attempt and a `not_run` for the same ID → the attempt wins |

### PLAN — `expect` / `--for` (TRD §4.1)

| ID | Given → expect |
|---|---|
| PLAN-1 | `expects.plan` parses; `Case.expects` = two texts |
| PLAN-2 | `expect` after a step → PlanError with the line |
| PLAN-3 | `expect` inside `flow` / `if` / `retry` → PlanError |
| PLAN-4 | E2 with no linked check → PlanError naming E2 and its text |
| PLAN-5 | `--for E9` → PlanError "has no E9"; `--for` on a case without expects → PlanError |
| PLAN-6 | `check --for` inside a flow → PlanError |
| PLAN-7 | linked check inside `if`/`else`/`retry` counts for the static rule |
| PLAN-8 | `--for` malformed (`X1`, empty) → PlanError via `_check_args` |
| PLAN-9 | `expect ""` and `expect` with two args → PlanError |

### CLI — hand-driven (TRD §4.2, §4.3)

| ID | Given → expect |
|---|---|
| CLI-1 | `case plan TC-02 "t" --expect a --expect b` → exit 0, prints E1/E2, logs `case_plan` |
| CLI-2 | `case plan` with no active run → exit 2 |
| CLI-3 | `check … --for E3` where TC-02 has 2 E → exit 2, **no** check event |
| CLI-4 | `check --for` with no current case → exit 2 |
| CLI-5 | `check --result pass --kind db --for E1` → event has `for` |
| CLI-6 | declared run, `case start TC-09` (undeclared) → `case_plan` with `added: true`, then `case_start` |
| CLI-7 | `case plan` during another case → that case stays open |
| CLI-8 | `case end` auto: E2 unverified, rest pass → blocked, reason has `E2` + text |
| CLI-9 | `case end pass` with E2 unverified → blocked "marked pass by the tester; not verified" |
| CLI-10 | linked check fail → fail; crash + all E verified → fail |
| CLI-11 | declare 8, run 5, `stop` → 3 `not_run`, reason "run stopped with tnazar stop…"; summary `8 case(s): … ○ 3 not run … | verified 5/8` |
| CLI-12 | `stop` when adb cleanup raises → the open case still ends `blocked` and `not_run` is still written |
| CLI-13 | auto-end by the next `case start` with E2 unverified → reason has both "next case started…" and `not verified: E2` |
| CLI-14 | `case plan "t"` then `case start TC-02` with no title → report shows the plan title |
| CLI-15 | `start` while a declared run is open → its not-run reason is "run closed by a new tnazar start" |

### RUN — plan runner (TRD §4.4)

| ID | Given → expect |
|---|---|
| RUN-1 (F1) | `ten_cases.plan`, TC-03 raises → report 10 rows: TC-01/02 results, TC-03 blocked, TC-04…10 not run "run stopped at TC-03: error…"; exit 2 |
| RUN-2 (F2) | `--stop-on-fail`, TC-02 fails → 8 not run "--stop-on-fail after TC-02"; exit 1 |
| RUN-3 (F3) | message at TC-04 → exit 3, run open, no `not_run` yet; then `stop` → TC-05…10 not run, default reason |
| RUN-4 | F3 then `run --from TC-04` → all 10 rows real, no `not_run`; `case_plan` order unchanged |
| RUN-5 | `--only TC-05` → requested 1, header `1 of 10 plan cases selected (--only TC-05)`, exit 0 when it passes |
| RUN-6 | `expects.plan`, `if` branch skips E2's check → case blocked naming E2; `run` exits 1 |
| RUN-7 | all pass with all E verified → exit 0, `Result: all passed` |
| RUN-8 | `run` prints the declared summary line (it survives `_stop()`'s filter) with `verified V/N` |
| RUN-9 | `--from` continuation keeps `selection` from the first invocation; header says `10 of 10` |

### REP — report (TRD §4.5)

| ID | Given → expect |
|---|---|
| REP-1 | declared summary line = today's line + additions only; zero counts omitted; live run shows `⏳ k pending` |
| REP-7 | all cases pass but a case-less check failed → not green (`Result: failed`) |
| REP-2 | Markdown: `Result: incomplete` when any not run/blocked/skipped; `failed` when only fails; `all passed` |
| REP-3 | per-case expected results with ✅/❌/○ and the linked check text |
| REP-4 | undeclared report has exactly one extra line "completeness unknown", nothing else differs (diff against PH0-2 output) |
| REP-5 | HTML escapes `expect` text (`<script>`) |
| REP-6 | `runs` and `report` commands print the new line for declared runs |

### RPL — replay (TRD §4.6)

| ID | Given → expect |
|---|---|
| RPL-1 | source `case_plan` copied; replay summary is declared with the same requested list |
| RPL-2 | E3 proven only by a db check → replay case blocked, reason `not verified: E3`, with the existing `[db] not re-verified on replay` note |
| RPL-3 | E1 proven by a UI check that passes again → verified |
| RPL-4 | source had TC-07 not run → replay row not run "not run in the recorded run"; exit 1 |
| RPL-5 | source without expects (legacy) → replay identical to today (PH0-6) |
| RPL-6 | declared source → same step numbering as the undeclared equivalent, and no extra 0.3 s settle per `case_plan`/`not_run` (count `time.sleep` calls) |
| RPL-7 | a step raises mid-replay → `not_run` still written in `finally` |

### UI — dashboard (TRD §4.7, DA-14)

| ID | Given → expect |
|---|---|
| UI-1 | `describe({type:"not_run"…})` → "TC-07 not run — reason" (node) |
| UI-2 | `layout` gives `not_run` its own block kind, never inside a `say` group, in requested order |
| UI-5 | `layout` skips `case_plan` (no `• case_plan` rows) |
| UI-3 | `case_end` with `expects` states → row mentions `E2 not verified` |
| UI-4 | `/api/state` summary for a declared live run shows `requested` line |

## 5. Acceptance mapping

ARCHITECTURE.md acceptance list gets **DA-14** → `tests/test_acceptance.py`:
`DA-14: [test_not_run_cases_are_rows_with_their_reason, test_layout_gives_not_run_its_own_block,
test_layout_skips_case_plan, test_stop_writes_not_run_for_declared_cases]`.

## 6. L4 — device smoke (before any push)

1. `scripts/smoke_dashboard.py --device <serial>` with a new DA-14 step: declare 2 Settings cases,
   run one, `stop`; read the dashboard timeline → a grey `not run` row for the second, with the
   default reason.
2. Manual on the emulator: `./tnazar run examples/settings.plan --stop-on-fail` with a case edited
   to fail → HTML report shows not-run rows and `Result: incomplete`; screenshot attached to the PR.
3. Manual: a plan with `expect`/`--for` on Settings (`E1 "Internet" shown`) passes; flip the
   check into an untaken `if` → blocked naming E1.

## 7. Exit criteria (before any push or PR)

- Phase 0 green before the first change and after the last.
- All IDs above implemented and green on `/usr/bin/python3` (3.9).
- `ai-code-review` against `main`: HIGH/MED fixed, LOW listed.
- §6 smoke done on a device; what was seen is reported, not inferred.
