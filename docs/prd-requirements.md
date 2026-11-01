# PRD — Requirements traceability: "every case passed" never reads as "the feature is tested"

| | |
|---|---|
| Status | **Grilled 2026-11-01** (Q1–Q10), then revised for an external review (R1–R5, §10). Frozen; **built** on `feature/requirements` (2026-11-01). |
| Branch | `feature/requirements` (off `main` once `feature/judgment` merges) |
| Builds on | [prd-coverage.md](prd-coverage.md) (case and expectation ledger), `feature/judgment` (verdicts and causes) |
| Code | `teesri_nazar/plan.py`, `coverage.py`, `runner.py`, `report.py`, `intake.py`, `cli.py` (`plan review`, new `plan approve`) |

---

## 1. Context gate

- **Strategy / ICP** [RESOLVED FROM WORKSPACE: product/strategy.md (Last updated: 2026-10-19)]:
  QA engineers and SDETs on 10–200 person Android teams. Focus **Engagement**: outside people
  finish real runs *and trust the result*. Trust feature; no contradiction.
- **Evidence: [STRATEGY-DRIVEN ONLY - NO COMPELLING EVIDENCE]** from users (none outside yet).
  The gap itself is a **[FACT, code]**:
  - F1 `coverage.py` knows cases and expected results only. Nothing above a case exists.
  - F2 `plan.review()` prints "ready to run" for any set of cases whose `expect` lines are
    proven. Six cases that never test duplicate transfers are "ready".
  - F3 `coverage.tally()` is `green` when every requested case passed: a run of an incomplete
    case set reports `all passed`.
  - F4 The only record of what the feature *should* do is the PRD in chat. The agent that
    writes the cases is the same agent that decides what to leave out, and nothing checks it.
- **Stage / appetite:** pre-launch, one part-time builder. **Shape Up, 5 working days**, scope
  flexes. Rules kept: standard library only, Python 3.9, append-only `events.jsonl`, exit-code
  meanings unchanged (ARCHITECTURE.md §11).

## 2. Problem

Asked to test money transfer, the agent writes six cases; all six pass; the report says
`6 case(s): ✅ 6 pass | verified 6/6`, Result: all passed. The PRD also said "a second submit
within 60 s must not create a second transfer". No case tests it, and no line of the report
says so. The ledger answers "did the planned cases run and prove what they said?", never
"did the plan cover what was asked?". A green report over an incomplete plan is a false green
one level up — the worst bug for a tool whose pitch is "hand off your regression".

## 3. The model

```
Source (PRD / story / AC list, by reference)
  └─ Requirement R1, R2 …   (proposed by Claude, approved by the user, fingerprinted)
       └─ Expected result TC-03 E2   (`expect "…" --req R2`)
            └─ Check (`check … --for E2`) → evidence (screenshot, db detail)
```

Claude proposes; the **user approves the requirement list**; from then on, every number in the
report comes from the deterministic engine (`coverage.py`), never from Claude's summary.

**Honest boundary:** the engine proves every *approved* requirement has evidence, or names the
ones that do not. It cannot prove the approved list is complete against the PRD: that is the
human approval in step 3 below, made cheap by putting each requirement's source line next to it.

## 4. Solution (user-visible)

### 4.1 Plan syntax (all optional; top of the plan, like `app`)

```
app com.example.bank
source "Transfer PRD v3 (confluence …/12345), 2026-10-30"
requirement R1 "Valid amount transfers and shows a reference number" --source "§2.1"
requirement R2 "Second submit within 60 s creates no second transfer" --source "§2.4"
requirement R5 "Daily limit resets at midnight IST" --source "§3.2" --out-of-scope "needs a 24 h clock change"
approved 3f9a01c2b7de      # written by `tnazar plan approve`, never by hand

case TC-03 "Double tap on Pay"
  expect "Only one transfer row in history" --req R2
  ...
  check "1 transfer" --for E1
```

- `--req` goes on an **`expect` line**, not on a case: a requirement is proven by a specific
  expected result, so a case that covers R1 and R2 cannot pass R2 by proving only R1.
  An expected result may name several (`--req R1 --req R2`); a requirement may be linked from
  several cases.
- Unknown `--req R9`, a requirement defined twice, or `requirement` below a `case` → plan error
  naming the line (same as every other syntax error).

### 4.2 Requirement states (pure, in `coverage.py`)

Per link (one expected result): **verified** only when the expected result is verified **and its
case passed**; **failed** when the expected result failed; otherwise **not verified** with the
case's cause (blocked, not run, not selected, failed elsewhere).

| Requirement | Rule |
|---|---|
| ✅ verified | it has links and **every** link is verified |
| ❌ failed | any link failed |
| ◐ not verified | has links, none failed, at least one not verified (names which and why) |
| ○ not covered | no `expect` links to it — **the omission this PRD exists for** |
| ⊘ out of scope | `--out-of-scope "<why>"`, shown with the reason; never verified |

"Every link" (not "any") is deliberate: a requirement proven in TC-03 but not run in TC-07
is not proven for this run. Strict means a requirement can read *less* green, never falsely green.

### 4.3 Plan review and approval

- `tnazar plan review` prints, **before** the cases, every requirement with its source and the
  expected results that link to it, then the fingerprint. New gaps (draft mode lists all):
  `R2 "…" has no expected result: link one with --req R2, or mark it --out-of-scope "<why>"`, and
  `requirements not approved` / `requirements changed since approval (approved X, now Y)`.
  Plans with no `requirement` line print exactly what they print today.
- `tnazar plan approve plans/x.plan` writes or replaces the `approved <fingerprint>` line.
  Fingerprint = first 12 hex of SHA-256 over, in order: the `source` line, then per requirement
  its id, text, `--source`, out-of-scope reason, and every link to it as (case id, expected-result
  text). Changing an approved requirement's text, scope, source **or which expected results prove
  it** changes the fingerprint and needs re-approval. Reordering cases or editing steps does not.
- This is a workflow safeguard that makes a silent change visible, **not** cryptographic proof
  that a person approved anything: whoever can edit the file can run `plan approve`. The real
  approval is the user's yes in chat; the agent runs `plan approve` only after it (CLAUDE.md /
  `test-feature` rule, §4.7), and the report never calls the scope "user-approved", only
  "approved fingerprint matches".

### 4.4 Running: a run-scoped scope snapshot

`tnazar run` does **not** refuse a plan with a not-covered requirement or an unapproved scope.
It prints them at start and the report carries them. Refusing would push the agent to delete the
requirement to get the run going — the exact gaming the fingerprint exists to catch.

Today `runner.py` logs `case_plan` only for the **selected** cases (`runner.py:90`), so a report
of `run --only TC-01` cannot know that TC-05 also proves R1. Therefore:

- At the start of every `tnazar run` call on a plan that declares requirements, the runner logs
  one **`scope`** event: `source`, fingerprint, approved fingerprint, every requirement (id, text,
  source, out-of-scope reason), **every** case's links (case id → expected result → requirement
  ids, selected or not) and the selected case ids.
- `case_plan` events and their meaning stay exactly as today (selected cases only). Case results,
  `verified V/N` and the case Result word are computed from them, unchanged.
- Requirement states are computed from the **latest** `scope` event plus the case evidence in
  the run. A link to a case that was never selected in this run is `not verified (TC-05 not
  selected)`; one that was selected but never started is `not verified (TC-05 not run)`.
- Continuations of the same run (`run --from` / `--only` after exit 3) log a fresh `scope`; the
  selected ids accumulate across them. If the fingerprint differs between two `scope` events of
  one run, the Requirements line says `scope changed mid-run` and can never be all verified.
- Plans without requirements log no `scope` event: their event log is byte-identical to today.

### 4.5 Summary, report and exit code

- Summary line: today's line unchanged as the prefix, plus `  | requirements V/N verified` only
  when requirements were declared. Example for the acceptance fixture:
  `6 case(s): ✅ 6 pass  | checks 9/9 passed  | verified 6/6  | requirements 4/5 verified`
- Report, under today's **Result** line, a **Requirements** line:
  - `all requirements verified` when every requirement is verified, none excluded, and the
    approved fingerprint matches;
  - `all in-scope requirements verified; 1 excluded (R5: <why>)` when the rest hold and some
    are out of scope; the summary shows `requirements 4/5 verified (1 excluded)`;
  - `incomplete — R2 not covered; R4 not verified (TC-05 not selected)` otherwise, naming each,
    including `scope not approved`, `approved fingerprint does not match` and `scope changed
    mid-run`;
  - `no in-scope requirement: feature verification not claimed` when every requirement is
    excluded. That is never a success.
- A Requirements table: id, text, source, state, proven by (`TC-03 E1 ← check "1 transfer"`), or
  why not.
- Plans without requirements: one line, `requirements not declared: feature completeness not
  claimed` — the same honesty as today's "requested cases not declared".
- **Exit code (revised Q8):** a plan that declares requirements exits **1** unless the
  Requirements line is `all requirements verified` or `all in-scope requirements verified`. A
  failed, not-covered, not-verified, unapproved or changed requirement never exits 0, so a CI
  gate that reads only the exit code cannot read an incomplete feature as success. No flag turns
  it off. Exit 2 / 3 / 5 keep precedence and meaning; no new exit code. **Plans without
  requirements keep today's exit codes exactly.**
- Consequence, intended: `run --only TC-05` on a requirements plan exits 1 when it leaves other
  links unproven. The case results print as today; the Requirements line names what was not
  selected. To get 0, run the cases the requirements need.

### 4.6 Replay

`replay.py` copies only `case_plan` events into the new run (`replay.py:195`), and an event type
it does not know falls through to step execution. So:

- Replay copies the source run's latest `scope` event, unchanged, before the cases (same place
  as `case_plan`), and `scope` joins the events replay never executes as a step.
- Requirement states in the replay are computed from the replay's **own** new checks and case
  results against that copied scope. Nothing is copied from the source run's verdicts.
- A link whose evidence replay cannot re-collect (a recorded `check --result pass --kind db`,
  an `if` branch not taken, a case skipped because its setup did not hold) is `not verified`
  in the replay, never verified by inheritance.
- Old runs without a `scope` event replay exactly as today.

### 4.7 Agent behaviour (CLAUDE.md, `test-feature` skill)

When the input has a PRD, story or AC list: list requirements with `--source` first, show
`plan review`, wait for the user's yes, `plan approve`, then run. The chat report quotes the
`requirements V/N` part and lists every not-covered and not-verified requirement. Free-text asks
with no spec: no requirements; the report says completeness is not claimed.

## 5. Does it only add? (the no-degrade contract)

| Today | After |
|---|---|
| Plans with no `requirement` line | **Byte-identical** parse, `plan review`, event log, summary line, exit codes; report gains only the one "not declared" line |
| Case verdicts (`judge.py`), `case_plan` events | Unchanged. The requirements layer reads verdicts, never writes them |
| Old runs on disk | Report and replay as today (no `scope` event → not declared) |
| Hand-driven runs | Unchanged (requirements are plan-only in this batch, §7) |
| Run speed | ≥ 0.8× main on the bench plans (pure O(events) computation, no device calls) |

The one stricter behaviour — exit 1 on incomplete requirements — exists only for plans that opt
in by declaring requirements, which no plan does today.

## 6. Done when (TDD gate — tests written first, red, then green)

**Phase 0, characterisation (green on main before any change, must stay green):**
- C1 every `examples/*.plan` and `plans/*.plan`: `plan review` output and parsed `Plan` equal
  main's (golden files).
- C2 every run under `runs/`: `summary_line` and Markdown report equal main's except the one
  added "not declared" line.
- C3 the judgment re-judge of past case results: identical statuses and causes.
- C4 a legacy plan run on the fake device: event log (minus timestamps) and exit code equal main's.
- C5 replaying a legacy run: same events and exit code as main.

**Acceptance (fake device, `tests/test_requirements.py`, fixture `tests/fixtures/transfer-req.plan`):**
- A1 five requirements, six cases, R2 linked by nothing, all checks pass → summary ends
  `requirements 4/5 verified`; Requirements line `incomplete — R2 not covered`; **exit 1**; no
  line anywhere reads `all requirements verified`. **The headline test.**
- A2 same plan, R3's check fails → R3 failed, `3/5`, exit 1.
- A3 R4's only case ends `blocked · precondition not met` → R4 not verified, cause named.
- A4 R1 linked from TC-01 and TC-05, `run --only TC-01` → R1 `not verified (TC-05 not
  selected)`, exit 1; `case_plan` events list only TC-01 (as today); `scope` lists all six cases.
- A5 R5 out of scope with reason, rest verified, approved → `requirements 4/5 verified (1
  excluded)`, Requirements `all in-scope requirements verified; 1 excluded`, exit 0.
- A6 scope edited after `plan approve` → review gap names both fingerprints; report Requirements
  `incomplete`, exit 1.
- A7 `plan review` lists every requirement and the not-covered gap; legacy plans unchanged (C1).
- A8 `--req R9`, duplicate `requirement R1`, `requirement` after a `case` → plan error with line.
- A9 a case covering R1 and R2 proves only R1's expected result → R2 `not verified (TC-06 E2 not
  verified)`; R1 is not verified either, because its case did not pass (Q6). Never verified by
  the other requirement's evidence. (Corrected during build: the draft said R1 verified, which
  contradicts Q6.)
- A10 every requirement out of scope → `no in-scope requirement: feature verification not
  claimed`, exit 1. A legacy plan with all cases passing → exit 0, as today.
- A11 TC-03 proves R2's expected result, then crashes → R2 not verified, cause `TC-03 failed: crash`.
- A12 partial runs: `--from TC-04` → links in TC-01..03 `not selected`; `--stop-on-fail` after
  TC-02 fails → TC-03.. `not run`; exit-3 hand-back then `run --from TC-04` in the same run →
  selected ids accumulate and, when all evidence is in, `all requirements verified`, exit 0.
- A13 fingerprint: changing a requirement's `--source`, moving its `--req` to another expected
  result, or adding a second link → `approved fingerprint does not match`; reordering cases or
  editing a `tap` step → still matches.
- A14 replay of A5's passing run → copied `scope`, states recomputed from the replay's checks;
  with R1's check failing on replay → R1 failed in the replay while the source run still says
  verified; a link proven by a recorded `--kind db` check → `not verified` in the replay; the
  `scope` event is never executed as a step.
- A15 an approved plan whose `scope` events differ between a run and its continuation → `scope
  changed mid-run`, exit 1.

**Ship gate (CLAUDE.md "when is it done"):** Python 3.9 suite green; one `ai-code-review`
(HIGH/MED fixed); `smoke_dashboard.py` on a device; bench speed ≥ 0.8× main; A1 and A14 shown on
the emulator, not only the fake device.

## 7. Out of bounds (no-gos for this batch)

- Requirements in hand-driven runs (`case plan --req`, `tnazar requirement plan`). Plans first.
- Claude reading the PRD *inside* tnazar. tnazar stores references and ids, never fetches docs.
- Automatic completeness checking of the requirement list against the PRD (§3 honest boundary).
- `requirement` column in `cases import` (step 2, ~1 day, after A1–A15).
- Requirements database, Jira sync, dashboard requirements panel. The dashboard shows the
  extended summary line only.
- New exit codes.
- Next: a shared `features/<name>.req` file referenced by several plans, with the report adding
  up requirements across their runs. Needs cross-run aggregation; too big for this appetite.

## 8. Risks and rabbit holes

- **Ceremony kills adoption:** approval adds a round-trip. Mitigation: everything is optional;
  the agent only declares requirements when a spec was given.
- **Granularity drift:** "Transfers work" as one requirement verifies trivially. Mitigation: the
  agent rule asks for one requirement per testable statement with a `--source`; the user approves.
- **`--only` reruns exit 1 on requirements plans.** Intended (§4.5); the agent must not read that
  exit as a case failure: the Requirements line says "not selected", the case table says pass.
- **Appetite:** the scope snapshot and replay add about a day; the 5-day box holds by keeping the
  dashboard and import out. If it slips, the `cases import` column moves out first.

## 9. Success measure

- Guardrails, each blocks the release: **0** legacy diffs (C1–C5); **0** runs where a
  requirements plan exits 0 or reads all verified while a requirement lacks a verified link
  (A1–A15); the planted omission (A1) is detected on every run, fake device and emulator.
- Outcome: three real specifications are turned into approved requirements, linked to executable
  cases and reported with evidence the user agrees with (no requirement the user calls wrongly
  verified).
- Zero omissions found on real specs is **not** a reason to retire anything: it may mean the plan
  was complete. Retire the default workflow (agent declares requirements for every spec) only if
  user testing shows the approval step costs more than it gives [assumption, no baseline; revisit
  after 5 user interviews, see bets.md Now #1].

## 10. Decisions log

Grilling, 2026-11-01, all accepted; revised the same day after an external PRD review (R1–R5).

| # | Decision | Why |
|---|---|---|
| Q1 | `--req` on `expect`, not on `case` | A case covering R1 and R2 cannot pass R2 by proving only R1 |
| Q2 | Requirements live in the plan file this batch | One plan = one run = one claim; multi-plan `.req` files go to Next (§7) |
| Q3 | Not-covered requirement: `plan review` gap, `run` warns and reports | Refusing pushes the agent to delete the requirement to get running |
| Q4 | Approval fingerprint (`plan approve`) in v1; covers sources and links (R3) | Without it the list, or its evidence mapping, is whatever the agent left in the file |
| Q5 | Verified only when **every** link is verified | A link not run or not selected is not proof for this run |
| Q6 | A link counts only when its case passed | A crash after the check may be the very bug (double transfer) |
| Q7 | Out-of-scope requirements stay in N, shown as excluded (R2) | Nothing silently leaves the denominator; all-excluded claims nothing |
| Q8 | **Revised (R2):** requirements plans exit 1 unless all (in-scope) requirements verified; no flag; legacy plans unchanged | An exit-0 gate must not read an incomplete feature as success |
| Q9 | Plans only; hand-driven runs say "requirements not declared" | Halves the surface; real runs use plans |
| Q10 | The agent declares requirements only when given a spec (PRD, story, AC list, ticket) | No approval round-trip for one-line asks |
| R1 | Run-scoped `scope` snapshot of all requirements, links and selected cases; `case_plan` unchanged | `runner.py` records only selected cases; A4 needs the unselected links |
| R4 | Replay copies `scope`, recomputes from new evidence, never inherits verdicts | Replay is core; `replay.py` would otherwise drop or execute the new event |
| R5 | Success = real specs traced with trusted evidence; no "0 omissions → retire" rule | Zero omissions can mean a complete plan, not a useless feature |

Defaults set without a separate question (say if you disagree):
- `requirement` column in `cases import`: step 2 after A1–A15, ~1 day.
- The event is named `scope` (one per `run` call), not one `requirement_plan` per requirement.
